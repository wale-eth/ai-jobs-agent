"""Parser tests against captured API fixtures."""

import json
from pathlib import Path

from jobs_agent.ats import ashby, greenhouse, lever

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_greenhouse_parse():
    jobs = greenhouse.parse(load("greenhouse_sample.json"), "skyscanner")
    assert len(jobs) == 2
    job = jobs[0]
    assert job.source == "greenhouse"
    assert job.external_id == "7774936"
    assert job.title == "Data Engineering Manager"
    assert job.location == "London or Edinburgh"
    # posted_at prefers first_published, normalized to UTC
    assert job.posted_at == "2026-04-02T12:19:03+00:00"
    # content is double-escaped HTML; parser must unescape then strip tags
    assert "<p>" not in job.description
    assert "&lt;" not in job.description
    assert "Skilled Worker visa sponsorship" in job.description
    assert job.key == "greenhouse:skyscanner:7774936"


def test_lever_parse():
    jobs = lever.parse(load("lever_sample.json"), "moonpig")
    assert len(jobs) == 2
    job = jobs[0]
    assert job.title == "Analytics Engineering Lead"
    assert job.location == "London"
    # createdAt is epoch milliseconds
    assert job.posted_at.startswith("2026-")
    assert job.posted_at.endswith("+00:00")
    # list sections are folded into the description
    assert "semantic layer" in job.description


def test_ashby_parse_skips_unlisted():
    jobs = ashby.parse(load("ashby_sample.json"), "posthog")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Product Engineer"
    assert job.posted_at == "2026-07-28T09:15:00+00:00"
    assert "sponsor a Skilled Worker visa" in job.description
