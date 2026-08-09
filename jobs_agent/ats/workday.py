"""Workday CXS poller.

Workday has no official public job API and no cross-tenant search: every
employer runs its own tenant career site at
    https://{tenant}.{host}.myworkdayjobs.com/{site}
Each site is backed by the same JSON endpoint the site's own frontend uses:

List (POST, paged, max 20 per page):
    https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    body: {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": ""}
    -> {"total": N, "jobPostings": [{"title", "externalPath",
        "locationsText", "postedOn", "bulletFields": [req_id]}]}

Detail (GET):
    https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}
    -> {"jobPostingInfo": {"jobDescription" (HTML), "location",
        "externalUrl", "postedOn", ...}}

Caveats this module handles:
- "postedOn" is humanized ("Posted Today", "Posted 2 Days Ago",
  "Posted 30+ Days Ago"): dates are approximate day-level values, so jobs
  carry posted_at_precise=False and stay out of the latency metric
  (same treatment as Workable's date-only timestamps).
- Details cost one request per posting, so like the SmartRecruiters poller
  they are fetched only for postings inside RECENT_DAYS. Genuinely new
  postings always fall inside that window.
- Applying on Workday requires a candidate account. Detection and
  preparation still work; the apply step is flagged for Adewale
  ("account required, waiting for Adewale") downstream.

companies.yaml entries for this source need two extra keys:
    workday:
      - {slug: hsbc, name: HSBC, host: wd3, site: HSBCCareers}
`slug` doubles as the tenant name in the CXS URL. See
tools/discover_workday.py for building and verifying these entries.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import UTC, datetime, timedelta

from jobs_agent.ats.base import USER_AGENT, get_json, strip_html
from jobs_agent.models import Job

LIST_API = "https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
DETAIL_API = "https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}"
PUBLIC_URL = "https://{tenant}.{host}.myworkdayjobs.com/en-US/{site}{path}"
PAGE_SIZE = 20            # Workday's hard maximum for the jobs endpoint
MAX_POSTINGS = 1000       # safety cap per tenant per sweep
RECENT_DAYS = 21

_POSTED_ON = re.compile(r"posted\s+(?:(today)|(yesterday)|(\d+)\+?\s+days?\s+ago)", re.I)


def post_json(url: str, body: dict, timeout: int = 30) -> dict:
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


def _posted_at(posted_on: str | None, now: datetime | None = None) -> str | None:
    """Approximate ISO date from Workday's humanized postedOn text.

    "Posted 30+ Days Ago" maps to exactly -30 days: a floor, not the real
    date. Always pair with posted_at_precise=False.
    """
    if not posted_on:
        return None
    match = _POSTED_ON.search(posted_on)
    if not match:
        return None
    now = now or datetime.now(UTC)
    today, yesterday, days = match.groups()
    delta = 0 if today else 1 if yesterday else int(days)
    day = (now - timedelta(days=delta)).date()
    return datetime(day.year, day.month, day.day, tzinfo=UTC).isoformat()


def _external_id(item: dict) -> str:
    bullets = [b for b in (item.get("bulletFields") or []) if b]
    if bullets:
        return str(bullets[0])
    # fall back to the last path segment, e.g. .../Title_JR-2026-1234
    return (item.get("externalPath") or "").rsplit("/", 1)[-1] or "unknown"


def parse(
    items: list[dict],
    tenant: str,
    host: str,
    site: str,
    details: dict[str, dict] | None = None,
    now: datetime | None = None,
) -> list[Job]:
    details = details or {}
    jobs = []
    for item in items:
        path = item.get("externalPath") or ""
        external_id = _external_id(item)
        url = PUBLIC_URL.format(tenant=tenant, host=host, site=site, path=path)
        description = ""
        location = item.get("locationsText") or ""
        if external_id in details:
            info = (details[external_id].get("jobPostingInfo")) or {}
            description = strip_html(info.get("jobDescription", ""))
            url = info.get("externalUrl") or url
            extra = ", ".join(
                loc for loc in (info.get("additionalLocations") or []) if loc
            )
            location = info.get("location") or location
            if extra:
                location = f"{location}; {extra}" if location else extra
        jobs.append(
            Job(
                source="workday",
                company=tenant,
                external_id=external_id,
                title=item.get("title", ""),
                url=url,
                location=location,
                description=description,
                posted_at=_posted_at(item.get("postedOn"), now=now),
                posted_at_precise=False,  # humanized day-level dates only
            )
        )
    return jobs


def fetch(company_slug: str, host: str = "wd3", site: str | None = None) -> list[Job]:
    """Poll one Workday tenant. Registered in graph.FETCHERS as "workday".

    companies.yaml supplies host and site per entry (see module docstring);
    graph.fetch passes any extra entry keys through as keyword arguments.
    """
    if not site:
        raise ValueError(
            f"workday entry for {company_slug!r} needs a 'site' key in companies.yaml"
        )
    tenant = company_slug
    items: list[dict] = []
    offset = 0
    while len(items) < MAX_POSTINGS:
        page = post_json(
            LIST_API.format(tenant=tenant, host=host, site=site),
            {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset,
             "searchText": ""},
        )
        postings = page.get("jobPostings", [])
        items.extend(postings)
        offset += len(postings)
        if not postings or offset >= min(page.get("total", 0), MAX_POSTINGS):
            break

    cutoff = datetime.now(UTC) - timedelta(days=RECENT_DAYS)
    details: dict[str, dict] = {}
    for item in items:
        posted = _posted_at(item.get("postedOn"))
        if posted and datetime.fromisoformat(posted) >= cutoff:
            path = item.get("externalPath") or ""
            try:
                details[_external_id(item)] = get_json(
                    DETAIL_API.format(tenant=tenant, host=host, site=site,
                                      path=path)
                )
            except Exception:  # noqa: BLE001 - detail is best-effort
                pass
    return parse(items, tenant, host, site, details)

