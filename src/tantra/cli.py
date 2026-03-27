"""
Tantra AI — CLI entry point
तंत्र  ·  Command-line interface

Usage:
    tantra --help
    tantra run "Research AI trends and draft a LinkedIn post"
    tantra crew social "Write 3 LinkedIn posts this week"
    tantra memory search "What content works best on LinkedIn?"
    tantra serve
"""
from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="tantra",
    help="तंत्र (Tantra AI) — Local Autonomous Agent Intelligence Stack",
    add_completion=False,
    rich_markup_mode="rich",
)

console = Console()


def _banner() -> None:
    console.print(Panel(
        "[bold cyan]तंत्र (Tantra AI)[/bold cyan]\n"
        "[dim]Local Autonomous Agent Intelligence Stack[/dim]",
        border_style="cyan",
    ))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def run(
    task: str = typer.Argument(..., help="Natural language task description"),
    model: str = typer.Option("director", "--model", "-m", help="Model tier: frontier/director/manager/worker"),
    agent: str = typer.Option("Tantra", "--agent", "-a", help="Agent name"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show reasoning steps"),
) -> None:
    """Run a single agent task and print the result."""
    _banner()
    console.print(f"\n[cyan]Task:[/cyan] {task}")
    console.print(f"[cyan]Model:[/cyan] {model}  |  [cyan]Agent:[/cyan] {agent}\n")

    async def _run() -> None:
        from tantra.agents.worker import WorkerAgent
        from tantra.core.config import ModelTier

        try:
            tier = ModelTier(model)
        except ValueError:
            console.print(f"[red]Unknown model tier: {model}[/red]")
            console.print(f"Valid tiers: {[t.value for t in ModelTier]}")
            raise typer.Exit(1)

        worker = WorkerAgent(
            name=agent,
            role="General Purpose Agent",
            goal="Execute tasks accurately and produce actionable results",
            model_tier=tier,
            verbose=verbose,
        )
        with console.status("[cyan]Thinking...[/cyan]"):
            result = await worker.execute(task)

        if result.success:
            console.print(Panel(result.output, title="[green]Result[/green]", border_style="green"))
        else:
            console.print(f"[red]Failed:[/red] {result.error}")

    asyncio.run(_run())


@app.command()
def crew(
    domain: str = typer.Argument("social", help="Crew domain: social"),
    task: str = typer.Argument(..., help="Task for the crew to execute"),
    verbose: bool = typer.Option(True, "--verbose/--quiet", "-v/-q"),
) -> None:
    """Run a full CrewAI crew for a domain task."""
    _banner()
    console.print(f"\n[cyan]Crew:[/cyan] {domain}  |  [cyan]Task:[/cyan] {task}\n")

    if domain == "social":
        from tantra.crews.social_crew import build_social_media_crew
        with console.status("[cyan]Assembling social media crew...[/cyan]"):
            c = build_social_media_crew(verbose=verbose)
        console.print("[green]Crew assembled. Running...[/green]\n")
        result = c.kickoff(inputs={"task": task})
        console.print(Panel(str(result), title="[green]Crew Result[/green]", border_style="green"))
    else:
        console.print(f"[red]Unknown domain: {domain}[/red]. Available: social")
        raise typer.Exit(1)


@app.command()
def memory(
    action: str = typer.Argument(..., help="Action: save | search"),
    text: str = typer.Argument(..., help="Text to save or query to search"),
    namespace: str = typer.Option("default", "--ns", help="Memory namespace"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of results"),
) -> None:
    """Save or search agent memory."""
    _banner()

    async def _run() -> None:
        from tantra.memory.manager import MemoryManager
        mem = MemoryManager(namespace=namespace)
        await mem.init()

        if action == "save":
            pid = await mem.save(content=text)
            console.print(f"[green]✓[/green] Saved to namespace [cyan]{namespace}[/cyan] (id: {pid})")

        elif action == "search":
            results = await mem.search(query=text, top_k=top_k)
            if not results:
                console.print("[yellow]No memories found.[/yellow]")
                return
            table = Table(title=f"Memory search: {text[:60]}", show_lines=True)
            table.add_column("Score", style="cyan", width=8)
            table.add_column("Content", style="white")
            for r in results:
                table.add_row(f"{r['score']:.3f}", r["content"])
            console.print(table)
        else:
            console.print(f"[red]Unknown action: {action}[/red]. Use: save | search")

    asyncio.run(_run())


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Port number"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes"),
    workers: int = typer.Option(1, "--workers", "-w", help="Number of uvicorn workers"),
) -> None:
    """Start the Tantra API server."""
    import uvicorn
    _banner()
    console.print(f"\n[cyan]Starting API server on {host}:{port}...[/cyan]\n")
    uvicorn.run(
        "tantra.main:app",
        host=host,
        port=port,
        reload=reload,
        workers=workers if not reload else 1,
        log_level="info",
    )


@app.command()
def status() -> None:
    """Check the status of all Tantra services."""
    import httpx
    from tantra.core.config import settings

    _banner()
    console.print("\n[cyan]Service Status[/cyan]\n")

    services = {
        "Tantra API": f"http://localhost:8000/health",
        "LiteLLM Proxy": f"{settings.litellm_base_url}/health",
        "Ollama": "http://localhost:11434/api/tags",
        "Qdrant": "http://localhost:6333/healthz",
        "Open WebUI": "http://localhost:3000",
        "n8n": "http://localhost:5678",
    }

    table = Table(show_header=True)
    table.add_column("Service", style="cyan")
    table.add_column("URL", style="dim")
    table.add_column("Status", justify="center")

    for name, url in services.items():
        try:
            resp = httpx.get(url, timeout=3)
            status_str = "[green]● UP[/green]" if resp.status_code < 400 else f"[yellow]● {resp.status_code}[/yellow]"
        except Exception:
            status_str = "[red]● DOWN[/red]"
        table.add_row(name, url, status_str)

    console.print(table)
    console.print()


if __name__ == "__main__":
    app()
