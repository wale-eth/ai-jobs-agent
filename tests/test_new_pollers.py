"""Parser tests for the Workable and SmartRecruiters pollers."""

import json
from pathlib import Path

from jobs_agent.ats import smartrecruiters, workable

FIXTURES = Path(__file__).parent / "fixtures"


def test_workable_parse():
    payload = json.loads((FIXTURES / "workable_sample.json").read_text())
    jobs = workable.parse(payload, "huggingface")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "workable"
    assert job.external_id == "F4C096B22E"
    assert job.location.startswith("Remote")
    assert "Paris" in job.location
    # date-only timestamps must not feed the minutes-level latency metric
    assert job.posted_at == "2026-07-30T00:00:00+00:00"
    assert job.posted_at_precise is False
    assert "visa sponsorship" in job.description


def test_workable_imprecise_dates_excluded_from_latency(tmp_path):
    from jobs_agent.store import Store

    payload = json.loads((FIXTURES / "workable_sample.json").read_text())
    jobs = workable.parse(payload, "huggingface")
    store = Store(tmp_path / "jobs.db")
    store.upsert_jobs(jobs, backfill=False)
    row = store.get_job(jobs[0].key)
    assert row["detection_latency_s"] is None
    assert store.stats()["latency_sample_size"] == 0


def test_smartrecruiters_parse_with_details():
    fixture = json.loads((FIXTURES / "smartrecruiters_sample.json").read_text())
    jobs = smartrecruiters.parse(fixture["list"], "asos", fixture["detail"])
    assert len(jobs) == 2

    recent = jobs[0]
    assert recent.source == "smartrecruiters"
    assert recent.posted_at == "2026-08-03T16:23:57.082000+00:00"
    assert recent.posted_at_precise is True
    assert recent.url.endswith("senior-project-manager")
    assert "right to work in the UK" in recent.description
    assert recent.location == "Barnsley, England, United Kingdom"

    old = jobs[1]  # outside the detail window: no description, built URL
    assert old.description == ""
    assert old.url == "https://jobs.smartrecruiters.com/asos/744000999999999"
    assert old.location.startswith("Remote")
