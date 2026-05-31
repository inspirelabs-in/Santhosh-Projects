"""Typer CLI: `grabon-intel <cmd>`."""
from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.table import Table

from . import logging as gi_log
from .budget import get_today
from .config import get_settings
from .db import session as session_ctx
from .signals import MetaAdLibraryCollector
from .signals.base import run_collector

app = typer.Typer(no_args_is_help=True, add_completion=False)
collect_app = typer.Typer(no_args_is_help=True, help="Run a signal collector.")
worker_app = typer.Typer(no_args_is_help=True, help="Temporal worker control.")
workflow_app = typer.Typer(no_args_is_help=True, help="Trigger Temporal workflows.")
schedule_app = typer.Typer(no_args_is_help=True, help="Temporal schedule management.")
eval_app = typer.Typer(no_args_is_help=True, help="Run eval harness over golden brand set.")
notify_app = typer.Typer(no_args_is_help=True, help="Notifier diagnostics.")
app.add_typer(collect_app, name="collect")
app.add_typer(worker_app, name="worker")
app.add_typer(workflow_app, name="workflow")
app.add_typer(schedule_app, name="schedule")
app.add_typer(eval_app, name="eval")
app.add_typer(notify_app, name="notify")
console = Console()


@app.callback()
def _root() -> None:
    gi_log.configure()


@app.command()
def info() -> None:
    """Print resolved config (secrets redacted)."""
    s = get_settings()
    t = Table(title="grabon-intel config")
    t.add_column("key")
    t.add_column("value")
    t.add_row("database_url", _redact_db(s.database_url))
    t.add_row("meta_ad_library_token", "***" if s.meta_ad_library_token.get_secret_value() else "(unset)")
    t.add_row("meta_ad_library_base", s.meta_ad_library_base)
    t.add_row("daily_cost_cap_cents", str(s.daily_cost_cap_cents))
    t.add_row("paused", str(s.paused))
    t.add_row("log_level", s.log_level)
    console.print(t)


@app.command()
def budget() -> None:
    """Show today's spend / cap."""

    async def _go() -> tuple[int, int]:
        async with session_ctx() as ses:
            return await get_today(ses)

    spent, cap = asyncio.run(_go())
    console.print(f"spent={spent}c cap={cap}c remaining={cap - spent}c")


@collect_app.command("meta-ads")
def collect_meta_ads(
    search_terms: str = typer.Option(..., "--search-terms", "-q", help="search query"),
    country: str = typer.Option("IN", "--country", "-c"),
    limit: int = typer.Option(20, "--limit", "-n", help="max API pages"),
    page_size: int = typer.Option(100, "--page-size"),
    active_status: str = typer.Option("ACTIVE", "--active-status"),
) -> None:
    """Pull active Meta ads matching a query; emit ad_spend.meta.active signals."""
    if get_settings().paused:
        console.print("[yellow]GRABON_PAUSED=true — refusing to run[/]")
        raise typer.Exit(code=1)

    coll = MetaAdLibraryCollector(
        search_terms=search_terms,
        country=country,
        limit=limit,
        page_size=page_size,
        active_status=active_status,
    )
    result = asyncio.run(run_collector(coll))
    console.print_json(json.dumps(result))


def _redact_db(url: str) -> str:
    # Strip password from libpq-style URL.
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest and ":" in rest.split("@", 1)[0]:
        creds, host = rest.split("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return url


@worker_app.command("start")
def worker_start() -> None:
    """Start Temporal worker — long-running. Ctrl-C to stop."""
    from .worker import main as _serve

    _serve()


@workflow_app.command("run-discovery")
def workflow_run_discovery(
    collector: str = typer.Option(..., "--collector", "-c"),
    search_terms: str = typer.Option("", "--search-terms", "-q"),
    country: str = typer.Option("IN", "--country"),
    limit: int = typer.Option(20, "--limit", "-n"),
    fan_out: bool = typer.Option(False, "--fan-out", help="kick child DossierWF per new brand"),
    max_fan_out: int = typer.Option(10, "--max-fan-out"),
) -> None:
    """Execute DiscoveryWF synchronously via Temporal client."""
    from temporalio.client import Client, TLSConfig

    from .workflows import DiscoveryWF, DiscoveryWFInput

    params: dict[str, object] = {}
    if search_terms:
        params["search_terms"] = search_terms
    if country:
        params["country"] = country
    if limit:
        params["limit"] = limit

    s = get_settings()

    async def _go() -> None:
        client = await Client.connect(
            s.temporal_address,
            namespace=s.temporal_namespace,
            tls=TLSConfig() if s.temporal_tls else False,
        )
        result = await client.execute_workflow(
            DiscoveryWF.run,
            DiscoveryWFInput(
                collector=collector,
                params=params,
                fan_out_dossiers=fan_out,
                max_fan_out=max_fan_out,
            ),
            id=f"discovery-{collector}-{int(__import__('time').time())}",
            task_queue=s.temporal_task_queue,
        )
        console.print_json(json.dumps(result.__dict__))

    asyncio.run(_go())


@workflow_app.command("run-dossier")
def workflow_run_dossier(
    brand_id: int = typer.Option(..., "--brand-id", "-b"),
    reason: str = typer.Option("manual", "--reason"),
) -> None:
    from temporalio.client import Client, TLSConfig

    from .workflows import DossierWF, DossierWFInput

    s = get_settings()

    async def _go() -> None:
        client = await Client.connect(
            s.temporal_address,
            namespace=s.temporal_namespace,
            tls=TLSConfig() if s.temporal_tls else False,
        )
        result = await client.execute_workflow(
            DossierWF.run,
            DossierWFInput(brand_id=brand_id, reason=reason),
            id=f"dossier-{brand_id}-{int(__import__('time').time())}",
            task_queue=s.temporal_task_queue,
        )
        console.print_json(json.dumps(result.__dict__))

    asyncio.run(_go())


@schedule_app.command("bootstrap")
def schedule_bootstrap() -> None:
    """Create/update all Temporal schedules for autonomous operation."""
    from .schedules import run_bootstrap

    results = run_bootstrap()
    t = Table(title="Schedule Bootstrap Results")
    t.add_column("Schedule ID")
    t.add_column("Status")
    for sid, status in results.items():
        style = "green" if status in ("created", "updated") else "red"
        t.add_row(sid, f"[{style}]{status}[/]")
    console.print(t)


@schedule_app.command("list")
def schedule_list() -> None:
    """List all active Temporal schedules."""
    from temporalio.client import Client, TLSConfig

    s = get_settings()

    async def _go() -> list[dict[str, str]]:
        client = await Client.connect(
            s.temporal_address,
            namespace=s.temporal_namespace,
            tls=TLSConfig() if s.temporal_tls else False,
        )
        schedules = []
        async for sch in await client.list_schedules():
            wf_type = getattr(sch.schedule.action, "workflow", "?")
            schedules.append({"id": sch.id, "workflow": wf_type})
        return schedules

    rows = asyncio.run(_go())
    t = Table(title="Active Schedules")
    t.add_column("ID")
    t.add_column("Workflow")
    for r in rows:
        t.add_row(r["id"], r["workflow"])
    console.print(t)


@eval_app.command("run")
def eval_run(
    file: str = typer.Option(..., "--file", "-f", help="path to golden YAML"),
    out: str = typer.Option("", "--out", "-o", help="optional JSON report path"),
) -> None:
    from .eval import load_golden, run_eval

    async def _go() -> None:
        cases = load_golden(file)
        report = await run_eval(cases)
        payload = {
            "started_at": report.started_at,
            "finished_at": report.finished_at,
            "n_cases": report.n_cases,
            "n_passed": report.n_passed,
            "pass_rate": report.pass_rate,
            "total_cost_cents": report.total_cost_cents,
            "records": [r.__dict__ for r in report.records],
        }
        console.print_json(json.dumps(payload))
        if out:
            from pathlib import Path

            Path(out).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    asyncio.run(_go())


@notify_app.command("digest")
def notify_digest(period_hours: int = typer.Option(24, "--period-hours")) -> None:
    """Send a one-shot digest now (bypasses Temporal)."""
    from .notify.digest import send_digest

    result = asyncio.run(send_digest(period_hours))
    console.print_json(json.dumps(result))


@notify_app.command("test")
def notify_test(
    title: str = typer.Option("Grabon Intel test", "--title"),
    summary: str = typer.Option("If you can read this, the notifier works.", "--summary"),
) -> None:
    """Send a test ping through every available notifier."""
    from .notify.base import Notification, get_notifiers

    note = Notification(title=title, summary=summary, facts=[("env", "test")], actions=[])

    async def _go() -> dict:
        results: dict[str, bool] = {}
        for n in get_notifiers():
            results[n.name] = await n.send(note)
        return results

    console.print_json(json.dumps(asyncio.run(_go())))


if __name__ == "__main__":
    app()
