"""Discover and verify Workday tenants worth polling.

Workday has no global directory, so coverage is built from candidate tenant
slugs which this tool verifies against the live endpoints. For each seed it:

1. finds the tenant's host + career-site slug by probing
   https://{tenant}.{wdN}.myworkdayjobs.com/ across common hosts and reading
   the redirect to /en-US/{site};
2. confirms the CXS jobs endpoint answers with a JSON job list;
3. counts in-scope roles via searchText probes (data science / machine
   learning / AI) and samples locations for UK signals;
4. optionally cross-checks the company name against the Home Office
   register of licensed Skilled Worker sponsors (CSV);
5. emits a ready-to-paste `workday:` block for companies.yaml containing
   only tenants that pass, plus a per-seed report of everything rejected.

Usage:
    python tools/discover_workday.py --seeds tools/workday_seeds.txt \
        [--register path/to/sponsor_register.csv] [--require-uk] \
        [--out workday_companies.yaml]

Seeds file: one entry per line, "tenant_slug" or "tenant_slug, Display Name".
Lines starting with # are ignored. Run from a network that can reach
myworkdayjobs.com (the GitHub Actions runner qualifies).

Politeness: one tenant at a time, a pause between requests, and only
HEAD/GET/POST calls the public career site itself makes.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "ai-jobs-agent/1.0 (+https://github.com/wale-eth/ai-jobs-agent)"
# Most tenants live on a handful of data-centre hosts.
HOSTS = ["wd3", "wd1", "wd103", "wd5", "wd12", "wd10", "wd102", "wd2", "wd101"]
SCOPE_QUERIES = ["data scientist", "machine learning", "AI engineer"]
UK_HINTS = re.compile(
    r"united kingdom|london|manchester|edinburgh|glasgow|cambridge|oxford|"
    r"bristol|leeds|birmingham|belfast|cardiff|\buk\b",
    re.I,
)
PAUSE_S = 1.0

# --- sponsor-register matching (same normalization as apply/cvgen/register.py)
_DROP = {
    "ltd", "limited", "inc", "incorporated", "llc", "llp", "plc", "corp",
    "corporation", "co", "company", "group", "holdings", "international",
    "global", "uk", "gb", "usa", "the", "and",
}
_NONALNUM = re.compile(r"[^a-z0-9]+")


def _tokens(name: str) -> frozenset[str]:
    return frozenset(
        t for t in _NONALNUM.sub(" ", name.lower()).split() if t and t not in _DROP
    )


def load_register(path: Path) -> list[frozenset[str]]:
    entries = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = reader.fieldnames or []
        target = next(
            (c for c in cols if c.strip().lower() == "organisation name"),
            cols[0] if cols else None,
        )
        for row in reader:
            toks = _tokens(row.get(target, "") if target else "")
            if toks:
                entries.append(toks)
    return entries


def register_match(name: str, register: list[frozenset[str]]) -> bool:
    toks = _tokens(name)
    return bool(toks) and any(
        toks <= entry or entry <= toks for entry in register
    )


# --- HTTP helpers (stdlib only)
def _get(url: str, timeout: int = 20) -> tuple[int, str, str]:
    """Return (status, final_url, body_head)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.geturl(), response.read(2048).decode(
            "utf-8", "replace"
        )


def _post_json(url: str, body: dict, timeout: int = 20) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


# --- probing
def find_site(tenant: str) -> tuple[str, str] | None:
    """Return (host, site) for a tenant by following the root redirect."""
    for host in HOSTS:
        root = f"https://{tenant}.{host}.myworkdayjobs.com/"
        try:
            status, final_url, _ = _get(root)
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if status != 200:
            continue
        # final URL looks like https://tenant.wd3.myworkdayjobs.com/en-US/SiteName
        match = re.search(r"/en-[A-Za-z]{2}/([^/?#]+)", final_url)
        if match:
            return host, match.group(1)
    return None


def probe_jobs(tenant: str, host: str, site: str) -> dict | None:
    url = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    try:
        page = _post_json(
            url, {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
        )
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(page, dict) or "jobPostings" not in page:
        return None
    return page


def scope_counts(tenant: str, host: str, site: str) -> dict[str, int]:
    url = f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    counts = {}
    for query in SCOPE_QUERIES:
        try:
            page = _post_json(
                url,
                {"appliedFacets": {}, "limit": 20, "offset": 0,
                 "searchText": query},
            )
            counts[query] = int(page.get("total", 0))
        except Exception:  # noqa: BLE001
            counts[query] = -1
        time.sleep(PAUSE_S)
    return counts


def uk_signal(page: dict) -> bool:
    sample = " ".join(
        (item.get("locationsText") or "") for item in page.get("jobPostings", [])
    )
    return bool(UK_HINTS.search(sample))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", required=True, type=Path)
    ap.add_argument("--register", type=Path,
                    help="Home Office sponsor register CSV (optional)")
    ap.add_argument("--require-uk", action="store_true",
                    help="drop tenants whose first job page shows no UK locations")
    ap.add_argument("--out", type=Path, default=Path("workday_companies.yaml"))
    args = ap.parse_args()

    register = load_register(args.register) if args.register else None

    accepted: list[dict] = []
    rejected: list[str] = []
    for line in args.seeds.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        slug, _, name = (part.strip() for part in line.partition(","))
        name = name or slug
        found = find_site(slug)
        if not found:
            rejected.append(f"{slug}: no live tenant on known hosts")
            continue
        host, site = found
        page = probe_jobs(slug, host, site)
        if page is None:
            rejected.append(f"{slug}: tenant found ({host}/{site}) but jobs endpoint failed")
            continue
        if args.require_uk and not uk_signal(page):
            rejected.append(f"{slug}: live but no UK locations in sample")
            continue
        if register is not None and not register_match(name, register):
            rejected.append(f"{slug}: not matched on sponsor register ({name})")
            continue
        counts = scope_counts(slug, host, site)
        accepted.append(
            {"slug": slug, "name": name, "host": host, "site": site,
             "total": page.get("total", 0), "scope": counts}
        )
        print(f"OK   {slug}: {host}/{site}, {page.get('total', 0)} open, "
              f"scope hits {counts}", file=sys.stderr)
        time.sleep(PAUSE_S)

    lines = ["workday:"]
    for entry in accepted:
        lines.append(
            "  - {slug: %s, name: %s, host: %s, site: %s}"
            % (entry["slug"], entry["name"], entry["host"], entry["site"])
        )
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\naccepted {len(accepted)} -> {args.out}", file=sys.stderr)
    for line in rejected:
        print(f"SKIP {line}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

