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
from pathlib import Path
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
    elif domain == "youtube":
        from tantra.crews.youtube_crew import build_youtube_crew, parse_script_output
        with console.status("[cyan]Assembling YouTube script crew...[/cyan]"):
            c = build_youtube_crew(topic_hint=task, verbose=verbose)
        console.print("[green]YouTube crew assembled. Running...[/green]\n")
        result = c.kickoff()
        raw = str(result.raw if hasattr(result, "raw") else result)
        script = parse_script_output(raw)
        if script:
            import json as _json
            console.print(Panel(
                _json.dumps(script, indent=2)[:3000],
                title="[green]YouTube Script (JSON)[/green]",
                border_style="green",
            ))
        else:
            console.print(Panel(raw[:2000], title="[yellow]Raw Output[/yellow]", border_style="yellow"))
    else:
        console.print(f"[red]Unknown domain: {domain}[/red]. Available: social, youtube")
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

    # Each entry: (display_name, browser_url, internal_check_url)
    # CLI runs inside the tantra-api container — must use Docker service names
    # for inter-container HTTP.  Browser URLs (localhost:PORT) are shown to the
    # user but are NOT reachable from inside the container.
    services = [
        (
            "Tantra API",
            "http://localhost:8000",
            "http://localhost:8000/health",        # self — localhost OK
        ),
        (
            "LiteLLM Proxy",
            "http://localhost:4000",
            "http://litellm:4000/health/liveliness",  # no auth required
        ),
        (
            "Ollama",
            "http://localhost:11434",
            "http://ollama:11434/api/tags",
        ),
        (
            "Qdrant",
            "http://localhost:6333",
            "http://qdrant:6333/healthz",
        ),
        (
            "Open WebUI",
            "http://localhost:3000",
            "http://open-webui:8080/health",       # internal port is 8080
        ),
        (
            "n8n",
            "http://localhost:5678",
            "http://n8n:5678",
        ),
        (
            "Flower",
            "http://localhost:5555",
            "http://flower:5555",
        ),
        (
            "Terminal (ttyd)",
            "http://localhost:7681",
            "http://ttyd:7681",
        ),
    ]

    table = Table(show_header=True)
    table.add_column("Service", style="cyan")
    table.add_column("Browser URL", style="dim")
    table.add_column("Status", justify="center")

    for name, browser_url, check_url in services:
        try:
            resp = httpx.get(check_url, timeout=3)
            if resp.status_code < 400:
                status_str = "[green]● UP[/green]"
            elif resp.status_code == 401:
                # Auth required but service is alive
                status_str = "[green]● UP[/green] [dim](auth)[/dim]"
            else:
                status_str = f"[yellow]● {resp.status_code}[/yellow]"
        except Exception:
            status_str = "[red]● DOWN[/red]"
        table.add_row(name, browser_url, status_str)

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

def _load_all_task_modules() -> None:
    """
    Eagerly import every module listed in celery_app.conf.include.

    Celery's include= setting is processed by worker processes on startup.
    The CLI and API server are NOT workers, so included modules are never
    imported automatically — tasks defined in those modules are invisible
    to celery_app.tasks until we import them here.
    """
    import importlib
    from tantra.tasks.celery_app import app as celery_app

    for module_path in celery_app.conf.include or []:
        try:
            importlib.import_module(module_path)
        except Exception:  # noqa: BLE001
            pass  # skip broken modules gracefully (logged at debug level)


def _resolve_task(name: str):
    """
    Resolve a short task name (e.g. 'research_and_draft_posts') or a full
    dotted path (e.g. 'tantra.tasks.social.research_and_draft_posts') to the
    registered Celery task object.  Returns (task, full_name) or (None, None).
    """
    from tantra.tasks.celery_app import app as celery_app

    _load_all_task_modules()

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
    _load_all_task_modules()  # ensure included task modules are imported
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


# ---------------------------------------------------------------------------
# `tantra director` sub-commands  (Phase 2)
# ---------------------------------------------------------------------------

director_app = typer.Typer(
    name="director",
    help="Director planning engine — weekly plans, agent tasks, reviews",
    add_completion=False,
    rich_markup_mode="rich",
)
app.add_typer(director_app, name="director")


@director_app.command("status")
def director_status() -> None:
    """Show the active weekly plan and all agent tasks."""
    import json
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from tantra.core.config import settings
    from tantra.tasks.celery_app import app as celery_app  # noqa: ensure tasks imported

    _banner()

    engine = create_engine(settings.database_sync_url, echo=False)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        try:
            from tantra.db.director import AgentTask, WeeklyPlan
        except ImportError as e:
            console.print(f"[red]DB models not available:[/red] {e}")
            raise typer.Exit(1)

        plan = session.execute(
            select(WeeklyPlan)
            .where(WeeklyPlan.status == "active")
            .order_by(WeeklyPlan.week_start.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not plan:
            console.print("[yellow]No active weekly plan.[/yellow]")
            console.print("[dim]Run: tantra task run director_weekly_planning[/dim]")
            return

        goals = plan.goals or {}
        console.print(Panel(
            f"[bold]Week {plan.week_number}/{plan.year}[/bold]  —  "
            f"starts {plan.week_start}\n\n"
            f"[cyan]Primary topic:[/cyan] {goals.get('primary_topic', '?')}\n"
            f"[cyan]LinkedIn target:[/cyan] {goals.get('linkedin_posts_target', '?')} posts  "
            f"[cyan]Progress posts:[/cyan] {goals.get('progress_posts_target', '?')}\n\n"
            f"[dim]{plan.director_analysis or 'No analysis yet.'}[/dim]",
            title="[bold cyan]Active Weekly Plan[/bold cyan]",
            border_style="cyan",
        ))

        tasks = session.execute(
            select(AgentTask)
            .where(AgentTask.plan_id == plan.id)
            .order_by(AgentTask.scheduled_for.asc())
        ).scalars().all()

        if not tasks:
            console.print("[dim]No agent tasks created yet.[/dim]")
            return

        table = Table(title="Agent Tasks", border_style="dim")
        table.add_column("Type", style="cyan")
        table.add_column("Assigned To", style="magenta")
        table.add_column("Priority")
        table.add_column("Scheduled")
        table.add_column("Status")

        status_colours = {
            "pending": "yellow", "in_progress": "cyan",
            "completed": "green", "failed": "red", "skipped": "dim",
        }
        failed_tasks = []
        for t in tasks:
            colour = status_colours.get(t.status, "white")
            table.add_row(
                t.task_type,
                t.assigned_to,
                t.priority,
                str(t.scheduled_for)[:16] if t.scheduled_for else "—",
                f"[{colour}]{t.status}[/{colour}]",
            )
            if t.status == "failed":
                failed_tasks.append(t)

        console.print(table)

        summary = {
            "pending": sum(1 for t in tasks if t.status == "pending"),
            "in_progress": sum(1 for t in tasks if t.status == "in_progress"),
            "completed": sum(1 for t in tasks if t.status == "completed"),
            "failed": sum(1 for t in tasks if t.status == "failed"),
        }
        console.print(
            f"[dim]Total: {len(tasks)} tasks — "
            f"pending: {summary['pending']}, "
            f"in_progress: {summary['in_progress']}, "
            f"completed: {summary['completed']}, "
            f"failed: {summary['failed']}[/dim]"
        )

        # Show error details for failed tasks
        if failed_tasks:
            console.print()
            console.print("[bold red]Failed Task Errors:[/bold red]")
            for t in failed_tasks:
                err = t.error_message or "(no error message stored)"
                console.print(
                    f"  [cyan]{t.task_type}[/cyan] "
                    f"({str(t.scheduled_for)[:16] if t.scheduled_for else '?'}) "
                    f"[dim]id={str(t.id)[:8]}[/dim]"
                )
                console.print(f"    [red]→ {err[:200]}[/red]")
            console.print(
                "\n[dim]To retry all failed tasks: "
                "[cyan]tantra director retry-failed[/cyan][/dim]"
            )

        # ── Phase 3: Ad-hoc tasks (no plan_id — created via director chat) ──────
        try:
            from sqlalchemy import select as _select, null
            adhoc_tasks = session.execute(
                _select(AgentTask)
                .where(AgentTask.plan_id == None)  # noqa: E711
                .where(AgentTask.task_type.in_([
                    "youtube_script", "youtube_produce", "youtube_publish",
                ]))
                .order_by(AgentTask.created_at.desc())
                .limit(10)
            ).scalars().all()

            if adhoc_tasks:
                console.print()
                adhoc_table = Table(
                    title="[bold magenta]Phase 3 — Ad-hoc Tasks[/bold magenta]",
                    border_style="dim",
                )
                adhoc_table.add_column("Type", style="magenta")
                adhoc_table.add_column("Instructions", style="white", max_width=50)
                adhoc_table.add_column("Status")
                adhoc_table.add_column("Created", style="dim")
                adhoc_table.add_column("ID", style="dim")
                for t in adhoc_tasks:
                    colour = status_colours.get(t.status, "white")
                    instr = (t.instructions or "")[:60] or (
                        str(t.context.get("topic_hint", ""))[:60]
                        if isinstance(t.context, dict) else ""
                    )
                    adhoc_table.add_row(
                        t.task_type,
                        instr,
                        f"[{colour}]{t.status}[/{colour}]",
                        str(t.created_at)[:16] if t.created_at else "—",
                        str(t.id)[:8],
                    )
                console.print(adhoc_table)

                # Show errors for any failed ad-hoc tasks
                failed_adhoc = [t for t in adhoc_tasks if t.status == "failed"]
                if failed_adhoc:
                    console.print()
                    console.print("[bold red]Phase 3 Task Errors:[/bold red]")
                    for t in failed_adhoc:
                        err = t.error_message or "(no error stored — check celery-worker logs)"
                        console.print(
                            f"  [magenta]{t.task_type}[/magenta] "
                            f"[dim]id={str(t.id)[:8]}[/dim]"
                        )
                        console.print(f"    [red]→ {err[:300]}[/red]")
                    console.print(
                        "\n[dim]Full traceback: "
                        "[cyan]docker compose logs celery-worker --tail=200 | grep -A 30 youtube[/cyan][/dim]"
                    )
        except Exception as _e:
            console.print(f"[dim]Phase 3 tasks: {_e}[/dim]")

        # ── Phase 3: YouTube Videos section ───────────────────────────────────
        try:
            from tantra.db.social import YouTubeVideo
            from sqlalchemy import select as _select

            yt_videos = session.execute(
                _select(YouTubeVideo)
                .order_by(YouTubeVideo.created_at.desc())
                .limit(10)
            ).scalars().all()

            if yt_videos:
                console.print()
                yt_status_colours = {
                    "scripted": "yellow", "approved": "cyan", "producing": "blue",
                    "produced": "magenta", "uploading": "cyan", "live": "green",
                    "rejected": "dim", "failed": "red",
                }
                yt_table = Table(title="[bold cyan]YouTube Videos[/bold cyan]", border_style="dim")
                yt_table.add_column("Title", style="white", max_width=40)
                yt_table.add_column("Status")
                yt_table.add_column("Scenes", justify="right", style="dim")
                yt_table.add_column("Views", justify="right", style="dim")
                yt_table.add_column("YouTube URL / Note", style="dim", max_width=45)

                for v in yt_videos:
                    colour = yt_status_colours.get(v.status, "white")
                    scenes = len((v.script or {}).get("scenes", []))
                    url_or_note = (
                        v.youtube_url or
                        ("awaiting approval" if v.status == "scripted" else
                         "production pending" if v.status == "approved" else
                         "rejection reason: " + (v.rejection_reason or "—") if v.status == "rejected" else
                         "—")
                    )
                    yt_table.add_row(
                        (v.title or "Untitled")[:40],
                        f"[{colour}]{v.status}[/{colour}]",
                        str(scenes) if scenes else "—",
                        f"{v.views:,}" if v.views else "—",
                        url_or_note[:45],
                    )

                console.print(yt_table)
                live_count = sum(1 for v in yt_videos if v.status == "live")
                pending_count = sum(1 for v in yt_videos if v.status in ("scripted", "approved", "producing", "produced", "uploading"))
                console.print(
                    f"[dim]YouTube: {len(yt_videos)} video(s) — "
                    f"live: {live_count}, in pipeline: {pending_count}[/dim]"
                )
        except Exception as yt_exc:
            # YouTube section is non-fatal — DB table may not exist yet before first migration
            console.print(f"[dim](YouTube section unavailable: {yt_exc})[/dim]")


@director_app.command("retry-failed")
def director_retry_failed(
    task_type_filter: Optional[str] = typer.Option(
        None, "--type", "-t",
        help="Only retry tasks of this type (e.g. progress_post, research_draft)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview what would be retried without changing DB"),
) -> None:
    """Reset all failed agent tasks to 'pending' so dispatch_due_tasks picks them up again."""
    from datetime import datetime
    from sqlalchemy import create_engine, select, update
    from sqlalchemy.orm import sessionmaker
    from tantra.core.config import settings

    _banner()

    engine = create_engine(settings.database_sync_url, echo=False)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        try:
            from tantra.db.director import AgentTask, WeeklyPlan
        except ImportError as e:
            console.print(f"[red]DB models not available:[/red] {e}")
            raise typer.Exit(1)

        # Get the active plan
        plan = session.execute(
            select(WeeklyPlan)
            .where(WeeklyPlan.status == "active")
            .order_by(WeeklyPlan.week_start.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not plan:
            console.print("[yellow]No active weekly plan.[/yellow]")
            raise typer.Exit(0)

        # Find failed tasks
        query = (
            select(AgentTask)
            .where(AgentTask.plan_id == plan.id, AgentTask.status == "failed")
            .order_by(AgentTask.scheduled_for.asc())
        )
        if task_type_filter:
            query = query.where(AgentTask.task_type == task_type_filter)

        failed = session.execute(query).scalars().all()

        if not failed:
            console.print("[green]No failed tasks to retry.[/green]")
            raise typer.Exit(0)

        console.print(f"[yellow]Found {len(failed)} failed task(s):[/yellow]")
        for t in failed:
            err = t.error_message or "(no error stored)"
            console.print(
                f"  [cyan]{t.task_type}[/cyan] "
                f"scheduled={str(t.scheduled_for)[:16] if t.scheduled_for else '?'}  "
                f"[dim]error: {err[:100]}[/dim]"
            )

        if dry_run:
            console.print("\n[dim]Dry run — no changes made. Remove --dry-run to retry.[/dim]")
            raise typer.Exit(0)

        # Reset to pending with scheduled_for = now (immediate dispatch)
        now = datetime.utcnow()
        count = 0
        for t in failed:
            t.status = "pending"
            t.error_message = None
            t.result = None
            t.started_at = None
            t.completed_at = None
            # Push scheduled_for to now so dispatch_due_tasks picks it up immediately
            t.scheduled_for = now
            count += 1

        session.commit()
        console.print(
            f"\n[green]✓[/green] Reset [cyan]{count}[/cyan] task(s) to pending "
            f"(scheduled for now — will dispatch at next beat tick within 30 min).\n"
            "[dim]Force immediate dispatch:[/dim]  "
            "[cyan]tantra task run director_dispatch_due_tasks --wait[/cyan]"
        )


@director_app.command("errors")
def director_errors(
    limit: int = typer.Option(10, "--limit", "-n", help="Max tasks to show"),
) -> None:
    """Show detailed error information for all failed agent tasks."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from tantra.core.config import settings

    _banner()

    engine = create_engine(settings.database_sync_url, echo=False)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        try:
            from tantra.db.director import AgentTask
        except ImportError as e:
            console.print(f"[red]DB models not available:[/red] {e}")
            raise typer.Exit(1)

        failed = session.execute(
            select(AgentTask)
            .where(AgentTask.status == "failed")
            .order_by(AgentTask.completed_at.desc().nulls_last())
            .limit(limit)
        ).scalars().all()

        if not failed:
            console.print("[green]No failed tasks found.[/green]")
            return

        for t in failed:
            err = t.error_message or "(no error message stored)"
            result_str = str(t.result or {})
            console.print(Panel(
                f"[bold]{t.task_type}[/bold]  assigned_to=[cyan]{t.assigned_to}[/cyan]  "
                f"priority={t.priority}\n"
                f"[dim]ID: {t.id}[/dim]\n"
                f"Scheduled: {str(t.scheduled_for)[:19] if t.scheduled_for else '—'}  "
                f"Failed at: {str(t.completed_at)[:19] if t.completed_at else '—'}\n\n"
                f"[red]Error:[/red] {err}\n\n"
                f"[dim]Result payload: {result_str[:300]}[/dim]",
                border_style="red",
                title=f"[red]FAILED[/red]",
            ))

        console.print(
            f"\n[dim]{len(failed)} failed task(s) shown. "
            "Run [cyan]tantra director retry-failed[/cyan] to reset them.[/dim]"
        )


@director_app.command("recover")
def director_recover() -> None:
    """
    Recover stuck in-progress agent tasks after an unplanned worker crash or restart.

    Scans all AgentTasks in 'in_progress' state. Any task that has exceeded its
    type-specific time budget is reset to 'pending' so dispatch_due_tasks picks
    it up again at the next beat tick (within 30 min) or immediately with:

      tantra task run director_dispatch_due_tasks --wait

    This command runs the same logic as the automatic 15-min beat schedule and
    the @worker_ready startup hook — useful for immediate manual recovery.
    """
    _banner()

    console.print("[cyan]Scanning for stuck in-progress agent tasks...[/cyan]\n")

    try:
        from tantra.tasks.director_tasks import recover_stuck_agent_tasks
        result = recover_stuck_agent_tasks()
    except Exception as exc:
        console.print(f"[red]Recovery scan failed:[/red] {exc}")
        raise typer.Exit(1)

    scanned = result.get("scanned", 0)
    recovered = result.get("recovered_tasks", [])
    count = result.get("recovered_count", 0)

    console.print(f"[dim]Scanned {scanned} in-progress task(s).[/dim]")

    if not recovered:
        console.print("[green]✓[/green] No stuck tasks found — all in-progress tasks are within their time budget.")
        return

    console.print(f"[yellow]⚠[/yellow]  Recovered [cyan]{count}[/cyan] stuck task(s):\n")
    for t in recovered:
        console.print(
            f"  [cyan]{t['task_type']}[/cyan]  "
            f"stuck since {t['stuck_since'][:19]}  "
            f"({t['stuck_for_minutes']} min)  "
            f"[dim]id={t['id'][:8]}[/dim]"
        )

    console.print(
        f"\n[green]✓[/green] Reset to pending. "
        "Will dispatch within 30 min, or force now:\n"
        "[dim]  [cyan]tantra task run director_dispatch_due_tasks --wait[/cyan][/dim]"
    )


@director_app.command("plans")
def director_plans(
    limit: int = typer.Option(5, "--limit", "-n", help="Number of plans to show"),
) -> None:
    """List recent weekly plans."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from tantra.core.config import settings

    _banner()

    engine = create_engine(settings.database_sync_url, echo=False)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        try:
            from tantra.db.director import WeeklyPlan
        except ImportError as e:
            console.print(f"[red]DB models not available:[/red] {e}")
            raise typer.Exit(1)

        plans = session.execute(
            select(WeeklyPlan).order_by(WeeklyPlan.week_start.desc()).limit(limit)
        ).scalars().all()

        if not plans:
            console.print("[yellow]No weekly plans found.[/yellow]")
            return

        table = Table(title="Weekly Plans", border_style="dim")
        table.add_column("Week", style="cyan")
        table.add_column("Year")
        table.add_column("Status")
        table.add_column("Primary Topic")
        table.add_column("Created")

        status_colours = {
            "planning": "yellow", "active": "green",
            "completed": "dim", "cancelled": "red",
        }
        for p in plans:
            colour = status_colours.get(p.status, "white")
            primary = (p.goals or {}).get("primary_topic", "—")[:50]
            table.add_row(
                f"Week {p.week_number} ({p.week_start})",
                str(p.year),
                f"[{colour}]{p.status}[/{colour}]",
                primary,
                str(p.created_at)[:16],
            )

        console.print(table)


@director_app.command("plan")
def director_plan(
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for result"),
    timeout: int = typer.Option(120, "--timeout", "-t", help="Seconds to wait"),
) -> None:
    """
    Trigger the Director to plan this week now (manual run).
    Equivalent to: tantra task run director_weekly_planning
    """
    from tantra.tasks.celery_app import app as celery_app
    _load_all_task_modules()
    _banner()

    console.print("[cyan]Triggering weekly planning...[/cyan]")
    result = celery_app.send_task("tantra.tasks.director.weekly_planning", queue="agents")
    console.print(f"[dim]Celery task ID: {result.id}[/dim]")

    if not wait:
        console.print(f"[dim]Check status with: tantra task status {result.id}[/dim]")
        return

    console.print(f"[dim]Waiting up to {timeout}s...[/dim]")
    try:
        with console.status("[cyan]Director is planning...[/cyan]"):
            value = result.get(timeout=timeout)
        console.print(Panel(
            str(value),
            title="[green]Weekly Plan Created[/green]",
            border_style="green",
        ))
        console.print("[dim]Run 'tantra director status' to see the full plan.[/dim]")
    except Exception as exc:
        console.print(f"[red]Planning failed or timed out:[/red] {exc}")
        raise typer.Exit(1)


@director_app.command("tasks")
def director_tasks_list(
    status_filter: Optional[str] = typer.Option(None, "--status", "-s", help="Filter: pending|in_progress|completed|failed"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """List agent tasks (all or filtered by status)."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from tantra.core.config import settings

    _banner()

    engine = create_engine(settings.database_sync_url, echo=False)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        try:
            from tantra.db.director import AgentTask
        except ImportError as e:
            console.print(f"[red]DB models not available:[/red] {e}")
            raise typer.Exit(1)

        query = select(AgentTask).order_by(AgentTask.created_at.desc())
        if status_filter:
            query = query.where(AgentTask.status == status_filter)
        query = query.limit(limit)
        tasks = session.execute(query).scalars().all()

        if not tasks:
            console.print(f"[yellow]No tasks found{f' with status={status_filter!r}' if status_filter else ''}.[/yellow]")
            return

        table = Table(title="Agent Tasks", border_style="dim")
        table.add_column("Type", style="cyan")
        table.add_column("Assigned To")
        table.add_column("Priority")
        table.add_column("Status")
        table.add_column("Scheduled")
        table.add_column("ID", style="dim")

        status_colours = {
            "pending": "yellow", "in_progress": "cyan",
            "completed": "green", "failed": "red", "skipped": "dim",
        }
        for t in tasks:
            colour = status_colours.get(t.status, "white")
            table.add_row(
                t.task_type,
                t.assigned_to,
                t.priority,
                f"[{colour}]{t.status}[/{colour}]",
                str(t.scheduled_for)[:16] if t.scheduled_for else "—",
                str(t.id)[:8],
            )

        console.print(table)


# ---------------------------------------------------------------------------
# director chat — async helpers (called via asyncio.run)
# ---------------------------------------------------------------------------

async def _handle_chat_approval(director, history: list[dict], r, session_id: str, chat_ttl: int) -> None:
    """
    Extract AgentTasks from the conversation and commit them to the DB.
    Called when the user types an approval keyword (approve / go / execute / …).
    """
    import json
    from datetime import datetime

    from tantra.agents.director import _extract_json

    console.print(
        "\n[bold yellow]⚡ Approval detected[/bold yellow]  "
        "[dim]— asking Director to extract tasks…[/dim]"
    )

    extraction_prompt = (
        "The user has just confirmed approval — they typed an approval word (approve/go/execute).\n"
        "Extract EVERY task that was proposed, requested, or discussed in this conversation "
        "that should now be committed to the DB and dispatched to a worker.\n\n"
        "Include any task the user asked to create or run, even if they did not say 'approved' "
        "inline — the approval word they just typed covers all proposed tasks.\n\n"
        "Return ONLY a JSON array (no commentary, no markdown fences):\n"
        "[\n"
        "  {\n"
        '    "task_type": "research_draft|progress_post|youtube_script|youtube_produce|youtube_publish|analytics_review",\n'
        '    "assigned_to": "social_crew|youtube_crew|media_crew|cmo|cto|director",\n'
        '    "priority": "high|medium|low",\n'
        '    "instructions": "<exact instructions from our conversation>",\n'
        '    "context": {"topic_hint": "<optional topic hint>"}\n'
        "  }\n"
        "]\n\n"
        "If the conversation contains NO tasks at all (e.g. it was purely informational), return [].\n"
        "For youtube_script tasks, set assigned_to to youtube_crew."
    )

    # Inject approval keyword into extraction history so the LLM knows the user approved.
    # The real approval turn is intercepted before it reaches `history`, so we simulate it here.
    extraction_history = history + [
        {"role": "user", "content": "approve"},
        {"role": "assistant", "content": "Understood — I will now extract and commit all proposed tasks."},
        {"role": "user", "content": extraction_prompt},
    ]
    extraction_response = ""
    with console.status("[cyan]Director extracting tasks…[/cyan]"):
        try:
            gen = await director.converse(extraction_history)
            async for token in gen:
                extraction_response += token
        except Exception as exc:
            console.print(f"[red]Task extraction failed: {exc}[/red]")
            return

    try:
        tasks_raw = _extract_json(extraction_response)
        if not isinstance(tasks_raw, list):
            raise ValueError("Expected JSON array")
    except Exception as exc:
        console.print(
            f"[yellow]Could not parse task list ({exc}).[/yellow]\n"
            "[dim]Be specific: tell the Director exactly which tasks to run, then say 'approve'.[/dim]"
        )
        return

    if not tasks_raw:
        console.print("[dim]No tasks found in the conversation to commit.[/dim]")
        return

    # ── Validate task_type against the Celery handlers that actually exist ────
    _VALID_TASK_TYPES = {
        # Phase 1 — LinkedIn
        "research_draft",
        "progress_post",
        # Phase 2 — Analytics
        "analytics_review",
        # Phase 3 — YouTube
        "youtube_script",    # director chat → YouTubeCrew → script → n8n approval
        "youtube_produce",   # tantra-media → TTS + video/images + assembly (Phase 3b)
        "youtube_publish",   # YouTube Data API upload (Phase 3c)
    }
    _VALID_ASSIGNED_TO = {"social_crew", "youtube_crew", "media_crew", "cmo", "cto", "director"}

    valid_tasks = []
    skipped_tasks = []
    for t in tasks_raw:
        tt = t.get("task_type", "")
        if tt not in _VALID_TASK_TYPES:
            skipped_tasks.append(t)
            continue
        # Normalise assigned_to to a known value; fall back to social_crew
        at = t.get("assigned_to", "social_crew")
        if at not in _VALID_ASSIGNED_TO:
            t["assigned_to"] = "social_crew"
        valid_tasks.append(t)

    if skipped_tasks:
        console.print(
            f"\n[yellow]⚠ Skipped {len(skipped_tasks)} task(s) — task_type not supported by any Celery handler:[/yellow]"
        )
        for t in skipped_tasks:
            console.print(f"  [dim]× {t.get('task_type', '?')}[/dim]")
        console.print(
            "[dim]Supported types: [cyan]research_draft[/cyan], [cyan]progress_post[/cyan], "
            "[cyan]youtube_script[/cyan], [cyan]analytics_review[/cyan]\n"
            "Phase 3 tasks (YouTube production, Instagram posts, X threads) don't have "
            "Celery handlers yet — they'll be added when Phase 3 is built.[/dim]"
        )

    if not valid_tasks:
        console.print(
            "[yellow]No executable tasks to commit.[/yellow]\n"
            "[dim]Use specific task types the system can execute. "
            "Discussion tasks and strategy planning aren't AgentTasks — they're conversation.[/dim]"
        )
        return

    console.print(f"\n[bold]Tasks to create ([cyan]{len(valid_tasks)}[/cyan]):[/bold]")
    for i, t in enumerate(valid_tasks, 1):
        console.print(
            f"  [dim]{i}.[/dim] [cyan]{t.get('task_type', '?')}[/cyan] "
            f"[{t.get('priority', 'medium')}]  "
            f"[dim]{t.get('instructions', '')[:80]}[/dim]"
        )

    try:
        from datetime import datetime

        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        from tantra.core.config import settings
        from tantra.db.director import AgentTask, WeeklyPlan

        engine = create_engine(settings.database_sync_url, echo=False)
        DBSession = sessionmaker(bind=engine)
        with DBSession() as db_session:
            plan = db_session.execute(
                select(WeeklyPlan)
                .where(WeeklyPlan.status == "active")
                .order_by(WeeklyPlan.week_start.desc())
                .limit(1)
            ).scalar_one_or_none()

            now = datetime.utcnow()
            for t in valid_tasks:
                db_session.add(AgentTask(
                    plan_id=plan.id if plan else None,
                    task_type=t.get("task_type", "research_draft"),
                    assigned_to=t.get("assigned_to", "social_crew"),
                    priority=t.get("priority", "medium"),
                    instructions=t.get("instructions", ""),
                    context=t.get("context", {}),
                    scheduled_for=now,
                    status="pending",
                ))
            db_session.commit()

        console.print(
            f"\n[green]✓[/green] Created [cyan]{len(valid_tasks)}[/cyan] AgentTask(s) → pending.\n"
            "[dim]Dispatch now:[/dim]  "
            "[cyan]tantra task run dispatch_due_tasks --wait[/cyan]"
        )

        # ── Inject a committed-note into conversation history ─────────────────
        # This prevents the NEXT approval trigger in the same session from
        # re-extracting and re-creating the exact same tasks.
        # The Director will see this note and return [] on the next extraction.
        committed_summary = ", ".join(
            f"{t.get('task_type')} ({t.get('priority','medium')})"
            for t in valid_tasks
        )
        history.append({
            "role": "assistant",
            "content": (
                f"[COMMITTED] Tasks already created in DB: {committed_summary}. "
                "These are now pending dispatch — do NOT extract them again. "
                "Only create new tasks if the user explicitly requests something different."
            ),
        })
        # Persist updated history so the note survives a resume
        history_key = f"tantra:director:chat:{session_id}:history"
        try:
            r.setex(history_key, chat_ttl, json.dumps(history))
        except Exception:
            pass

    except Exception as exc:
        console.print(f"[red]DB error creating tasks: {exc}[/red]")


async def _director_chat_session(resume_session_id: Optional[str]) -> None:
    """
    Full async implementation of the Director chat REPL.
    Runs inside asyncio.run() called by the Typer command.
    """
    import json
    import time
    import uuid
    from datetime import datetime

    import redis as _redis_lib
    from rich.prompt import Prompt
    from tantra.agents.director import DirectorAgent
    from tantra.core.config import settings

    CHAT_TTL = 30 * 24 * 3600          # 30-day session persistence
    CHAT_INDEX_KEY = "tantra:director:chat:index"

    # ── Redis DB3 ─────────────────────────────────────────────────────────────
    redis_url = settings.celery_broker_url.replace("/1", "/3")
    r = _redis_lib.from_url(redis_url, decode_responses=True)

    # ── Session init ──────────────────────────────────────────────────────────
    session_id = resume_session_id or str(uuid.uuid4())[:12]
    history_key = f"tantra:director:chat:{session_id}:history"
    meta_key    = f"tantra:director:chat:{session_id}:meta"

    raw_history = r.get(history_key)
    history: list[dict] = json.loads(raw_history) if raw_history else []

    raw_meta = r.get(meta_key)
    meta: dict = json.loads(raw_meta) if raw_meta else {
        "session_id": session_id,
        "created_at": datetime.utcnow().isoformat(),
        "message_count": 0,
        "last_active": datetime.utcnow().isoformat(),
    }

    # ── Banner ────────────────────────────────────────────────────────────────
    _banner()
    resumed = bool(history)
    turns = meta.get("message_count", 0)
    console.print(Panel(
        f"[bold cyan]Director Chat[/bold cyan]  —  Interactive REPL\n"
        f"[dim]Session ID: {session_id}[/dim]\n\n"
        + (f"[dim]Resumed — {turns} previous turn(s)[/dim]\n" if resumed else "")
        + "[dim]Type [cyan]exit[/cyan] or Ctrl+C to quit.  "
          "Say [yellow]approve[/yellow] / [yellow]go[/yellow] / [yellow]execute[/yellow] to commit tasks.[/dim]",
        border_style="cyan",
    ))

    # Show last exchange as context
    if history:
        last_two = history[-2:] if len(history) >= 2 else history
        for msg in last_two:
            label = "[bold cyan]You[/bold cyan]" if msg["role"] == "user" else "[bold magenta]Director[/bold magenta]"
            preview = msg["content"][:280] + ("…" if len(msg["content"]) > 280 else "")
            console.print(f"\n{label}: [dim]{preview}[/dim]")
        console.print()

    director = DirectorAgent()

    # ── REPL loop ─────────────────────────────────────────────────────────────
    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print(
                f"\n[dim]Session saved. Resume:[/dim]  "
                f"[cyan]tantra director chat --resume {session_id}[/cyan]"
            )
            break

        stripped = user_input.strip()
        if not stripped:
            continue
        if stripped.lower() in ("exit", "quit", "bye", ":q", "q"):
            console.print(
                f"[dim]Session saved. Resume:[/dim]  "
                f"[cyan]tantra director chat --resume {session_id}[/cyan]"
            )
            break

        # ── Intercept approval keywords BEFORE sending to LLM ────────────────
        # If the user sends a standalone approval word ("execute", "approve", etc.)
        # we must NOT forward it to the model — the model would treat "execute" as
        # a natural language command and hallucinate a response, then the extraction
        # would run on that hallucinated content instead of the real conversation.
        if DirectorAgent.should_approve(stripped):
            console.print(
                f"\n[bold yellow]⚡ Approval keyword detected[/bold yellow]  "
                "[dim]— skipping LLM response, extracting tasks from conversation above…[/dim]"
            )
            # Run extraction against history WITHOUT the approval keyword message
            # (the bare "approve"/"execute" is not a meaningful conversational turn)
            await _handle_chat_approval(director, history, r, session_id, CHAT_TTL)
            # Save updated session state (approval may have added tasks to context)
            try:
                meta["last_active"] = datetime.utcnow().isoformat()
                r.setex(history_key, CHAT_TTL, json.dumps(history))
                r.setex(meta_key,    CHAT_TTL, json.dumps(meta))
            except Exception:
                pass
            continue    # back to REPL prompt — no LLM response needed

        # ── Add user turn to history ──────────────────────────────────────────
        history.append({"role": "user", "content": stripped})

        # ── Get live system context (fast DB read, grounds Director in reality) ─
        live_ctx = DirectorAgent.get_live_context()

        # ── Stream Director response ──────────────────────────────────────────
        console.print(f"\n[bold magenta]Director[/bold magenta] [dim]▶[/dim] ", end="")
        full_response = ""
        try:
            gen = await director.converse(history, live_context=live_ctx)
            async for token in gen:
                console.print(token, end="", highlight=False)
                full_response += token
        except Exception as exc:
            console.print(f"\n[red]LLM error: {exc}[/red]")
            console.print("[dim]Check litellm is running (port 4000) and qwen3:30b is pulled.[/dim]")
            history.pop()   # revert — no response was stored
            continue

        console.print()     # newline after streamed response

        # Add assistant turn
        history.append({"role": "assistant", "content": full_response})
        meta["message_count"] = sum(1 for m in history if m["role"] == "user")
        meta["last_active"] = datetime.utcnow().isoformat()

        # ── Persist to Redis ──────────────────────────────────────────────────
        try:
            r.setex(history_key, CHAT_TTL, json.dumps(history))
            r.setex(meta_key,    CHAT_TTL, json.dumps(meta))
            r.zadd(CHAT_INDEX_KEY, {session_id: time.time()})
            r.expire(CHAT_INDEX_KEY, CHAT_TTL)
        except Exception:
            pass    # non-fatal — session lives in memory for this run


# ---------------------------------------------------------------------------
# director chat / director sessions — Typer commands
# ---------------------------------------------------------------------------

@director_app.command("chat")
def director_chat(
    resume: Optional[str] = typer.Option(
        None, "--resume", "-r",
        help="Resume a previous session by its session ID",
    ),
) -> None:
    """
    Interactive Director chat — multi-turn streaming conversation with the CAIO.

    Session history is stored in Redis DB3 for 30 days.

    Examples:

      tantra director chat                        # new session
      tantra director chat --resume abc123        # resume existing session
      tantra director sessions                    # list all sessions
    """
    asyncio.run(_director_chat_session(resume))


@director_app.command("sessions")
def director_sessions(
    limit: int = typer.Option(10, "--limit", "-n", help="Max sessions to show"),
) -> None:
    """List all saved Director chat sessions."""
    import json

    import redis as _redis_lib
    from tantra.core.config import settings

    _banner()

    try:
        redis_url = settings.celery_broker_url.replace("/1", "/3")
        r = _redis_lib.from_url(redis_url, decode_responses=True)
        sessions = r.zrevrangebyscore(
            "tantra:director:chat:index", "+inf", "-inf",
            start=0, num=limit, withscores=True,
        )
    except Exception as exc:
        console.print(f"[red]Redis connection error: {exc}[/red]")
        raise typer.Exit(1)

    if not sessions:
        console.print("[yellow]No chat sessions found.[/yellow]")
        console.print("[dim]Start one with: tantra director chat[/dim]")
        return

    table = Table(title="Director Chat Sessions", border_style="dim")
    table.add_column("Session ID", style="cyan")
    table.add_column("Turns", justify="right")
    table.add_column("Created", style="dim")
    table.add_column("Last Active", style="dim")
    table.add_column("Resume Command", style="dim")

    for session_id, _ in sessions:
        raw = r.get(f"tantra:director:chat:{session_id}:meta")
        meta = json.loads(raw) if raw else {}
        table.add_row(
            session_id,
            str(meta.get("message_count", "?")),
            str(meta.get("created_at", "?"))[:16],
            str(meta.get("last_active", "?"))[:16],
            f"tantra director chat --resume {session_id}",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# media — tantra-media microservice commands (Phase 3b)
# ---------------------------------------------------------------------------


@app.command()
def media_status(
    api_url: str = typer.Option("http://localhost:8000", "--api", envvar="TANTRA_API_URL"),
) -> None:
    """Check tantra-media service health and show produced videos."""
    import httpx

    console.print("[bold cyan]tantra-media status[/bold cyan]")

    # Health check via tantra-api proxied health, or direct
    media_url = "http://localhost:8100"
    try:
        resp = httpx.get(f"{media_url}/health", timeout=5)
        data = resp.json()
        console.print(f"  Service:    [green]online[/green]  ({media_url})")
        console.print(f"  Media dir:  {data.get('media_dir', '?')}")
        console.print(f"  Dir exists: {data.get('media_dir_exists', '?')}")
    except httpx.ConnectError:
        console.print(f"  Service:    [red]offline[/red] — run: docker compose up -d tantra-media")
        return
    except Exception as exc:
        console.print(f"  Service:    [red]error[/red] — {exc}")
        return

    # Show videos in produced/producing status
    try:
        resp = httpx.get(f"{api_url}/api/v1/youtube/videos?status=produced", timeout=10)
        if resp.status_code == 200:
            videos = resp.json().get("videos", [])
            if videos:
                console.print(f"\n  [bold]Produced videos ({len(videos)}):[/bold]")
                for v in videos[:5]:
                    console.print(f"    {v['id'][:8]}  {v.get('title', '?')[:50]}")
            else:
                console.print("  No produced videos yet.")
    except Exception:
        pass


@app.command()
def media_produce(
    video_id: str = typer.Argument(..., help="YouTubeVideo UUID to produce"),
    force: bool = typer.Option(False, "--force", "-f", help="Regenerate even if files exist"),
    api_url: str = typer.Option("http://localhost:8000", "--api", envvar="TANTRA_API_URL"),
) -> None:
    """
    Manually trigger production of a YouTube video via tantra-media.

    This enqueues a youtube_produce AgentTask. The video must be in 'approved' status.
    The Celery worker will call tantra-media to generate TTS + slides + MP4.

    Examples:
      tantra media-produce <video-uuid>
      tantra media-produce <video-uuid> --force
    """
    import httpx

    console.print(f"[cyan]Triggering production for video {video_id}...[/cyan]")

    # POST to approve endpoint if video is in scripted state, otherwise
    # create a youtube_produce task directly
    try:
        resp = httpx.get(f"{api_url}/api/v1/youtube/videos/{video_id}", timeout=10)
        if resp.status_code == 404:
            console.print(f"[red]Video {video_id} not found[/red]")
            raise typer.Exit(1)
        video = resp.json()
        status = video.get("status", "?")
        title = video.get("title", "?")
        console.print(f"  Video:  [bold]{title}[/bold]")
        console.print(f"  Status: {status}")

        if status == "produced" and not force:
            console.print("[yellow]Already produced. Use --force to regenerate.[/yellow]")
            raise typer.Exit(0)

        if status not in ("approved", "produced", "failed"):
            console.print(f"[red]Cannot produce from status '{status}'. Video must be in approved/produced/failed.[/red]")
            raise typer.Exit(1)

        # Dispatch via task run
        import subprocess
        result = subprocess.run(
            ["tantra", "task", "run", "youtube_produce",
             "--context", f"youtube_video_id={video_id}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            console.print("[green]✓ Production task queued.[/green]")
            console.print("[dim]Monitor with: tantra monitor --filter task[/dim]")
        else:
            console.print(f"[red]Failed to queue task:[/red] {result.stderr}")

    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to API at {api_url}[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# monitor — live stream of agent/model events
# ---------------------------------------------------------------------------

@app.command()
def monitor(
    filter_event: Optional[str] = typer.Option(
        None, "--filter", "-f",
        help="Filter by event type prefix (e.g. llm, agent, tool, task, system)",
    ),
    last: int = typer.Option(
        0, "--last", "-n",
        help="Show last N events from history before streaming (0 = stream only)",
    ),
) -> None:
    """
    Stream live agent and model events to the console.

    Subscribes to the tantra:monitor:live Redis pub/sub channel and prints
    color-coded events as they happen — LLM calls, agent steps, tool use,
    task lifecycle, system messages.

    Also accessible as a browser dashboard at: http://localhost:8000/monitor
    (requires tantra-api to be running)

    Examples:
      tantra monitor                  # all events
      tantra monitor --filter llm     # only LLM calls
      tantra monitor --filter tool    # only tool invocations
    """
    import json as _json

    from rich.text import Text
    from tantra.core.config import settings
    from tantra.core.monitor import MONITOR_CHANNEL

    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.celery_broker_url, decode_responses=True)
        r.ping()
    except Exception as exc:
        console.print(f"[red]Cannot connect to Redis:[/red] {exc}")
        raise typer.Exit(1)

    EVENT_STYLES: dict[str, tuple[str, str]] = {
        "llm_start":   ("LLM START ", "blue"),
        "llm_end":     ("LLM END   ", "cyan"),
        "llm_failed":  ("LLM FAIL  ", "red"),
        "agent_step":  ("AGENT STEP", "magenta"),
        "tool_call":   ("TOOL CALL ", "yellow"),
        "task_start":  ("TASK START", "green"),
        "task_end":    ("TASK END  ", "green"),
        "task_failed": ("TASK FAIL ", "red"),
        "system":      ("SYSTEM    ", "dim"),
    }

    def _fmt(d: dict) -> Optional[str]:
        ev = d.get("event", "system")
        if filter_event and not ev.startswith(filter_event):
            return None

        label, colour = EVENT_STYLES.get(ev, (ev.ljust(10)[:10], "white"))
        ts = str(d.get("ts", ""))[:19].replace("T", " ")

        if ev == "llm_end":
            detail = (
                f"[{colour}]{d.get('model','?')}[/{colour}]  "
                f"[green]{d.get('latency_ms','?')}ms[/green]  "
                f"{d.get('total_tokens',0)} tok  "
                f"[dim]agent={d.get('agent','?')}[/dim]"
            )
        elif ev == "llm_start":
            detail = (
                f"[{colour}]{d.get('model','?')}[/{colour}]  "
                f"[dim]{d.get('messages_count',0)} msgs  agent={d.get('agent','?')}[/dim]"
            )
        elif ev == "llm_failed":
            detail = f"[red]{d.get('model','?')}[/red]  [red]{str(d.get('error',''))[:120]}[/red]"
        elif ev == "agent_step":
            detail = (
                f"[magenta]{d.get('agent','?')}[/magenta]  "
                f"[dim]{str(d.get('thought_preview',''))[:100]}[/dim]"
            )
        elif ev == "tool_call":
            detail = (
                f"[magenta]{d.get('agent','?')}[/magenta] → "
                f"[yellow]{d.get('tool','?')}[/yellow]  "
                f"[dim]{str(d.get('input_preview',''))[:80]}[/dim]"
            )
        elif ev in ("task_start", "task_end"):
            detail = (
                f"[{colour}]{d.get('task_type','?')}[/{colour}]  "
                f"[dim]id={str(d.get('task_id','?'))[:8]}[/dim]"
            )
        elif ev == "task_failed":
            detail = (
                f"[red]{d.get('task_type','?')}[/red]  "
                f"[red]{str(d.get('error',''))[:120]}[/red]"
            )
        else:
            detail = str(d.get("message", _json.dumps(d)))[:120]

        return (
            f"[dim]{ts}[/dim]  "
            f"[{colour}]{label}[/{colour}]  "
            f"{detail}"
        )

    console.rule("[bold cyan]Tantra AI — Live Monitor[/bold cyan]")
    console.print(
        f"[dim]Channel:[/dim] [cyan]{MONITOR_CHANNEL}[/cyan]"
        + (f"  [dim]filter=[/dim][yellow]{filter_event}[/yellow]" if filter_event else "")
    )
    console.print("[dim]Browser dashboard:[/dim] [cyan]http://localhost:8000/monitor[/cyan]")
    console.print("[dim]Press Ctrl+C to stop.\n[/dim]")

    ps = r.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(MONITOR_CHANNEL)

    try:
        for msg in ps.listen():
            if msg and msg.get("type") == "message":
                try:
                    data = _json.loads(msg["data"])
                    line = _fmt(data)
                    if line:
                        console.print(line)
                except Exception:
                    pass
    except KeyboardInterrupt:
        console.print("\n[dim]Monitor stopped.[/dim]")
    finally:
        try:
            ps.unsubscribe()
            r.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# `tantra config` sub-commands  (Option B — Model Registry)
# ---------------------------------------------------------------------------

config_app = typer.Typer(
    name="config",
    help="Manage Tantra AI model registry and LiteLLM configuration",
    add_completion=False,
    rich_markup_mode="rich",
)
app.add_typer(config_app, name="config")

_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "tantra_models.yaml"
_GENERATOR_PATH = Path(__file__).resolve().parents[2] / "scripts" / "generate_litellm_config.py"


def _load_registry() -> dict:
    """Load tantra_models.yaml; raise if missing."""
    import yaml  # type: ignore

    if not _REGISTRY_PATH.exists():
        console.print(f"[red]Registry not found:[/red] {_REGISTRY_PATH}")
        raise typer.Exit(1)
    with open(_REGISTRY_PATH) as f:
        return yaml.safe_load(f)


def _save_registry(data: dict) -> None:
    """Write registry back to tantra_models.yaml preserving structure."""
    import yaml  # type: ignore

    with open(_REGISTRY_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _run_generator(*extra_args: str) -> int:
    """Run generate_litellm_config.py with given args; return exit code."""
    import subprocess

    cmd = ["python", str(_GENERATOR_PATH), *extra_args]
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


@config_app.command("model")
def config_model(
    tier: str = typer.Argument(..., help="Tier name (e.g. director, manager, worker, coder, fast)"),
    tag: str = typer.Argument(..., help="Ollama model tag (e.g. qwen3:30b) or cloud ID"),
    no_restart: bool = typer.Option(False, "--no-restart", help="Skip restarting LiteLLM after update"),
) -> None:
    """Set the PRIMARY model for a tier in the registry, then regenerate + restart LiteLLM."""
    data = _load_registry()
    tiers = data.get("tiers", {})

    if tier not in tiers:
        available = ", ".join(tiers.keys())
        console.print(f"[red]Unknown tier:[/red] '{tier}'. Available: {available}")
        raise typer.Exit(1)

    old_primary = tiers[tier].get("primary", "<none>")
    tiers[tier]["primary"] = tag
    data["tiers"] = tiers
    _save_registry(data)

    console.print(f"[green]✓[/green] Updated [bold]{tier}[/bold]: {old_primary} → [bold]{tag}[/bold]")

    gen_args = ["--apply"]
    if not no_restart:
        gen_args.append("--restart-litellm")

    console.print("[dim]Regenerating litellm_config.yaml …[/dim]")
    rc = _run_generator(*gen_args)
    if rc != 0:
        console.print(f"[red]Generator failed (exit {rc}). Check output above.[/red]")
        raise typer.Exit(rc)

    console.print("[green]✓[/green] litellm_config.yaml updated" + (" and LiteLLM restarted." if not no_restart else "."))


@config_app.command("list-models")
def config_list_models() -> None:
    """Display the current model registry in a Rich table."""
    from rich.table import Table

    data = _load_registry()
    tiers = data.get("tiers", {})

    table = Table(title="Tantra Model Registry", show_lines=True)
    table.add_column("Tier", style="bold cyan", no_wrap=True)
    table.add_column("Primary (local)", style="green")
    table.add_column("Fallback (local)", style="yellow")
    table.add_column("Cloud", style="magenta")
    table.add_column("Cloud Fallback", style="dim magenta")
    table.add_column("Max Tokens", justify="right")
    table.add_column("Temp", justify="right")

    for tier_name, cfg in tiers.items():
        table.add_row(
            tier_name,
            cfg.get("primary", "—"),
            cfg.get("fallback", "—"),
            cfg.get("cloud", "—"),
            cfg.get("cloud_fallback", "—"),
            str(cfg.get("max_tokens", "—")),
            str(cfg.get("temperature", "—")),
        )

    console.print(table)

    pending = data.get("pending_pulls", [])
    if pending:
        console.print(f"\n[dim]Pending pulls ({len(pending)}):[/dim] {', '.join(pending)}")


@config_app.command("sync-models")
def config_sync_models(
    apply: bool = typer.Option(False, "--apply", help="Write regenerated config to disk"),
    restart: bool = typer.Option(False, "--restart", help="Also restart LiteLLM (implies --apply)"),
) -> None:
    """Check registry vs Ollama models; optionally regenerate + restart LiteLLM."""
    console.print("[dim]Checking registry against Ollama …[/dim]")
    _run_generator("--check-ollama")

    if restart:
        apply = True

    if apply:
        gen_args = ["--apply"]
        if restart:
            gen_args.append("--restart-litellm")
        console.print("[dim]Regenerating litellm_config.yaml …[/dim]")
        rc = _run_generator(*gen_args)
        if rc != 0:
            console.print(f"[red]Generator failed (exit {rc}).[/red]")
            raise typer.Exit(rc)
        console.print("[green]✓[/green] litellm_config.yaml updated" + (" and LiteLLM restarted." if restart else "."))
    else:
        console.print("[dim]Dry-run complete. Use --apply to write changes.[/dim]")


if __name__ == "__main__":
    app()
