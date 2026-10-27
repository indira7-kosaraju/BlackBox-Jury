"""``bbj`` command-line interface (DESIGN.md §8).

Local commands (``db upgrade``, ``seed``) act on the configured SQLite file.
Run commands (``run``, ``status``, ``report``, ``cancel``) call the local API
over HTTP with ``BBJ_API_TOKEN``. Colour is used only on a TTY; ``--json`` gives
machine-readable output.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Annotated, Any

import typer

from blackbox_jury.core.config import get_settings
from blackbox_jury.db.seed import seed as seed_db
from blackbox_jury.db.session import Database, upgrade

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Blackbox Jury CLI")
db_app = typer.Typer(no_args_is_help=True, help="Database commands")
app.add_typer(db_app, name="db")

DEFAULT_API = "http://127.0.0.1:8000"
TERMINAL = {"completed", "failed", "cancelled", "timed_out"}
REPORTS_DIR = Path("reports/out")
_BADGE = {
    "CONFIRMED": "■ CONFIRMED",
    "NOT_REPRODUCED": "○ NOT REPRODUCED",
    "INCONCLUSIVE": "◐ INCONCLUSIVE",
}

ApiOpt = Annotated[str, typer.Option("--api", help="API base URL (local only)")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output")]


class ApiCallError(RuntimeError):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        err = body.get("error", {}) if isinstance(body, dict) else {}
        super().__init__(f"{status} {err.get('code', 'error')}: {err.get('message', '')}")
        self.status = status
        self.body = body


def _http(
    method: str,
    url: str,
    token: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310  # scheme checked in _api
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except json.JSONDecodeError:
            return exc.code, {}


def _api(
    api: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not api.startswith(("http://127.0.0.1", "http://localhost")):
        raise typer.BadParameter("only a local API (127.0.0.1/localhost) is supported")
    token = get_settings().api_token.get_secret_value()
    status, payload = _http(method, api.rstrip("/") + "/api/v1" + path, token, body, headers)
    if status >= 400:
        raise ApiCallError(status, payload)
    return payload


def _echo(text: str, fg: str | None = None) -> None:
    typer.secho(text, fg=fg if sys.stdout.isatty() else None)


def _fail(exc: Exception) -> None:
    _echo(f"error: {exc}", "red")
    if (
        isinstance(exc, ApiCallError)
        and exc.body.get("error", {}).get("code") == "sandbox_unavailable"
    ):
        _echo("Docker is not reachable — no tests were executed. Start Docker Desktop and retry.")
    raise typer.Exit(1)


# -- local commands ------------------------------------------------------------------------


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply database migrations."""
    upgrade(get_settings().database_url)
    _echo("database up to date", "green")


@app.command()
def seed() -> None:
    """Seed the demo contract, curated scenarios and the RefundBot target (idempotent)."""
    settings = get_settings()
    upgrade(settings.database_url)
    summary = seed_db(Database(settings.database_url), settings)
    _echo(json.dumps(summary))


# -- API commands ----------------------------------------------------------------------------


def _resolve(
    api: str, target: str, contract: str, scenarios: list[str] | None, suite: str | None
) -> dict[str, Any]:
    targets = {t["name"]: t for t in _api(api, "GET", "/targets")["items"]}
    if target not in targets:
        raise typer.BadParameter(f"unknown target {target!r}; run `bbj seed` first")
    contracts = _api(api, "GET", f"/contracts?name={contract}")["items"]
    if not contracts:
        raise typer.BadParameter(f"unknown contract {contract!r}")
    body: dict[str, Any] = {"target_id": targets[target]["id"], "contract_id": contracts[0]["id"]}
    if scenarios:
        known = {s["key"]: s["id"] for s in _api(api, "GET", "/scenarios")["items"]}
        missing = [k for k in scenarios if k not in known]
        if missing:
            raise typer.BadParameter(f"unknown scenario(s): {missing}")
        body["scenario_ids"] = [known[k] for k in scenarios]
    else:
        body["suite"] = suite or "demo"
    return body


@app.command()
def run(
    suite: Annotated[str | None, typer.Option(help="demo | slice")] = None,
    scenario: Annotated[list[str] | None, typer.Option(help="Scenario key (repeatable)")] = None,
    target: Annotated[str, typer.Option()] = "refundbot",
    contract: Annotated[str, typer.Option()] = "refund_support_v1",
    wait: Annotated[bool, typer.Option("--wait/--no-wait")] = True,
    api: ApiOpt = DEFAULT_API,
    as_json: JsonOpt = False,
) -> None:
    """Start a run against an authorized target and stream progress."""
    try:
        body = _resolve(api, target, contract, scenario, suite)
        created = _api(api, "POST", "/runs", body, {"Idempotency-Key": uuid.uuid4().hex})
        run_id = created["id"]
        if not as_json:
            _echo(f"run {run_id} queued ({created['scenario_count']} scenarios, target {target})")
        if not wait:
            _echo(json.dumps(created) if as_json else run_id)
            return
        seen: set[str] = set()
        while True:
            status = _api(api, "GET", f"/runs/{run_id}")
            if not as_json:
                for e in _api(api, "GET", f"/runs/{run_id}/executions?limit=100")["items"]:
                    if e["verdict"] and e["id"] not in seen:
                        seen.add(e["id"])
                        sev = f" {e['severity'].upper()}" if e["severity"] else ""
                        why = f" ({e['inconclusive_reason']})" if e["inconclusive_reason"] else ""
                        _echo(f"  {_BADGE[e['verdict']]}{sev}{why}  {e['scenario_key']}")
            if status["status"] in TERMINAL:
                break
            time.sleep(1)
        _finish(api, run_id, status, as_json)
    except (ApiCallError, typer.BadParameter) as exc:
        _fail(exc)


def _finish(api: str, run_id: str, status: dict[str, Any], as_json: bool) -> None:
    report = _api(api, "GET", f"/runs/{run_id}/report")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{run_id}.json"
    path.write_text(json.dumps(report, indent=2))
    t = status["totals"]
    if as_json:
        _echo(json.dumps({"run": status, "report_path": str(path)}))
        return
    _echo(
        f"run {status['status']}: selected {t['selected']} · executed {t['executed']} · "
        f"CONFIRMED {t['CONFIRMED']} · NOT REPRODUCED {t['NOT_REPRODUCED']} · "
        f"INCONCLUSIVE {t['INCONCLUSIVE']}"
    )
    _echo(report["disclaimer"])
    _echo(f"report: {path}")


@app.command()
def status(run_id: str, api: ApiOpt = DEFAULT_API) -> None:
    """Show a run's status and totals."""
    try:
        _echo(json.dumps(_api(api, "GET", f"/runs/{run_id}"), indent=2))
    except ApiCallError as exc:
        _fail(exc)


@app.command()
def report(
    run_id: str, out: Annotated[Path | None, typer.Option()] = None, api: ApiOpt = DEFAULT_API
) -> None:
    """Fetch the JSON report for a run."""
    try:
        data = json.dumps(_api(api, "GET", f"/runs/{run_id}/report"), indent=2)
    except ApiCallError as exc:
        _fail(exc)
        return
    if out:
        out.write_text(data)
        _echo(f"report: {out}")
    else:
        typer.echo(data)


@app.command()
def cancel(
    run_id: str, yes: Annotated[bool, typer.Option("--yes")] = False, api: ApiOpt = DEFAULT_API
) -> None:
    """Cancel a run. Remaining scenarios become INCONCLUSIVE."""
    if not yes and not typer.confirm(
        f"Cancel run {run_id}? Remaining scenarios will be INCONCLUSIVE."
    ):
        raise typer.Exit(1)
    try:
        _api(api, "POST", f"/runs/{run_id}/cancel")
        _echo("cancel requested")
    except ApiCallError as exc:
        _fail(exc)


if __name__ == "__main__":
    app()
