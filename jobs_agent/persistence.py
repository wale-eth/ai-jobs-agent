"""Append-only JSONL persistence for CI-friendly state.

The scheduled GitHub Actions run has no server and no database; its state
lives in a `data` branch of the repo. Committing a SQLite file every 30
minutes would bloat git history (binary, full-file deltas), so the durable
format is append-only JSONL: each sweep appends its new jobs and one sweep
record, which git stores as tiny line diffs. The SQLite store is rebuilt
from JSONL at the start of each run and used as a working cache.

Full job descriptions are not persisted (they can always be re-fetched by
url); an excerpt is kept for context.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobs_agent.store import Store

EXCERPT_CHARS = 500

JOB_FIELDS = [
    "key", "source", "company", "external_id", "title", "url", "location",
    "posted_at", "first_seen_at", "is_backfill", "detection_latency_s",
    "tier", "classify_method", "classify_evidence", "classify_model",
    "classified_at",
]


def load_into_store(store: Store, jobs_path: str | Path) -> int:
    """Rehydrate the working DB from jobs.jsonl. Returns rows loaded."""
    path = Path(jobs_path)
    if not path.exists():
        return 0
    loaded = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            store.conn.execute(
                f"""INSERT OR IGNORE INTO jobs
                    ({", ".join(JOB_FIELDS)}, description)
                    VALUES ({", ".join("?" for _ in JOB_FIELDS)}, ?)""",
                [row.get(f) for f in JOB_FIELDS]
                + [row.get("description_excerpt", "")],
            )
            loaded += 1
    store.conn.commit()
    return loaded


def append_jobs(store: Store, keys: list[str], jobs_path: str | Path) -> None:
    """Append newly seen jobs (by key) to jobs.jsonl."""
    if not keys:
        return
    path = Path(jobs_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for key in keys:
            job = store.get_job(key)
            if job is None:
                continue
            record = {f: job.get(f) for f in JOB_FIELDS}
            record["description_excerpt"] = (job.get("description") or "")[
                :EXCERPT_CHARS
            ]
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_sweep(report, started_at: str, sweeps_path: str | Path) -> None:
    path = Path(sweeps_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"started_at": started_at, **report.__dict__}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
