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


def load_closures(store: Store, closures_path: str | Path) -> int:
    """Apply the closed/reopened event log to the working DB, in order."""
    path = Path(closures_path)
    if not path.exists():
        return 0
    applied = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if event.get("event") == "reopened":
                store.reopen([event["key"]])
            else:
                store.conn.execute(
                    "UPDATE jobs SET closed_at = ? WHERE key = ?",
                    (event["at"], event["key"]),
                )
            applied += 1
    store.conn.commit()
    return applied


def append_closures(
    closed: list[str],
    reopened: list[str],
    at: str,
    closures_path: str | Path,
) -> None:
    if not closed and not reopened:
        return
    path = Path(closures_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for key in closed:
            fh.write(json.dumps({"event": "closed", "key": key, "at": at}) + "\n")
        for key in reopened:
            fh.write(
                json.dumps({"event": "reopened", "key": key, "at": at}) + "\n"
            )


SNAPSHOT_FIELDS = [
    "key", "source", "company", "title", "url", "location", "posted_at",
    "first_seen_at", "is_backfill", "detection_latency_s", "tier",
    "classify_method", "classify_evidence",
]


def export_snapshot(store: Store, snapshot_path: str | Path) -> int:
    """Write a minified JSON array of OPEN jobs for the frontend.

    Much smaller than the full history: closed jobs are dropped, evidence and
    excerpts are trimmed, and the JSON is unindented. The full record stays in
    jobs.jsonl; this file exists so every page view doesn't download history.
    """
    rows = store.conn.execute(
        "SELECT * FROM jobs WHERE closed_at IS NULL ORDER BY posted_at DESC"
    ).fetchall()
    out = []
    for row in rows:
        record = {f: row[f] for f in SNAPSHOT_FIELDS}
        record["classify_evidence"] = (record["classify_evidence"] or "")[:300]
        record["description_excerpt"] = (row["description"] or "")[:300]
        out.append(record)
    path = Path(snapshot_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(out, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return len(out)
