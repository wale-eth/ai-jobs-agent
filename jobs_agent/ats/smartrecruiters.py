"""SmartRecruiters public postings API poller.

List:   https://api.smartrecruiters.com/v1/companies/{company}/postings
Detail: .../postings/{id}  (carries the job-ad text and public URL)
No authentication required.

The list endpoint has no description text, and fetching a detail per posting
for a large employer would be hundreds of requests per sweep. Compromise:
details (description + canonical URL) are fetched only for postings released
in the last RECENT_DAYS. Genuinely new postings always fall inside that
window, so classification quality is unaffected; only backfill of old
postings arrives without text (and rules classify it as silent).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobs_agent.ats.base import get_json, strip_html
from jobs_agent.models import Job

LIST_API = (
    "https://api.smartrecruiters.com/v1/companies/{company}/postings"
    "?limit=100&offset={offset}"
)
DETAIL_API = (
    "https://api.smartrecruiters.com/v1/companies/{company}/postings/{pid}"
)
PUBLIC_URL = "https://jobs.smartrecruiters.com/{company}/{pid}"
RECENT_DAYS = 21
MAX_POSTINGS = 1000  # safety cap per company per sweep


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC).isoformat()
    except ValueError:
        return None


def parse_detail(detail: dict) -> tuple[str, str]:
    """Return (description_text, public_url) from a posting detail."""
    sections = ((detail.get("jobAd") or {}).get("sections")) or {}
    text = "\n\n".join(
        strip_html((sections.get(name) or {}).get("text", ""))
        for name in ("companyDescription", "jobDescription",
                     "qualifications", "additionalInformation")
    ).strip()
    return text, detail.get("postingUrl") or detail.get("applyUrl") or ""


def parse(
    items: list[dict], company: str, details: dict[str, dict] | None = None
) -> list[Job]:
    details = details or {}
    jobs = []
    for item in items:
        pid = str(item["id"])
        location = (item.get("location") or {})
        loc_text = location.get("fullLocation") or ", ".join(
            b for b in (location.get("city"), location.get("country")) if b
        )
        if location.get("remote"):
            loc_text = ("Remote" + (" - " + loc_text if loc_text else ""))
        description, url = "", PUBLIC_URL.format(company=company, pid=pid)
        if pid in details:
            description, detail_url = parse_detail(details[pid])
            url = detail_url or url
        jobs.append(
            Job(
                source="smartrecruiters",
                company=company,
                external_id=pid,
                title=item.get("name", ""),
                url=url,
                location=loc_text,
                description=description,
                posted_at=_iso(item.get("releasedDate")),
            )
        )
    return jobs


def fetch(company_slug: str) -> list[Job]:
    items: list[dict] = []
    offset = 0
    while len(items) < MAX_POSTINGS:
        page = get_json(LIST_API.format(company=company_slug, offset=offset))
        content = page.get("content", [])
        items.extend(content)
        offset += len(content)
        if not content or offset >= min(page.get("totalFound", 0), MAX_POSTINGS):
            break

    cutoff = datetime.now(UTC) - timedelta(days=RECENT_DAYS)
    details: dict[str, dict] = {}
    for item in items:
        released = _iso(item.get("releasedDate"))
        if released and datetime.fromisoformat(released) >= cutoff:
            pid = str(item["id"])
            try:
                details[pid] = get_json(
                    DETAIL_API.format(company=company_slug, pid=pid)
                )
            except Exception:  # noqa: BLE001 - detail is best-effort
                pass
    return parse(items, company_slug, details)
