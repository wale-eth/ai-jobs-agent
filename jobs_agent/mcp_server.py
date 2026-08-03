"""MCP server exposing the agent to any MCP client (Claude Desktop, etc.).

Run with:  python -m jobs_agent.mcp_server
Register in an MCP client config as a stdio server, e.g.

    {"mcpServers": {"ai-jobs-agent": {
        "command": "python", "args": ["-m", "jobs_agent.mcp_server"],
        "cwd": "/path/to/ai-jobs-agent"}}}
"""

from __future__ import annotations

import os

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - mcp>=2 moved FastMCP
    from fastmcp import FastMCP

from jobs_agent.store import Store

DB_PATH = os.environ.get("JOBS_DB", "data/jobs.db")

mcp = FastMCP(
    "ai-jobs-agent",
    instructions=(
        "Tracks job postings from company ATS boards (Greenhouse, Lever, "
        "Ashby), classifies UK visa sponsorship stance into three tiers "
        "(sponsors_explicit, silent_possible, no_sponsorship), and records "
        "detection latency (how quickly a posting was seen after the ATS "
        "published it)."
    ),
)


@mcp.tool()
def job_stats() -> dict:
    """Summary metrics: totals, tier breakdown, sweep count, and the
    median/p90 detection latency in seconds (live-tracked jobs only)."""
    return Store(DB_PATH).stats()


@mcp.tool()
def search_jobs(
    query: str = "",
    tier: str = "",
    company: str = "",
    new_only: bool = False,
    limit: int = 25,
) -> list[dict]:
    """Search stored postings. tier is one of sponsors_explicit,
    silent_possible, no_sponsorship, or empty for all. new_only=True
    restricts to jobs first seen after tracking began (excludes the
    initial backfill)."""
    return Store(DB_PATH).search(
        query=query,
        tier=tier or None,
        company=company or None,
        new_only=new_only,
        limit=min(limit, 100),
    )


@mcp.tool()
def get_job(key: str) -> dict:
    """Fetch one posting by its key (source:company:external_id),
    including the full description and classification evidence."""
    job = Store(DB_PATH).get_job(key)
    return job or {"error": f"no job with key {key}"}


@mcp.tool()
def run_sweep() -> dict:
    """Run one detect-classify-log cycle now (polls all configured
    company boards, stores new postings, classifies them)."""
    from jobs_agent.graph import run_sweep as _run

    report = _run(db_path=DB_PATH)
    return report.__dict__


if __name__ == "__main__":
    mcp.run()
