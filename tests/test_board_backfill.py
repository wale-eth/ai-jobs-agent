"""Adding a new board must not pollute the detection-latency metric."""

import json
from pathlib import Path

from jobs_agent.graph import run_sweep
from jobs_agent.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


def test_new_board_jobs_are_baseline(tmp_path, monkeypatch):
    from jobs_agent import graph
    from jobs_agent.ats import greenhouse

    payload = json.loads((FIXTURES / "greenhouse_sample.json").read_text())

    monkeypatch.setitem(
        graph.FETCHERS, "greenhouse",
        lambda slug: greenhouse.parse(payload, slug),
    )
    for src in ("lever", "ashby", "workable", "smartrecruiters"):
        monkeypatch.setitem(graph.FETCHERS, src, lambda slug: [])

    companies = tmp_path / "companies.yaml"
    companies.write_text("greenhouse:\n  - {slug: alpha, name: Alpha}\n")
    db = tmp_path / "jobs.db"

    run_sweep(str(db), str(companies), jsonl_dir=str(tmp_path))

    # Registry grows: board beta joins with old postings (fixture dates are
    # months in the past). Those must arrive as backfill with no latency.
    companies.write_text(
        "greenhouse:\n"
        "  - {slug: alpha, name: Alpha}\n"
        "  - {slug: beta, name: Beta}\n"
    )
    run_sweep(str(db), str(companies), jsonl_dir=str(tmp_path))

    store = Store(db)
    beta_rows = store.search(company="beta", include_closed=True, limit=10)
    assert beta_rows, "beta jobs stored"
    full = [store.get_job(r["key"]) for r in beta_rows]
    assert all(r["is_backfill"] == 1 for r in full)
    assert all(r["detection_latency_s"] is None for r in full)
    assert store.stats()["latency_sample_size"] == 0
