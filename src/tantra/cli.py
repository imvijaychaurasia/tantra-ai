"""
Tantra AI — CLI entry point
तंत्र  ·  Command-line interface

Usage:
    tantra --help
    tantra run "Research AI trends and draft a LinkedIn post"
    tantra crew social "Write 3 LinkedIn posts this week"
    tantra memory search "What content works best on LinkedIn?"
    tantra skills list
    tantra skills install linkedin-human-post
    tantra skills info linkedin-human-post
    tantra plugins list
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

# Sub-apps
skills_app = typer.Typer(
    name="skills",
    help="Manage Tantra AI skills (SKILL.md bundles)",
    add_completion=False,
    rich_markup_mode="rich",
)
plugins_app = typer.Typer(
    name="plugins",
    help="Manage Tantra AI plugins (PLUGIN.md bundles)",
    add_completion=False,
    rich_markup_mode="rich",
)

task_app = typer.Typer(
    name="task",
    help="Trigger and inspect Celery tasks",
    add_completion=False,
    rich_markup_mode="rich",
)

app.add_typer(skills_app, name="skills")
app.add_typer(plugins_app, name="plugins")
app.add_typer(task_app, name="task")

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


# ---------------------------------------------------------------------------
# Skills subcommands  —  tantra skills <action>
# ---------------------------------------------------------------------------

@skills_app.command("list")
def skills_list(
    all_dirs: bool = typer.Option(False, "--all", "-a", help="Include user-installed + builtins"),
) -> None:
    """List all available (loaded) skills."""
    from tantra.skills.loader import get_loader

    _banner()
    loader = get_loader()
    skills = loader.list()

    if not skills:
        console.print("[yellow]No skills loaded.[/yellow]")
        raise typer.Exit(0)

    table = Table(title="Tantra Skills", show_header=True, show_lines=True)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Version", style="dim", width=8)
    table.add_column("Category", style="yellow", width=12)
    table.add_column("Platform", style="magenta", width=12)
    table.add_column("Description", style="white")

    for s in skills:
        table.add_row(
            s.name,
            s.version,
            s.category or "—",
            s.platform or "any",
            s.description[:70] + ("…" if len(s.description) > 70 else ""),
        )

    console.print(table)
    console.print(f"\n[dim]{len(skills)} skill(s) loaded.[/dim]")


@skills_app.command("info")
def skills_info(
    name: str = typer.Argument(..., help="Skill name"),
) -> None:
    """Show full details and instructions for a skill."""
    from tantra.skills.loader import get_loader

    _banner()
    loader = get_loader()
    skill = loader.get(name)
    if skill is None:
        console.print(f"[red]Skill not found:[/red] {name}")
        raise typer.Exit(1)

    console.print(Panel(
        f"[bold cyan]{skill.name}[/bold cyan]  v{skill.version}\n"
        f"[dim]{skill.description}[/dim]\n\n"
        f"[yellow]Category:[/yellow] {skill.category or '—'}   "
        f"[yellow]Platform:[/yellow] {skill.platform or 'any'}   "
        f"[yellow]Author:[/yellow] {skill.author or '—'}\n"
        f"[yellow]Tags:[/yellow] {', '.join(skill.tags) if skill.tags else '—'}\n"
        f"[yellow]Source dir:[/yellow] {skill.skill_dir}",
        title="Skill Info",
        border_style="cyan",
    ))
    if skill.instructions:
        console.print(Panel(
            skill.instructions,
            title="[green]Instructions[/green]",
            border_style="green",
        ))
    else:
        console.print("[yellow]No instructions found in this skill.[/yellow]")


@skills_app.command("install")
def skills_install(
    source: str = typer.Argument(..., help="Skill slug, GitHub URL, or local path"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if already installed"),
) -> None:
    """Install a skill from the TantraHub, GitHub, or a local path."""
    from pathlib import Path
    from tantra.skills.installer import install as _install

    _banner()
    console.print(f"[cyan]Installing skill:[/cyan] {source}")
    with console.status("[cyan]Installing...[/cyan]"):
        try:
            result = _install(source=source, overwrite=force)
        except FileExistsError:
            console.print(
                f"[yellow]Skill already installed.[/yellow] Use [cyan]--force[/cyan] to overwrite."
            )
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]Install failed:[/red] {exc}")
            raise typer.Exit(1)

    console.print(f"[green]✓[/green] Installed [cyan]{result.get('name', source)}[/cyan]")


@skills_app.command("uninstall")
def skills_uninstall(
    name: str = typer.Argument(..., help="Skill name to remove"),
) -> None:
    """Uninstall a user-installed skill."""
    from tantra.skills.installer import uninstall as _uninstall

    _banner()
    removed = _uninstall(name)
    if removed:
        console.print(f"[green]✓[/green] Uninstalled [cyan]{name}[/cyan]")
    else:
        console.print(f"[yellow]Skill not found (or it's a builtin):[/yellow] {name}")


@skills_app.command("search")
def skills_search(
    query: str = typer.Argument(..., help="Search term (name, description, tag)"),
) -> None:
    """Search loaded skills by name, description, or tag."""
    from tantra.skills.loader import get_loader

    _banner()
    loader = get_loader()
    q = query.lower()
    results = [
        s for s in loader.list()
        if q in s.name.lower()
        or q in (s.description or "").lower()
        or any(q in t.lower() for t in (s.tags or []))
    ]

    if not results:
        console.print(f"[yellow]No skills match:[/yellow] {query}")
        raise typer.Exit(0)

    table = Table(title=f"Skills matching '{query}'", show_header=True, show_lines=True)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Category", style="yellow", width=12)
    table.add_column("Description", style="white")
    for s in results:
        table.add_row(
            s.name,
            s.category or "—",
            s.description[:80] + ("…" if len(s.description) > 80 else ""),
        )
    console.print(table)


@skills_app.command("prompt")
def skills_prompt(
    name: str = typer.Argument(..., help="Skill name"),
) -> None:
    """Print the raw system-prompt block for a skill (as injected into agents)."""
    from tantra.skills.loader import get_loader

    _banner()
    loader = get_loader()
    skill = loader.get(name)
    if skill is None:
        console.print(f"[red]Skill not found:[/red] {name}")
        raise typer.Exit(1)

    block = loader.build_system_prompt_block([name])
    if block:
        console.print(Panel(block, title=f"Prompt block — {name}", border_style="cyan"))
    else:
        console.print("[yellow]No instructions to show.[/yellow]")


# ---------------------------------------------------------------------------
# Plugins subcommands  —  tantra plugins <action>
# ---------------------------------------------------------------------------

@plugins_app.command("list")
def plugins_list() -> None:
    """List all registered plugins (builtin + installed)."""
    from tantra.plugins.registry import PluginRegistry

    _banner()
    reg = PluginRegistry()
    plugins = reg.list_all()

    if not plugins:
        console.print("[yellow]No plugins found.[/yellow]")
        raise typer.Exit(0)

    table = Table(title="Tantra Plugins", show_header=True, show_lines=True)
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Version", style="dim", width=8)
    table.add_column("Source", style="yellow", width=10)
    table.add_column("Enabled", style="green", width=8)
    table.add_column("Description", style="white")

    for p in plugins:
        enabled_str = "[green]✓[/green]" if p.get("enabled", True) else "[red]✗[/red]"
        table.add_row(
            p.get("name", "?"),
            p.get("version", "?"),
            p.get("source", "—"),
            enabled_str,
            (p.get("description") or "")[:70],
        )

    console.print(table)
    console.print(f"\n[dim]{len(plugins)} plugin(s).[/dim]")


@plugins_app.command("info")
def plugins_info(
    name: str = typer.Argument(..., help="Plugin name"),
) -> None:
    """Show details for a plugin."""
    from tantra.plugins.registry import PluginRegistry

    _banner()
    reg = PluginRegistry()
    plugins = {p["name"]: p for p in reg.list_all()}
    p = plugins.get(name)
    if p is None:
        console.print(f"[red]Plugin not found:[/red] {name}")
        raise typer.Exit(1)

    caps = p.get("capabilities") or {}
    console.print(Panel(
        f"[bold cyan]{p['name']}[/bold cyan]  v{p.get('version', '?')}\n"
        f"[dim]{p.get('description', '')}[/dim]\n\n"
        f"[yellow]Source:[/yellow] {p.get('source', '—')}   "
        f"[yellow]Enabled:[/yellow] {'yes' if p.get('enabled', True) else 'no'}\n"
        f"[yellow]Author:[/yellow] {p.get('author', '—')}\n\n"
        f"[yellow]Capabilities:[/yellow]\n"
        + "\n".join(
            f"  [cyan]{k}:[/cyan] {v}"
            for k, v in (caps.items() if isinstance(caps, dict) else {})
        ),
        title="Plugin Info",
        border_style="cyan",
    ))


@plugins_app.command("install")
def plugins_install(
    path: str = typer.Argument(..., help="Local path to plugin directory containing PLUGIN.md"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if already installed"),
) -> None:
    """Install a plugin from a local directory path."""
    from pathlib import Path
    from tantra.plugins.registry import PluginRegistry

    _banner()
    src = Path(path).expanduser().resolve()
    if not src.exists():
        console.print(f"[red]Path not found:[/red] {src}")
        raise typer.Exit(1)

    console.print(f"[cyan]Installing plugin from:[/cyan] {src}")
    try:
        reg = PluginRegistry()
        result = reg.install_from_path(src, overwrite=force)
        console.print(f"[green]✓[/green] Installed plugin [cyan]{result['name']}[/cyan] v{result['version']}")
    except FileExistsError:
        console.print(
            "[yellow]Plugin already installed.[/yellow] Use [cyan]--force[/cyan] to overwrite."
        )
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Install failed:[/red] {exc}")
        raise typer.Exit(1)


@plugins_app.command("uninstall")
def plugins_uninstall(
    name: str = typer.Argument(..., help="Plugin name"),
) -> None:
    """Uninstall a user-installed plugin."""
    from tantra.plugins.registry import PluginRegistry

    _banner()
    reg = PluginRegistry()
    removed = reg.uninstall(name)
    if removed:
        console.print(f"[green]✓[/green] Uninstalled plugin [cyan]{name}[/cyan]")
    else:
        console.print(f"[yellow]Plugin not found (or it's a builtin):[/yellow] {name}")


# ---------------------------------------------------------------------------
# Task subcommands  —  tantra task <action>
# ---------------------------------------------------------------------------

def _resolve_task(name: str):
    """
    Resolve a short task name (e.g. 'research_and_draft_posts') or a full
    dotted path (e.g. 'tantra.tasks.social.research_and_draft_posts') to the
    registered Celery task object.  Returns (task, full_name) or (None, None).
    """
    from tantra.tasks.celery_app import app as celery_app

    registered = celery_app.tasks

    # Exact match first
    if name in registered:
        return registered[name], name

    # Suffix match — find any registered task whose dotted name ends with '.<name>'
    matches = [k for k in registered if k.endswith(f".{name}") and not k.startswith("celery.")]
    if len(matches) == 1:
        return registered[matches[0]], matches[0]
    if len(matches) > 1:
        return None, f"Ambiguous: {matches}"

    return None, None


@task_app.command("list")
def task_list() -> None:
    """List all registered Celery tasks (excluding built-in Celery internals)."""
    from tantra.tasks.celery_app import app as celery_app

    _banner()
    tasks = sorted(
        k for k in celery_app.tasks
        if not k.startswith("celery.")
    )

    if not tasks:
        console.print("[yellow]No tasks registered.[/yellow]")
        raise typer.Exit(0)

    table = Table(title="Registered Celery Tasks", show_header=True, show_lines=False)
    table.add_column("Short name", style="cyan", no_wrap=True)
    table.add_column("Full dotted path", style="dim")

    for full in tasks:
        short = full.rsplit(".", 1)[-1]
        table.add_row(short, full)

    console.print(table)
    console.print(f"\n[dim]{len(tasks)} task(s) registered.[/dim]")
    console.print(
        "\n[dim]Run with:[/dim]  [cyan]tantra task run <short-name>[/cyan]"
    )


@task_app.command("run")
def task_run(
    name: str = typer.Argument(..., help="Task short name or full dotted path"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Block until task completes and print result"),
    timeout: int = typer.Option(120, "--timeout", "-t", help="Seconds to wait (only with --wait)"),
    kwargs_json: str = typer.Option("{}", "--kwargs", "-k", help="JSON kwargs to pass to the task"),
) -> None:
    """
    Fire a registered Celery task.

    Examples:\n
      tantra task run research_and_draft_posts\n
      tantra task run post_tantra_progress --wait\n
      tantra task run publish_approved_linkedin_posts --wait --timeout 30
    """
    import json

    _banner()

    task, full_name = _resolve_task(name)
    if task is None:
        if full_name and full_name.startswith("Ambiguous"):
            console.print(f"[red]Ambiguous task name:[/red] {full_name}")
        else:
            console.print(f"[red]Task not found:[/red] {name}")
            console.print("[dim]Run [cyan]tantra task list[/cyan] to see available tasks.[/dim]")
        raise typer.Exit(1)

    try:
        kw = json.loads(kwargs_json)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid --kwargs JSON:[/red] {exc}")
        raise typer.Exit(1)

    console.print(f"[cyan]Dispatching:[/cyan] {full_name}")
    if kw:
        console.print(f"[cyan]kwargs:[/cyan] {kw}")

    result = task.delay(**kw)
    console.print(f"[green]✓[/green] Queued  task_id=[cyan]{result.id}[/cyan]")

    if not wait:
        console.print(
            f"\n[dim]Check status:[/dim]  [cyan]tantra task status {result.id}[/cyan]"
        )
        return

    console.print(f"\n[dim]Waiting up to {timeout}s for result…[/dim]")
    try:
        with console.status("[cyan]Running...[/cyan]"):
            value = result.get(timeout=timeout)
        console.print(Panel(
            str(value),
            title="[green]Task Result[/green]",
            border_style="green",
        ))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Task failed or timed out:[/red] {exc}")
        raise typer.Exit(1)


@task_app.command("status")
def task_status(
    task_id: str = typer.Argument(..., help="Celery task UUID returned by 'tantra task run'"),
) -> None:
    """Check the current state and result of a dispatched task."""
    from celery.result import AsyncResult
    from tantra.tasks.celery_app import app as celery_app

    _banner()
    result = AsyncResult(task_id, app=celery_app)

    state_colour = {
        "PENDING": "yellow",
        "STARTED": "cyan",
        "SUCCESS": "green",
        "FAILURE": "red",
        "RETRY":   "yellow",
        "REVOKED": "dim",
    }
    colour = state_colour.get(result.state, "white")

    console.print(f"[cyan]Task ID:[/cyan]  {task_id}")
    console.print(f"[cyan]State:[/cyan]    [{colour}]{result.state}[/{colour}]")

    if result.state == "SUCCESS":
        console.print(Panel(str(result.result), title="[green]Result[/green]", border_style="green"))
    elif result.state == "FAILURE":
        console.print(f"[red]Error:[/red] {result.result}")
        if result.traceback:
            console.print(Panel(result.traceback, title="Traceback", border_style="red"))
    elif result.state == "PENDING":
        console.print("[dim]Task is waiting in queue or result has expired.[/dim]")


if __name__ == "__main__":
    app()
