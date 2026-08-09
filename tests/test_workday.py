"""Parser tests for the Workday poller."""

import json
from datetime import UTC, datetime
from pathlib import Path

from jobs_agent.ats import workday

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _jobs():
    fixture = json.loads((FIXTURES / "workday_sample.json").read_text())
    return workday.parse(
        fixture["list"], "examplebank", "wd3", "ExampleCareers",
        fixture["detail"], now=NOW,
    )


def test_workday_parse_with_detail():
    jobs = _jobs()
    assert len(jobs) == 2
    job = jobs[0]
    assert job.source == "workday"
    assert job.company == "examplebank"
    assert job.external_id == "JR-2026-04512"
    assert job.key == "workday:examplebank:JR-2026-04512"
    assert job.url.endswith("_JR-2026-04512")
    assert "visa sponsorship" in job.description
    assert job.location == "London; Edinburgh"
    # humanized "Posted 2 Days Ago" -> approximate day-level date
    assert job.posted_at == "2026-08-07T00:00:00+00:00"
    assert job.posted_at_precise is False


def test_workday_parse_backfill_without_detail():
    old = _jobs()[1]
    assert old.description == ""  # outside the detail window
    assert old.location == "2 Locations"
    assert old.url == (
        "https://examplebank.wd3.myworkdayjobs.com/en-US/ExampleCareers"
        "/job/Edinburgh/Machine-Learning-Engineer_JR-2026-03998"
    )
    # "30+ days" is a floor, still imprecise by design
    assert old.posted_at == "2026-07-10T00:00:00+00:00"
    assert old.posted_at_precise is False


def test_posted_at_edge_cases():
    assert workday._posted_at("Posted Today", now=NOW) == "2026-08-09T00:00:00+00:00"
    assert workday._posted_at("Posted Yesterday", now=NOW) == "2026-08-08T00:00:00+00:00"
    assert workday._posted_at("gibberish", now=NOW) is None
    assert workday._posted_at(None, now=NOW) is None


def test_fetch_requires_site():
    try:
        workday.fetch("examplebank", host="wd3", site=None)
    except ValueError as exc:
        assert "site" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("fetch without site must raise ValueError")

