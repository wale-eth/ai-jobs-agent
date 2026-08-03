"""Core data types shared across the agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Tier(StrEnum):
    """Three-tier sponsorship classification.

    Policy:
      SPONSORS_EXPLICIT  the posting explicitly offers visa sponsorship
      SILENT_POSSIBLE    no mention either way; treated as a partial positive
      NO_SPONSORSHIP     the posting explicitly refuses sponsorship
    """

    SPONSORS_EXPLICIT = "sponsors_explicit"
    SILENT_POSSIBLE = "silent_possible"
    NO_SPONSORSHIP = "no_sponsorship"


@dataclass
class Job:
    """A normalized job posting from any ATS."""

    source: str  # greenhouse | lever | ashby
    company: str  # slug from companies.yaml
    external_id: str  # the ATS's own id for the posting
    title: str
    url: str
    location: str = ""
    description: str = ""  # plain text
    posted_at: str | None = None  # ISO 8601 UTC, as reported by the ATS

    @property
    def key(self) -> str:
        return f"{self.source}:{self.company}:{self.external_id}"


@dataclass
class Classification:
    tier: Tier
    method: str  # "rules" | "llm" | "pending"
    evidence: str = ""  # matched phrase or model reasoning summary
    model: str | None = None


@dataclass
class SweepReport:
    """What one sweep did; the numbers the log line and tests care about."""

    companies_polled: int = 0
    companies_failed: list[str] = field(default_factory=list)
    jobs_seen: int = 0
    jobs_new: int = 0
    classified_rules: int = 0
    classified_llm: int = 0
    classified_pending: int = 0
