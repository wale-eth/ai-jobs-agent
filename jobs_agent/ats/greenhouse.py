"""Greenhouse public job board API poller.

Endpoint: https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
No authentication required for public boards.
"""

from __future__ import annotations

from datetime import UTC, datetime

from jobs_agent.ats.base import get_json, strip_html
from jobs_agent.models import Job

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"


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
        jobs.append(
            Job(
                source="greenhouse",
                company=company,
                external_id=str(item["id"]),
                title=item.get("title", ""),
                url=item.get("absolute_url", ""),
                location=(item.get("location") or {}).get("name", ""),
                description=strip_html(item.get("content", "")),
                posted_at=_iso(
                    item.get("first_published") or item.get("updated_at")
                ),
            )
        )
    return jobs


def fetch(board_slug: str) -> list[Job]:
    payload = get_json(API.format(board=board_slug))
    return parse(payload, board_slug)
