"""Lever public postings API poller.

Endpoint: https://api.lever.co/v0/postings/{site}?mode=json
No authentication required for public boards.
"""

from __future__ import annotations

from datetime import UTC, datetime

from jobs_agent.ats.base import get_json, strip_html
from jobs_agent.models import Job

API = "https://api.lever.co/v0/postings/{site}?mode=json"


def _iso_from_ms(value: int | None) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()


def parse(payload: list, company: str) -> list[Job]:
    jobs = []
    for item in payload:
        categories = item.get("categories") or {}
        description = item.get("descriptionPlain") or strip_html(
            item.get("description", "")
        )
        extra = "\n".join(
            f"{lst.get('text', '')}\n{strip_html(lst.get('content', ''))}"
            for lst in item.get("lists", [])
        )
        jobs.append(
            Job(
                source="lever",
                company=company,
                external_id=str(item["id"]),
                title=item.get("text", ""),
                url=item.get("hostedUrl", ""),
                location=categories.get("location", "") or "",
                description=(description + "\n" + extra).strip(),
                posted_at=_iso_from_ms(item.get("createdAt")),
            )
        )
    return jobs


def fetch(site_slug: str) -> list[Job]:
    payload = get_json(API.format(site=site_slug))
    return parse(payload, site_slug)
