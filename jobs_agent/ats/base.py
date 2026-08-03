"""Shared HTTP plumbing for the ATS pollers."""

from __future__ import annotations

import json
import urllib.request

USER_AGENT = "ai-jobs-agent/1.0 (+https://github.com/wale-eth/ai-jobs-agent)"


def get_json(url: str, timeout: int = 30) -> dict | list:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def strip_html(text: str) -> str:
    """Cheap HTML-to-text: good enough for classification input."""
    import html
    import re

    text = html.unescape(text or "")
    text = re.sub(r"<(br|/p|/li|/div|/h[1-6])\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()
