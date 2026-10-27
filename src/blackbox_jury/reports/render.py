"""Static HTML report (DESIGN.md). Jinja2 with autoescaping ALWAYS on: every
piece of evidence, agent output and scenario text is escaped. No external
resources are referenced; serve with ``CONTENT_SECURITY_POLICY``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES = Path(__file__).resolve().parent / "templates"
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)

_env = Environment(
    loader=FileSystemLoader(TEMPLATES),
    autoescape=True,  # never disable: report content is untrusted
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)


_env.filters["pretty"] = _pretty


def render_html(report: dict[str, Any]) -> str:
    totals = report["totals"]
    executed = max(totals["executed"], 1)
    bar = [
        {
            "label": label,
            "key": key,
            "count": totals[key],
            "pct": round(100 * totals[key] / executed, 1),
        }
        for key, label in (
            ("CONFIRMED", "CONFIRMED"),
            ("NOT_REPRODUCED", "NOT REPRODUCED"),
            ("INCONCLUSIVE", "INCONCLUSIVE"),
        )
    ]
    return _env.get_template("report.html.j2").render(report=report, bar=bar)
