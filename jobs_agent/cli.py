"""Command-line entry points.

    python -m jobs_agent.cli sweep     run one detect-classify-log cycle
    python -m jobs_agent.cli stats     print the metrics summary as JSON
    python -m jobs_agent.cli search    query stored jobs
"""

from __future__ import annotations

import argparse
import json
import sys

from jobs_agent.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobs-agent")
    parser.add_argument("--db", default="data/jobs.db")
    sub = parser.add_subparsers(dest="command", required=True)

    sweep_p = sub.add_parser("sweep", help="run one polling sweep")
    sweep_p.add_argument("--companies", default=None)
    sweep_p.add_argument(
        "--jsonl-dir",
        default=None,
        help="rehydrate from and append results to JSONL files in this dir",
    )

    sub.add_parser("stats", help="print metrics summary")

    search_p = sub.add_parser("search", help="search stored jobs")
    search_p.add_argument("--query", default="")
    search_p.add_argument("--tier", default=None)
    search_p.add_argument("--company", default=None)
    search_p.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)

    if args.command == "sweep":
        from jobs_agent.graph import run_sweep

        report = run_sweep(
            db_path=args.db,
            companies_path=args.companies,
            jsonl_dir=args.jsonl_dir,
        )
        print(json.dumps(report.__dict__, indent=2))
        return 0

    store = Store(args.db)
    if args.command == "stats":
        print(json.dumps(store.stats(), indent=2))
    elif args.command == "search":
        rows = store.search(
            query=args.query,
            tier=args.tier,
            company=args.company,
            limit=args.limit,
        )
        print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
