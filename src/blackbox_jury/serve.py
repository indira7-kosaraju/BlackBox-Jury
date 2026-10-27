"""Run the API with host/port from settings (env vars or .env), not shell defaults.

Usage: ``PYTHONPATH=src uv run python -m blackbox_jury.serve`` (what ``make run`` does).
"""

from __future__ import annotations

import uvicorn

from blackbox_jury.core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "blackbox_jury.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,  # our redacting JSON logging is configured by the app
    )


if __name__ == "__main__":
    main()
