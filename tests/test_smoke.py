from typer.testing import CliRunner

from data_analyse_agent.cli import app


def test_cli_doctor() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "data-analyse-agent is ready" in result.output
