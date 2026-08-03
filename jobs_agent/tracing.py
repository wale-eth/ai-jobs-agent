"""Langfuse tracing that degrades to a no-op without credentials.

Every LLM call goes through observe_llm(), which records the generation
(model, input size, latency, token usage) to Langfuse when
LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are set, and simply executes the
call when they are not. Sweeps are wrapped in a trace via sweep_trace().
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager


def _langfuse():
    if not (
        os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    ):
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception:
        return None


@contextmanager
def sweep_trace(name: str, metadata: dict | None = None):
    """Wrap a whole sweep in one Langfuse trace."""
    client = _langfuse()
    if client is None:
        yield None
        return
    with client.start_as_current_observation(
        as_type="span", name=name, metadata=metadata or {}
    ):
        yield client
    try:
        client.flush()
    except Exception:  # noqa: BLE001 - tracing must never break a sweep
        pass


def observe_llm(name: str, model: str, call):
    """Execute an LLM call, recording it as a Langfuse generation."""
    client = _langfuse()
    start = time.perf_counter()
    if client is None:
        return call()
    with client.start_as_current_observation(
        as_type="generation", name=name
    ) as generation:
        response = call()
        try:
            usage = getattr(response, "usage", None)
            generation.update(
                model=model,
                output=str(response.content[0].text)[:1000]
                if getattr(response, "content", None)
                else "",
                usage_details={
                    "input": getattr(usage, "input_tokens", 0),
                    "output": getattr(usage, "output_tokens", 0),
                }
                if usage
                else None,
                metadata={"latency_s": round(time.perf_counter() - start, 3)},
            )
        except Exception:  # noqa: BLE001 - tracing must never break a sweep
            pass
        return response
