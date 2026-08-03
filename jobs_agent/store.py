"""SQLite persistence and the detection-latency metric.

The metric that matters from day one:

    detection_latency = first_seen_at - posted_at

where posted_at comes from the ATS and first_seen_at is stamped by us the
first time a sweep sees the posting. Median latency across jobs first seen
after the tracker started is the headline number. Jobs that existed before
the first sweep are backfill and excluded from the metric (flagged
is_backfill=1), otherwise the metric would be dominated by old postings.
"""

from __future__ import annotations

import sqlite3
import statistics
from datetime import UTC, datetime
from pathlib import Path

from jobs_agent.models import Classification, Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    company TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    location TEXT,
    description TEXT,
    posted_at TEXT,
    first_seen_at TEXT NOT NULL,
    is_backfill INTEGER NOT NULL DEFAULT 0,
    detection_latency_s INTEGER,
    tier TEXT,
    classify_method TEXT,
    classify_evidence TEXT,
    classify_model TEXT,
    classified_at TEXT,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS sweeps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    companies_polled INTEGER,
    companies_failed TEXT,
    jobs_seen INTEGER,
    jobs_new INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jobs_tier ON jobs (tier);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs (first_seen_at);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: str | Path = "data/jobs.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # -- writes ------------------------------------------------------------

    def upsert_jobs(self, jobs: list[Job], backfill: bool) -> list[str]:
        """Insert unseen jobs, stamping first_seen_at. Returns new keys."""
        now = utcnow()
        new_keys = []
        for job in jobs:
            exists = self.conn.execute(
                "SELECT 1 FROM jobs WHERE key = ?", (job.key,)
            ).fetchone()
            if exists:
                continue
            latency = None
            if job.posted_at and not backfill and job.posted_at_precise:
                posted = datetime.fromisoformat(job.posted_at)
                latency = int(
                    (datetime.fromisoformat(now) - posted).total_seconds()
                )
                latency = max(latency, 0)
            self.conn.execute(
                """INSERT INTO jobs (key, source, company, external_id,
                   title, url, location, description, posted_at,
                   first_seen_at, is_backfill, detection_latency_s)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.key, job.source, job.company, job.external_id,
                    job.title, job.url, job.location, job.description,
                    job.posted_at, now, int(backfill), latency,
                ),
            )
            new_keys.append(job.key)
        self.conn.commit()
        return new_keys

    def save_classification(self, key: str, result: Classification) -> None:
        self.conn.execute(
            """UPDATE jobs SET tier = ?, classify_method = ?,
               classify_evidence = ?, classify_model = ?, classified_at = ?
               WHERE key = ?""",
            (
                result.tier.value, result.method, result.evidence[:2000],
                result.model, utcnow(), key,
            ),
        )
        self.conn.commit()

    def open_keys(self, source: str, company: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT key FROM jobs WHERE source = ? AND company = ? "
            "AND closed_at IS NULL",
            (source, company),
        ).fetchall()
        return {r["key"] for r in rows}

    def closed_keys(self, source: str, company: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT key FROM jobs WHERE source = ? AND company = ? "
            "AND closed_at IS NOT NULL",
            (source, company),
        ).fetchall()
        return {r["key"] for r in rows}

    def mark_closed(self, keys: list[str]) -> str:
        """Mark jobs as closed (no longer on their board). Returns timestamp."""
        now = utcnow()
        self.conn.executemany(
            "UPDATE jobs SET closed_at = ? WHERE key = ? AND closed_at IS NULL",
            [(now, k) for k in keys],
        )
        self.conn.commit()
        return now

    def reopen(self, keys: list[str]) -> None:
        """Clear closed_at for jobs that reappeared on their board."""
        self.conn.executemany(
            "UPDATE jobs SET closed_at = NULL WHERE key = ?",
            [(k,) for k in keys],
        )
        self.conn.commit()

    def record_sweep(self, report, started_at: str) -> None:
        self.conn.execute(
            """INSERT INTO sweeps (started_at, finished_at, companies_polled,
               companies_failed, jobs_seen, jobs_new)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                started_at, utcnow(), report.companies_polled,
                ",".join(report.companies_failed), report.jobs_seen,
                report.jobs_new,
            ),
        )
        self.conn.commit()

    # -- reads -------------------------------------------------------------

    def is_empty(self) -> bool:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()
        return row["n"] == 0

    def get_job(self, key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE key = ?", (key,)
        ).fetchone()
        return dict(row) if row else None

    def pending_classification(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE tier IS NULL OR classify_method = "
            "'pending' ORDER BY first_seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def search(
        self,
        query: str = "",
        tier: str | None = None,
        company: str | None = None,
        new_only: bool = False,
        include_closed: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        sql = (
            "SELECT key, source, company, title, url, location, posted_at, "
            "first_seen_at, detection_latency_s, tier, classify_method, "
            "closed_at FROM jobs WHERE 1=1"
        )
        params: list = []
        if not include_closed:
            sql += " AND closed_at IS NULL"
        if query:
            sql += " AND (title LIKE ? OR description LIKE ?)"
            params += [f"%{query}%", f"%{query}%"]
        if tier:
            sql += " AND tier = ?"
            params.append(tier)
        if company:
            sql += " AND company = ?"
            params.append(company)
        if new_only:
            sql += " AND is_backfill = 0"
        sql += " ORDER BY first_seen_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def stats(self) -> dict:
        totals = dict(
            self.conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(is_backfill) AS backfill, "
                "SUM(closed_at IS NOT NULL) AS closed FROM jobs"
            ).fetchone()
        )
        tiers = {
            r["tier"] or "unclassified": r["n"]
            for r in self.conn.execute(
                "SELECT tier, COUNT(*) AS n FROM jobs "
                "WHERE closed_at IS NULL GROUP BY tier"
            ).fetchall()
        }
        latencies = [
            r["detection_latency_s"]
            for r in self.conn.execute(
                "SELECT detection_latency_s FROM jobs "
                "WHERE detection_latency_s IS NOT NULL AND is_backfill = 0"
            ).fetchall()
        ]
        sweep_count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM sweeps"
        ).fetchone()["n"]
        return {
            "jobs_total": totals["total"],
            "jobs_open": (totals["total"] or 0) - (totals["closed"] or 0),
            "jobs_closed": totals["closed"] or 0,
            "jobs_backfill": totals["backfill"] or 0,
            "jobs_tracked_live": (totals["total"] or 0)
            - (totals["backfill"] or 0),
            "tiers": tiers,
            "sweeps": sweep_count,
            "median_detection_latency_s": (
                int(statistics.median(latencies)) if latencies else None
            ),
            "p90_detection_latency_s": (
                int(sorted(latencies)[int(0.9 * (len(latencies) - 1))])
                if latencies
                else None
            ),
            "latency_sample_size": len(latencies),
        }
