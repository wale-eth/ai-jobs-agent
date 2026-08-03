"""Ashby public posting API poller.

Endpoint: https://api.ashbyhq.com/posting-api/job-board/{org}
No authentication required for public boards.
"""

from __future__ import annotations

from datetime import UTC, datetime

from jobs_agent.ats.base import get_json, strip_html
from jobs_agent.models import Job

API = "https://api.ashbyhq.com/posting-api/job-board/{org}"


def _iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC).isoformat()
    except ValueError:
        return None


def parse(payload: dict, company: str) -> list[Job]:
    jobs = []
    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue
        description = item.get("descriptionPlain") or strip_html(
            item.get("descriptionHtml", "")
        )
        jobs.append(
            Job(
                source="ashby",
                company=company,
                external_id=str(item["id"]),
                title=item.get("title", ""),
                url=item.get("jobUrl") or item.get("applyUrl", ""),
                location=item.get("location", "") or "",
                description=description,
                posted_at=_iso(item.get("publishedAt")),
            )
        )
    return jobs


def fetch(org_slug: str) -> list[Job]:
    payload = get_json(API.format(org=org_slug))
    return parse(payload, org_slug)
