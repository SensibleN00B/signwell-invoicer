"""CLI entrypoint — Typer-based.

Commands:
  invoicer check                    — sanity-check API key + clients.yaml
  invoicer send <pdf>               — send invoice for signature
  invoicer status <document_id>     — fetch current SignWell status
  invoicer list                     — show recent sent invoices
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from invoicer.config import ClientsRegistry, Mode, Settings, infer_client_key
from invoicer.sender import send_invoice
from invoicer.signwell import SignWellClient, SignWellError
from invoicer.tracking import Tracker

app = typer.Typer(
    help="Local CLI for sending invoices to clients via SignWell.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


# --- Helpers ---------------------------------------------------------------------


def _load_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]  # pydantic-settings reads env
    except Exception as e:
        err_console.print(f"[bold red]Config error:[/] {e}")
        err_console.print(
            "\n[dim]Copy .env.example to .env and fill in SIGNWELL_API_KEY[/]"
        )
        raise typer.Exit(code=2) from None


def _resolve_mode(test_flag: bool | None, prod_flag: bool | None, settings: Settings) -> bool:
    """Returns True if test_mode should be enabled.

    --test / --prod override the default from env. Fail if both are set.
    """
    if test_flag and prod_flag:
        err_console.print("[bold red]Cannot use --test and --prod together.[/]")
        raise typer.Exit(code=2)
    if test_flag:
        return True
    if prod_flag:
        return False
    return settings.default_mode == Mode.TEST


def _format_row(row: dict) -> list[str]:
    return [
        row["document_id"][:12] + "...",
        row["client_key"],
        "TEST" if row["test_mode"] else "PROD",
        row["status"],
        row["created_at"][:19].replace("T", " "),
    ]


# --- Commands --------------------------------------------------------------------


@app.command()
def check() -> None:
    """Verify API key works and clients.yaml is valid."""
    settings = _load_settings()

    console.print(f"[dim]Clients registry:[/] {settings.clients_path}")
    try:
        registry = ClientsRegistry.load(settings.clients_path)
    except Exception as e:
        err_console.print(f"[bold red]Clients registry error:[/] {e}")
        raise typer.Exit(code=2) from None
    console.print(f"[green]✓[/] Loaded {len(registry.clients)} client(s): "
                  f"{', '.join(sorted(registry.clients))}")

    console.print(f"\n[dim]Default mode:[/] {settings.default_mode.value}")
    console.print(f"[dim]SignWell API:[/] verifying credentials...")
    try:
        with SignWellClient(settings.signwell_api_key) as sw:
            me = sw.me()
    except SignWellError as e:
        err_console.print(f"[bold red]API error:[/] {e}")
        raise typer.Exit(code=1) from None

    name = me.get("name") or me.get("email") or "(unknown account)"
    console.print(f"[green]✓[/] Authenticated as [bold]{name}[/]")


@app.command()
def send(
    pdf: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True,
                                         help="Path to invoice PDF")],
    client: Annotated[
        Optional[str],
        typer.Option("--client", "-c", help="Override client key (otherwise inferred from filename)"),
    ] = None,
    test: Annotated[
        Optional[bool], typer.Option("--test", help="Force test mode (free, not legally binding)")
    ] = None,
    prod: Annotated[
        Optional[bool], typer.Option("--prod", help="Force production mode (real, billable)")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print payload and exit; don't call API")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Send even if the same file was already sent")
    ] = False,
) -> None:
    """Send an invoice PDF to the client for signature."""
    settings = _load_settings()
    test_mode = _resolve_mode(test, prod, settings)
    registry = ClientsRegistry.load(settings.clients_path)

    # --- Resolve client ---
    client_key = client or infer_client_key(pdf.name, registry)
    if client_key is None:
        err_console.print(
            f"[bold red]Could not infer client from filename[/] '{pdf.name}'.\n"
            f"Pass --client <key> explicitly. Available: "
            f"{', '.join(sorted(registry.clients))}"
        )
        raise typer.Exit(code=2)
    try:
        client_obj = registry.get(client_key)
    except KeyError as e:
        err_console.print(f"[bold red]{e}[/]")
        raise typer.Exit(code=2) from None

    # --- Preview ---
    mode_label = "[yellow]TEST[/]" if test_mode else "[bold red]PRODUCTION[/]"
    console.print(Panel.fit(
        f"[bold]{pdf.name}[/]\n"
        f"→ {client_obj.name} <{client_obj.email}>\n"
        f"  client key:  {client_key}\n"
        f"  cc:          {', '.join(client_obj.cc) or '(none)'}\n"
        f"  language:    {client_obj.language}\n"
        f"  mode:        {mode_label}",
        title="Invoice to send",
        border_style="cyan",
    ))

    if dry_run:
        # Build payload but don't send, for inspection.
        from invoicer.sender import build_payload
        payload = build_payload(
            pdf_path=pdf, client=client_obj, settings=settings,
            test_mode=test_mode, draft=True,
        )
        dumped = payload.model_dump(exclude_none=True, mode="json")
        # Don't dump the huge base64; replace with a marker.
        for f in dumped.get("files", []):
            if "file_base64" in f:
                f["file_base64"] = f"<base64: {len(f['file_base64'])} chars>"
        console.print_json(data=dumped)
        console.print("\n[dim]--dry-run: API not called.[/]")
        return

    if not test_mode:
        # Extra confirmation before spending real documents and real email.
        confirm = typer.confirm(
            "Mode is PRODUCTION — this will email the client a real signature request. Continue?",
            default=False,
        )
        if not confirm:
            console.print("[yellow]Aborted.[/]")
            raise typer.Exit(code=1)

    # --- Send ---
    tracker = Tracker(settings.db_path)
    try:
        result = send_invoice(
            pdf_path=pdf,
            client_key=client_key,
            client=client_obj,
            settings=settings,
            tracker=tracker,
            test_mode=test_mode,
            force=force,
        )
    except SignWellError as e:
        err_console.print(f"[bold red]SignWell error:[/] {e}")
        raise typer.Exit(code=1) from None

    if result.already_sent:
        console.print(
            f"[yellow]⚠ This file was already sent.[/] "
            f"Document: {result.document_id}\n"
            f"  URL: {result.status_url}\n"
            f"  Use --force to send again."
        )
    else:
        console.print(f"[green]✓ Sent![/] Document: {result.document_id}")
        console.print(f"  URL: {result.status_url}")


@app.command()
def status(
    document_id: Annotated[str, typer.Argument(help="SignWell document ID")],
) -> None:
    """Fetch current signing status from SignWell."""
    settings = _load_settings()
    tracker = Tracker(settings.db_path)
    try:
        with SignWellClient(settings.signwell_api_key) as sw:
            doc = sw.get_document(document_id)
    except SignWellError as e:
        err_console.print(f"[bold red]{e}[/]")
        raise typer.Exit(code=1) from None

    status_value = doc.get("status", "unknown")
    console.print(f"[bold]Status:[/] {status_value}")
    console.print(f"[dim]Name:[/] {doc.get('name')}")
    for r in doc.get("recipients", []):
        signing_url = r.get("signing_url", "")
        console.print(
            f"  • {r.get('name')} <{r.get('email')}>: {r.get('status', '?')}"
            + (f"\n    Signing URL: {signing_url}" if signing_url else "")
        )
    if doc.get("completed_pdf_url"):
        console.print(f"[green]Completed PDF:[/] {doc['completed_pdf_url']}")

    # Sync local tracker status
    if status_value.lower() in ("completed", "declined", "cancelled"):
        tracker.update_status(document_id, status_value.lower())


@app.command("list")
def list_cmd(
    all_: Annotated[bool, typer.Option("--all", help="Show all, not just pending")] = False,
    limit: Annotated[int, typer.Option("--limit", help="Max rows")] = 30,
) -> None:
    """List locally-tracked sent invoices."""
    settings = _load_settings()
    tracker = Tracker(settings.db_path)
    rows = tracker.list_all(limit) if all_ else tracker.list_pending()

    if not rows:
        console.print("[dim](none)[/]")
        return

    table = Table(title="Sent invoices" if all_ else "Pending invoices")
    for col in ("Document ID", "Client", "Mode", "Status", "Created"):
        table.add_column(col)
    for row in rows:
        table.add_row(*_format_row(dict(row)))
    console.print(table)


if __name__ == "__main__":
    app()
