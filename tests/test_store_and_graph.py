"""Store, latency metric, JSONL persistence, and end-to-end graph tests."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobs_agent.graph import run_sweep
from jobs_agent.models import Job
from jobs_agent.persistence import append_jobs, load_into_store
from jobs_agent.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def make_job(external_id="1", posted_minutes_ago=10):
    posted = datetime.now(UTC) - timedelta(minutes=posted_minutes_ago)
    return Job(
        source="greenhouse",
        company="testco",
        external_id=external_id,
        title=f"Role {external_id}",
        url=f"https://example.com/{external_id}",
        description="A role.",
        posted_at=posted.isoformat(),
    )


def test_upsert_dedupes_and_stamps_latency(tmp_path):
    store = Store(tmp_path / "jobs.db")
    job = make_job(posted_minutes_ago=30)

    new = store.upsert_jobs([job], backfill=False)
    assert new == [job.key]
    again = store.upsert_jobs([job], backfill=False)
    assert again == []

    row = store.get_job(job.key)
    assert row["is_backfill"] == 0
    # posted 30 min ago, seen now: latency in [29, 31] minutes
    assert 29 * 60 <= row["detection_latency_s"] <= 31 * 60


def test_backfill_jobs_excluded_from_metric(tmp_path):
    store = Store(tmp_path / "jobs.db")
    store.upsert_jobs([make_job("a"), make_job("b")], backfill=True)
    store.upsert_jobs([make_job("c", posted_minutes_ago=20)], backfill=False)
    stats = store.stats()
    assert stats["jobs_total"] == 3
    assert stats["jobs_backfill"] == 2
    assert stats["latency_sample_size"] == 1
    assert 19 * 60 <= stats["median_detection_latency_s"] <= 21 * 60


def test_jsonl_round_trip(tmp_path):
    store = Store(tmp_path / "jobs.db")
    keys = store.upsert_jobs([make_job("x"), make_job("y")], backfill=False)
    jobs_path = tmp_path / "jobs.jsonl"
    append_jobs(store, keys, jobs_path)

    lines = jobs_path.read_text().strip().splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["key"].startswith("greenhouse:testco:")
    assert "description_excerpt" in record

    fresh = Store(tmp_path / "fresh.db")
    assert load_into_store(fresh, jobs_path) == 2
    assert fresh.get_job(record["key"])["first_seen_at"] == record["first_seen_at"]


def _fixture_fetcher(name, parser, company):
    payload = json.loads((FIXTURES / name).read_text())
    return lambda slug: parser(payload, company)


def test_graph_end_to_end(tmp_path, monkeypatch):
    """Two sweeps over fixtures: first is backfill, second finds one new job
    which gets classified without any network or LLM."""
    from jobs_agent import graph
    from jobs_agent.ats import greenhouse

    payload = json.loads((FIXTURES / "greenhouse_sample.json").read_text())

    def fake_fetch(slug):
        return greenhouse.parse(payload, slug)

    monkeypatch.setitem(graph.FETCHERS, "greenhouse", fake_fetch)
    monkeypatch.setitem(graph.FETCHERS, "lever", lambda slug: [])
    monkeypatch.setitem(graph.FETCHERS, "ashby", lambda slug: [])

    companies = tmp_path / "companies.yaml"
    companies.write_text(
        "greenhouse:\n  - {slug: testco, name: TestCo}\n"
    )
    db = tmp_path / "jobs.db"
    jsonl_dir = tmp_path

    report1 = run_sweep(
        db_path=str(db), companies_path=str(companies), jsonl_dir=str(jsonl_dir)
    )
    assert report1.jobs_seen == 2
    assert report1.jobs_new == 2  # baseline sweep stores everything

    # second sweep: same jobs plus one brand-new posting
    new_item = dict(payload["jobs"][0])
    new_item["id"] = 999
    new_item["title"] = "Brand New ML Role"
    new_item["first_published"] = datetime.now(UTC).isoformat()
    payload["jobs"].append(new_item)

    report2 = run_sweep(
        db_path=str(db), companies_path=str(companies), jsonl_dir=str(jsonl_dir)
    )
    assert report2.jobs_seen == 3
    assert report2.jobs_new == 1

    store = Store(db)
    stats = store.stats()
    assert stats["jobs_total"] == 3
    assert stats["jobs_backfill"] == 2
    assert stats["latency_sample_size"] == 1
    assert stats["median_detection_latency_s"] < 120  # seen seconds after post

    new_row = store.search(query="Brand New")[0]
    assert new_row["tier"] is not None  # classified (rules or pending)

    # JSONL grew by exactly the new rows
    lines = (tmp_path / "jobs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3
    sweeps = (tmp_path / "sweeps.jsonl").read_text().strip().splitlines()
    assert len(sweeps) == 2
