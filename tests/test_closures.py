"""Closed-job detection: disappearances close jobs, failures don't."""

import json
from pathlib import Path

from jobs_agent.graph import run_sweep
from jobs_agent.persistence import load_closures, load_into_store
from jobs_agent.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def _setup(tmp_path, monkeypatch, payload):
    from jobs_agent import graph
    from jobs_agent.ats import greenhouse

    monkeypatch.setitem(
        graph.FETCHERS, "greenhouse",
        lambda slug: greenhouse.parse(payload, slug),
    )
    for src in ("lever", "ashby", "workable", "smartrecruiters"):
        monkeypatch.setitem(graph.FETCHERS, src, lambda slug: [])
    companies = tmp_path / "companies.yaml"
    companies.write_text("greenhouse:\n  - {slug: testco, name: TestCo}\n")
    return companies


def test_disappeared_job_closes_and_snapshot_shrinks(tmp_path, monkeypatch):
    payload = json.loads((FIXTURES / "greenhouse_sample.json").read_text())
    companies = _setup(tmp_path, monkeypatch, payload)
    db = tmp_path / "jobs.db"

    run_sweep(str(db), str(companies), jsonl_dir=str(tmp_path))  # baseline: 2

    removed = payload["jobs"].pop(1)  # one job taken down
    report = run_sweep(str(db), str(companies), jsonl_dir=str(tmp_path))
    assert report.jobs_closed == 1

    store = Store(db)
    stats = store.stats()
    assert stats["jobs_total"] == 2
    assert stats["jobs_open"] == 1
    assert stats["jobs_closed"] == 1
    assert all(r["closed_at"] is None for r in store.search())
    assert len(store.search(include_closed=True)) == 2

    snapshot = json.loads((tmp_path / "open_jobs.json").read_text())
    assert len(snapshot) == 1
    assert snapshot[0]["title"] != removed["title"]

    # closures survive a rebuild from JSONL
    fresh = Store(tmp_path / "fresh.db")
    load_into_store(fresh, tmp_path / "jobs.jsonl")
    load_closures(fresh, tmp_path / "closures.jsonl")
    assert fresh.stats()["jobs_closed"] == 1

    # and the job reopens if it reappears
    payload["jobs"].append(removed)
    report = run_sweep(str(db), str(companies), jsonl_dir=str(tmp_path))
    assert Store(db).stats()["jobs_closed"] == 0


def test_failed_board_closes_nothing(tmp_path, monkeypatch):
    from jobs_agent import graph

    payload = json.loads((FIXTURES / "greenhouse_sample.json").read_text())
    companies = _setup(tmp_path, monkeypatch, payload)
    db = tmp_path / "jobs.db"
    run_sweep(str(db), str(companies), jsonl_dir=str(tmp_path))

    def boom(slug):
        raise RuntimeError("board down")

    monkeypatch.setitem(graph.FETCHERS, "greenhouse", boom)
    report = run_sweep(str(db), str(companies), jsonl_dir=str(tmp_path))
    assert report.jobs_closed == 0
    assert Store(db).stats()["jobs_open"] == 2
