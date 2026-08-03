"""Classifier policy tests: rules table plus a faked LLM for the grey zone."""

import json

from jobs_agent.classify import LlmClassifier, classify, classify_rules
from jobs_agent.models import Tier


def test_explicit_positive():
    result = classify_rules(
        "We are happy to sponsor a Skilled Worker visa for this role."
    )
    assert result.tier == Tier.SPONSORS_EXPLICIT
    assert result.method == "rules"


def test_explicit_positive_home_office_formula():
    result = classify_rules(
        "Where the role meets the relevant criteria, visa sponsorship is "
        "available in line with Home Office requirements."
    )
    assert result.tier == Tier.SPONSORS_EXPLICIT


def test_explicit_negative():
    result = classify_rules(
        "Applicants must have the right to work in the UK. Unfortunately, "
        "sponsorship is not available for this position."
    )
    assert result.tier == Tier.NO_SPONSORSHIP


def test_negative_wins_over_positive_fragment():
    # Refusals often contain positive-sounding substrings; negatives are
    # checked first by design.
    result = classify_rules(
        "We are not able to offer Skilled Worker visa sponsorship."
    )
    assert result.tier == Tier.NO_SPONSORSHIP


def test_silent_is_partial_positive():
    result = classify_rules(
        "Build recommendation systems at scale with a great team."
    )
    assert result.tier == Tier.SILENT_POSSIBLE
    assert result.method == "rules"


def test_ambiguous_returns_none_for_llm():
    assert classify_rules(
        "Visa questions can be discussed at the screening stage."
    ) is None


class FakeMessage:
    def __init__(self, text):
        self.content = [type("Block", (), {"text": text})()]
        self.usage = type("Usage", (), {"input_tokens": 10, "output_tokens": 5})()


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.messages = self

    def create(self, **kwargs):
        return FakeMessage(self.reply)


def test_llm_grey_zone():
    llm = LlmClassifier(
        client=FakeClient(
            json.dumps(
                {"tier": "no_sponsorship", "evidence": "cannot sponsor at this time"}
            )
        )
    )
    result = classify(
        "ML Engineer", "Visa questions: we cannot sponsor at this time, sadly.",
        llm=llm,
    )
    # rules catch this one actually; use a truly grey text
    result = classify(
        "ML Engineer", "Visa support may depend on circumstances.", llm=llm
    )
    assert result.method == "llm"
    assert result.tier == Tier.NO_SPONSORSHIP
    assert result.model


def test_llm_unparseable_falls_back_to_silent():
    llm = LlmClassifier(client=FakeClient("I think they probably sponsor?"))
    result = classify(
        "ML Engineer", "Visa support may depend on circumstances.", llm=llm
    )
    assert result.tier == Tier.SILENT_POSSIBLE
    assert result.evidence.startswith("unparseable")


def test_no_llm_marks_pending():
    result = classify(
        "ML Engineer", "Visa support may depend on circumstances.", llm=None
    )
    assert result.method == "pending"
    assert result.tier == Tier.SILENT_POSSIBLE
