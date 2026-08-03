"""Workable public widget API poller.

Endpoint: https://apply.workable.com/api/v1/widget/accounts/{account}?details=true
No authentication required. Note: published_on is a DATE with no time
component, so Workable jobs are excluded from the minutes-level detection
latency metric (posted_at_precise=False) rather than distorting it.
"""

from __future__ import annotations

from jobs_agent.ats.base import get_json, strip_html
from jobs_agent.models import Job

API = "https://apply.workable.com/api/v1/widget/accounts/{account}?details=true"


def parse(payload: dict, company: str) -> list[Job]:
    jobs = []
    for item in payload.get("jobs", []):
        location_bits = [item.get("city") or "", item.get("country") or ""]
        location = ", ".join(b for b in location_bits if b)
        if item.get("telecommuting"):
            location = ("Remote" + (" - " + location if location else ""))
        posted = item.get("published_on")  # date only, e.g. "2026-07-30"
        jobs.append(
            Job(
                source="workable",
                company=company,
                external_id=str(item.get("shortcode") or item.get("code")),
                title=item.get("title", ""),
                url=item.get("url", ""),
                location=location,
                description=strip_html(item.get("description", "")),
                posted_at=f"{posted}T00:00:00+00:00" if posted else None,
                posted_at_precise=False,
            )
        )
    return jobs


def fetch(account_slug: str) -> list[Job]:
    payload = get_json(API.format(account=account_slug))
    return parse(payload, account_slug)
