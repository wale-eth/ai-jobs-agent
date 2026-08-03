"""The sweep pipeline as a LangGraph state machine.

    load_companies -> fetch -> store_new -+-> classify -> finalize
                                          |               ^
                                          +---(no new)----+

Conditional edge: classification only runs when the sweep found new jobs,
so scheduled runs on quiet half-hours cost zero LLM tokens.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import yaml
from langgraph.graph import END, StateGraph

from jobs_agent.ats import ashby, greenhouse, lever, smartrecruiters, workable
from jobs_agent.classify import LlmClassifier, classify
from jobs_agent.models import Job, SweepReport
from jobs_agent.store import Store, utcnow
from jobs_agent.tracing import sweep_trace

FETCHERS = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "workable": workable.fetch,
    "smartrecruiters": smartrecruiters.fetch,
}


class SweepState(TypedDict, total=False):
    companies_path: str
    db_path: str
    jsonl_dir: str  # when set, state is rehydrated from / appended to JSONL
    companies: dict
    jobs: list[Job]
    new_keys: list[str]
    closed_keys: list[str]
    reopened_keys: list[str]
    closed_at: str
    backfill: bool
    report: SweepReport
    started_at: str


def _open_store(state: SweepState) -> Store:
    store = Store(state.get("db_path", "data/jobs.db"))
    jsonl_dir = state.get("jsonl_dir")
    if jsonl_dir and store.is_empty():
        from jobs_agent.persistence import load_closures, load_into_store

        load_into_store(store, Path(jsonl_dir) / "jobs.jsonl")
        load_closures(store, Path(jsonl_dir) / "closures.jsonl")
    return store


def load_companies(state: SweepState) -> SweepState:
    path = state.get("companies_path") or str(
        Path(__file__).parent / "companies.yaml"
    )
    with open(path, encoding="utf-8") as fh:
        companies = yaml.safe_load(fh)
    return {"companies": companies, "started_at": utcnow()}


def fetch(state: SweepState) -> SweepState:
    report = SweepReport()
    jobs: list[Job] = []
    for source, entries in state["companies"].items():
        fetcher = FETCHERS.get(source)
        if fetcher is None:
            continue
        for entry in entries or []:
            slug = entry["slug"]
            report.companies_polled += 1
            try:
                fetched = fetcher(slug)
                jobs.extend(fetched)
            except Exception as exc:  # noqa: BLE001 - resilient by design
                report.companies_failed.append(f"{source}:{slug} ({exc})")
    report.jobs_seen = len(jobs)
    return {"jobs": jobs, "report": report}


def store_new(state: SweepState) -> SweepState:
    store = _open_store(state)
    backfill = store.is_empty()  # first ever sweep is baseline, not "new"
    new_keys = store.upsert_jobs(state["jobs"], backfill=backfill)
    report = state["report"]
    report.jobs_new = len(new_keys)

    # Closure diff: a stored open job that a successfully polled board no
    # longer lists has been taken down. Failed boards are exempt, and a board
    # that suddenly reports zero jobs while having many stored is treated as
    # a glitch, not a mass takedown.
    failed = {f.split(" ")[0] for f in report.companies_failed}
    seen_by_board: dict[tuple[str, str], set[str]] = {}
    for job in state["jobs"]:
        seen_by_board.setdefault((job.source, job.company), set()).add(job.key)

    closed: list[str] = []
    reopened: list[str] = []
    if not backfill:
        for source, entries in state["companies"].items():
            for entry in entries or []:
                slug = entry["slug"]
                if f"{source}:{slug}" in failed:
                    continue
                seen = seen_by_board.get((source, slug), set())
                stored_open = store.open_keys(source, slug)
                if not seen and len(stored_open) > 5:
                    continue  # suspicious empty response; leave untouched
                closed.extend(sorted(stored_open - seen))
                reopened.extend(sorted(store.closed_keys(source, slug) & seen))

    closed_at = store.mark_closed(closed) if closed else ""
    if reopened:
        store.reopen(reopened)
    report.jobs_closed = len(closed)
    return {
        "new_keys": new_keys,
        "closed_keys": closed,
        "reopened_keys": reopened,
        "closed_at": closed_at,
        "backfill": backfill,
        "report": report,
    }


def has_new_jobs(state: SweepState) -> str:
    return "classify" if state["new_keys"] else "finalize"


def classify_new(state: SweepState) -> SweepState:
    store = Store(state.get("db_path", "data/jobs.db"))
    llm = LlmClassifier()
    report = state["report"]
    for key in state["new_keys"]:
        job = store.get_job(key)
        if job is None:
            continue
        result = classify(job["title"], job["description"], llm=llm)
        store.save_classification(key, result)
        if result.method == "rules":
            report.classified_rules += 1
        elif result.method == "llm":
            report.classified_llm += 1
        else:
            report.classified_pending += 1
    return {"report": report}


def finalize(state: SweepState) -> SweepState:
    store = Store(state.get("db_path", "data/jobs.db"))
    store.record_sweep(state["report"], state["started_at"])
    jsonl_dir = state.get("jsonl_dir")
    if jsonl_dir:
        from jobs_agent.persistence import (
            append_closures,
            append_jobs,
            append_sweep,
            export_snapshot,
        )

        append_jobs(
            store, state.get("new_keys", []), Path(jsonl_dir) / "jobs.jsonl"
        )
        append_closures(
            state.get("closed_keys", []),
            state.get("reopened_keys", []),
            state.get("closed_at") or state["started_at"],
            Path(jsonl_dir) / "closures.jsonl",
        )
        append_sweep(
            state["report"], state["started_at"],
            Path(jsonl_dir) / "sweeps.jsonl",
        )
        export_snapshot(store, Path(jsonl_dir) / "open_jobs.json")
    return {}


def build_graph():
    graph = StateGraph(SweepState)
    graph.add_node("load_companies", load_companies)
    graph.add_node("fetch", fetch)
    graph.add_node("store_new", store_new)
    graph.add_node("classify", classify_new)
    graph.add_node("finalize", finalize)

    graph.set_entry_point("load_companies")
    graph.add_edge("load_companies", "fetch")
    graph.add_edge("fetch", "store_new")
    graph.add_conditional_edges(
        "store_new", has_new_jobs, {"classify": "classify", "finalize": "finalize"}
    )
    graph.add_edge("classify", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def run_sweep(
    db_path: str = "data/jobs.db",
    companies_path: str | None = None,
    jsonl_dir: str | None = None,
) -> SweepReport:
    app = build_graph()
    with sweep_trace("ats-sweep", metadata={"db": db_path}):
        state = app.invoke(
            {
                "db_path": db_path,
                **({"companies_path": companies_path} if companies_path else {}),
                **({"jsonl_dir": jsonl_dir} if jsonl_dir else {}),
            }
        )
    return state["report"]
