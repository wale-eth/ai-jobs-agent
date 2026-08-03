"""MCP server contract: the four tools exist and stats runs end to end."""

import asyncio


def test_mcp_tools_registered(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_DB", str(tmp_path / "jobs.db"))
    import importlib

    import jobs_agent.mcp_server as server

    importlib.reload(server)

    tools = asyncio.run(server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert names == {"job_stats", "search_jobs", "get_job", "run_sweep"}

    stats = server.job_stats()
    assert stats["jobs_total"] == 0
    assert stats["median_detection_latency_s"] is None
