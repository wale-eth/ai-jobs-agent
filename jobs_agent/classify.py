"""Three-tier sponsorship classifier: rules first, LLM for the grey zone.

Policy (fixed for v1):
  explicit positive mention of sponsorship  -> SPONSORS_EXPLICIT
  explicit negative mention                 -> NO_SPONSORSHIP (hard exclude)
  no mention at all                         -> SILENT_POSSIBLE (partial
                                               positive, no LLM call needed)
  mentions visas/sponsorship ambiguously    -> LLM decides the tier

This ordering keeps LLM spend proportional to genuinely ambiguous text, not
to job volume. Every LLM call is traced to Langfuse when keys are present.
"""

from __future__ import annotations

import json
import os
import re

from jobs_agent.models import Classification, Tier
from jobs_agent.tracing import observe_llm

DEFAULT_MODEL = os.environ.get("CLASSIFIER_MODEL", "claude-haiku-4-5")

# Order matters: negatives are checked first because refusals frequently
# contain positive-sounding fragments ("Skilled Worker visa sponsorship...
# is not available").
NEGATIVE_PATTERNS = [
    (
        r"(cannot|can not|can't|unable to|not able to)\s+"
        r"(offer|provide|support|consider)[^.]{0,40}sponsor"
    ),
    r"sponsorship\s+(is|will)\s+not\b",
    (
        r"not?\s+(currently\s+)?(offer(ing)?|provid(e|ing)|"
        r"in a position to (offer|provide))[^.]{0,40}sponsor"
    ),
    r"no\s+(visa\s+)?sponsorship",
    r"without\s+(the\s+need\s+for\s+)?(visa\s+)?sponsorship",
    r"must\s+(already\s+)?have\s+(the\s+|full\s+)?right\s+to\s+work",
    r"unfortunately[^.]{0,60}sponsor",
]
POSITIVE_PATTERNS = [
    r"(visa\s+)?sponsorship\s+(is\s+)?(available|offered|provided|possible)",
    (
        r"(we\s+)?(can|are able to|are happy to|will)\s+"
        r"(offer|provide|consider|support)[^.]{0,40}(visa|sponsor)"
    ),
    r"sponsor(ship)?\s+(a\s+)?(skilled\s+worker|work)\s+visa",
    r"relocation\s+(and|&|\+)\s+visa",
    r"(happy|willing)\s+to\s+sponsor",
]
MENTION_PATTERN = re.compile(
    r"\b(sponsor|sponsorship|visa|right\s+to\s+work|work\s+authori[sz]ation)\b",
    re.I,
)

LLM_SYSTEM = """You classify job postings for UK visa sponsorship stance.
Return STRICT JSON: {"tier": "...", "evidence": "..."} where tier is exactly
one of:
- "sponsors_explicit": the posting explicitly says visa sponsorship is
  available or that they sponsor.
- "no_sponsorship": the posting explicitly refuses sponsorship or requires
  existing right to work without sponsorship.
- "silent_possible": the sponsorship stance is not actually stated either
  way (mentions of visas/eligibility that don't commit count as silent).
"evidence" is the shortest verbatim quote that justifies the tier, or ""
for silent_possible."""


def classify_rules(description: str) -> Classification | None:
    """Return a Classification when rules are conclusive, else None."""
    text = description or ""
    lowered = text.lower()

    for pattern in NEGATIVE_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            return Classification(
                Tier.NO_SPONSORSHIP, "rules", evidence=match.group(0)
            )
    for pattern in POSITIVE_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            return Classification(
                Tier.SPONSORS_EXPLICIT, "rules", evidence=match.group(0)
            )
    if not MENTION_PATTERN.search(text):
        return Classification(Tier.SILENT_POSSIBLE, "rules", evidence="")
    return None  # mentions the topic but no rule fired: the grey zone


class LlmClassifier:
    """Anthropic-backed classifier for the grey zone, traced via Langfuse."""

    def __init__(self, model: str = DEFAULT_MODEL, client=None):
        self.model = model
        self._client = client  # injectable for tests

    @property
    def available(self) -> bool:
        return self._client is not None or bool(
            os.environ.get("ANTHROPIC_API_KEY")
        )

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def classify(self, title: str, description: str) -> Classification:
        excerpt = _sponsorship_window(description)
        response = observe_llm(
            name="classify-sponsorship",
            model=self.model,
            call=lambda: self._get_client().messages.create(
                model=self.model,
                max_tokens=300,
                system=LLM_SYSTEM,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Job title: {title}\n\nRelevant excerpt from "
                            f"the posting:\n{excerpt}"
                        ),
                    }
                ],
            ),
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.M).strip()
        try:
            data = json.loads(raw)
            tier = Tier(data["tier"])
            evidence = str(data.get("evidence", ""))[:500]
        except (json.JSONDecodeError, KeyError, ValueError):
            tier, evidence = Tier.SILENT_POSSIBLE, f"unparseable: {raw[:200]}"
        return Classification(tier, "llm", evidence=evidence, model=self.model)


def _sponsorship_window(description: str, width: int = 700) -> str:
    """Send the LLM the text around sponsorship mentions, not the whole JD."""
    text = description or ""
    match = MENTION_PATTERN.search(text)
    if not match:
        return text[:width]
    start = max(0, match.start() - width // 2)
    return text[start : start + width * 2]


def classify(
    title: str, description: str, llm: LlmClassifier | None = None
) -> Classification:
    """Full policy: rules, then LLM if available, else pending."""
    result = classify_rules(description)
    if result is not None:
        return result
    if llm is not None and llm.available:
        return llm.classify(title, description)
    return Classification(Tier.SILENT_POSSIBLE, "pending", evidence="")
