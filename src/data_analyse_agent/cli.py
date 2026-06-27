import typer
from rich.console import Console

from data_analyse_agent import __version__
from data_analyse_agent.config import get_settings

app = typer.Typer(help="Data Analyse Agent command line interface.")
console = Console()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the application version.",
    ),
) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit()


@app.command()
def doctor() -> None:
    """Check whether the project skeleton can load correctly."""
    settings = get_settings()
    console.print("[green]OK[/green] data-analyse-agent is ready.")
    console.print(f"env={settings.app_env} log_level={settings.log_level} data_dir={settings.data_dir}")
