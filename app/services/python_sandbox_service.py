import ast
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from dotenv import load_dotenv


PYTHON_RUNS_DIR = Path("app/storage/python_runs")
CHART_DIR = Path("app/storage/charts")
DEFAULT_SANDBOX_IMAGE = "data-analyse-agent-python-sandbox:latest"
DEFAULT_SANDBOX_TIMEOUT_SECONDS = 60
DEFAULT_SANDBOX_MEMORY = "512m"
DEFAULT_SANDBOX_CPUS = "1"
MAX_RESULT_JSON_BYTES = 2 * 1024 * 1024
MAX_STREAM_CHARS = 4000
SUPPORTED_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_FIGURES = 10
PROHIBITED_IMPORT_MODULES = {"socket", "requests", "subprocess"}
ALLOWED_WORKSPACE_PREFIX = "/workspace"
WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"^[a-zA-Z]:[\\/]")
PYTHON_ANALYSIS_PREAMBLE = """\
# Auto-injected by PythonSandboxService.
# Make Matplotlib/Seaborn charts render Chinese labels in the Docker sandbox.
try:
    import matplotlib

    matplotlib.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Noto Sans CJK TC",
        "Microsoft YaHei",
        "SimHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False
except Exception:
    pass

try:
    import seaborn as sns

    sns.set_theme(
        font="Noto Sans CJK SC",
        rc={
            "font.sans-serif": [
                "Noto Sans CJK SC",
                "Noto Sans CJK JP",
                "Noto Sans CJK TC",
                "Microsoft YaHei",
                "SimHei",
                "Arial Unicode MS",
                "DejaVu Sans",
            ],
            "axes.unicode_minus": False,
        },
    )
except Exception:
    pass
"""


@dataclass(frozen=True)
class PythonSandboxFigure:
    chart_id: str
    title: str
    source_name: str
    path: Path
    url: str


@dataclass(frozen=True)
class PythonSandboxResult:
    run_id: str
    run_dir: Path
    result: dict[str, Any]
    stdout: str
    stderr: str
    input_rows: int
    analysis_goal: str
    figures: list[PythonSandboxFigure]


class PythonSandboxService:
    """Run generated Python analysis code inside a bounded Docker container."""

    def __init__(
        self,
        runs_dir: Path = PYTHON_RUNS_DIR,
        chart_dir: Path = CHART_DIR,
        image: str | None = None,
        timeout_seconds: int | None = None,
        memory: str | None = None,
        cpus: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        load_dotenv()
        self.runs_dir = runs_dir
        self.chart_dir = chart_dir
        self.image = image or os.getenv("PYTHON_SANDBOX_IMAGE", DEFAULT_SANDBOX_IMAGE)
        self.timeout_seconds = timeout_seconds or self._env_int(
            "PYTHON_SANDBOX_TIMEOUT_SECONDS",
            DEFAULT_SANDBOX_TIMEOUT_SECONDS,
        )
        self.memory = memory or os.getenv("PYTHON_SANDBOX_MEMORY", DEFAULT_SANDBOX_MEMORY)
        self.cpus = cpus or os.getenv("PYTHON_SANDBOX_CPUS", DEFAULT_SANDBOX_CPUS)
        self.runner = runner

    def run_analysis(
        self,
        dataframe: pd.DataFrame,
        python_code: str,
        analysis_goal: str,
    ) -> PythonSandboxResult:
        run_id = str(uuid.uuid4())
        run_dir = self.runs_dir / run_id
        input_dir = run_dir / "input"
        work_dir = run_dir / "work"
        output_dir = run_dir / "output"
        for directory in (input_dir, work_dir, output_dir):
            directory.mkdir(parents=True, exist_ok=False)

        normalized_code = self._normalize_python_code(python_code)
        if not normalized_code.strip():
            raise ValueError("Python 分析代码不能为空")
        self._validate_python_code(normalized_code)

        data_path = input_dir / "data.json"
        schema_path = input_dir / "schema.json"
        script_path = work_dir / "analysis.py"

        dataframe.to_json(
            data_path,
            orient="records",
            force_ascii=False,
            date_format="iso",
            indent=2,
        )
        schema_path.write_text(
            json.dumps(
                {
                    "analysis_goal": analysis_goal,
                    "row_count": int(len(dataframe)),
                    "columns": [
                        {"name": str(column), "dtype": str(dataframe[column].dtype)}
                        for column in dataframe.columns
                    ],
                    "input_path": "/workspace/input/data.json",
                    "result_path": "/workspace/output/result.json",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        script_path.write_text(self._build_analysis_script(normalized_code), encoding="utf-8")

        command = self._build_docker_command(input_dir, work_dir, output_dir)
        try:
            completed = self.runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("未找到 docker 命令，请确认 Docker Desktop 已安装并在 PATH 中") from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"Python 沙箱执行超过 {self.timeout_seconds} 秒，已自动中止"
            ) from exc

        stdout = self._truncate(completed.stdout)
        stderr = self._truncate(completed.stderr)
        if completed.returncode != 0:
            raise RuntimeError(
                "Python 沙箱脚本执行失败："
                f"退出码 {completed.returncode}\n"
                f"stdout:\n{stdout or '(空)'}\n"
                f"stderr:\n{stderr or '(空)'}"
            )

        result_path = output_dir / "result.json"
        if not result_path.exists():
            raise RuntimeError("Python 沙箱脚本未生成 /workspace/output/result.json")
        if result_path.stat().st_size > MAX_RESULT_JSON_BYTES:
            raise RuntimeError("Python 沙箱 result.json 过大，请减少输出内容")

        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Python 沙箱 result.json 不是合法 JSON") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Python 沙箱 result.json 顶层必须是 JSON 对象")
        figures = self._publish_figures(output_dir, result)
        result = self._rewrite_result_figure_paths(result, figures)

        return PythonSandboxResult(
            run_id=run_id,
            run_dir=run_dir,
            result=result,
            stdout=stdout,
            stderr=stderr,
            input_rows=int(len(dataframe)),
            analysis_goal=analysis_goal,
            figures=figures,
        )

    def health_check(self) -> dict[str, Any]:
        checks = [
            (
                "docker_cli",
                ["docker", "--version"],
                "请确认 Docker Desktop 已安装，并且 docker 命令在 PATH 中。",
            ),
            (
                "docker_daemon",
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                "请启动 Docker Desktop，并使用有权限访问 Docker daemon 的终端运行后端。",
            ),
            (
                "sandbox_image",
                ["docker", "image", "inspect", self.image, "--format", "{{.Id}}"],
                f"请先构建沙箱镜像：docker build -t {self.image} .\\docker\\python-sandbox",
            ),
        ]
        results = []
        for name, command, fix in checks:
            results.append(self._run_health_command(name, command, fix))

        return {
            "ok": all(item["ok"] for item in results),
            "image": self.image,
            "timeout_seconds": self.timeout_seconds,
            "memory": self.memory,
            "cpus": self.cpus,
            "checks": results,
        }

    def _publish_figures(
        self,
        output_dir: Path,
        result: dict[str, Any],
    ) -> list[PythonSandboxFigure]:
        files = [
            path
            for path in sorted(output_dir.iterdir())
            if path.is_file() and path.suffix.lower() in SUPPORTED_FIGURE_SUFFIXES
        ][:MAX_FIGURES]
        if not files:
            return []

        self.chart_dir.mkdir(parents=True, exist_ok=True)
        title_by_name = self._figure_titles_from_result(result)
        figures = []
        for source_path in files:
            chart_id = str(uuid.uuid4())
            target_path = self.chart_dir / f"{chart_id}{source_path.suffix.lower()}"
            shutil.copy2(source_path, target_path)
            figures.append(
                PythonSandboxFigure(
                    chart_id=chart_id,
                    title=title_by_name.get(source_path.name, source_path.stem),
                    source_name=source_path.name,
                    path=target_path,
                    url=f"/charts/{target_path.name}",
                )
            )
        return figures

    def _figure_titles_from_result(self, result: dict[str, Any]) -> dict[str, str]:
        titles = {}
        raw_figures = result.get("figures")
        if not isinstance(raw_figures, list):
            return titles
        for index, item in enumerate(raw_figures, start=1):
            if not isinstance(item, dict):
                continue
            source = (
                item.get("path")
                or item.get("file")
                or item.get("filename")
                or item.get("image_path")
                or item.get("chart_path")
            )
            if not source:
                continue
            title = str(item.get("title") or f"Python 分析图表 {index}")
            titles[Path(str(source)).name] = title
        return titles

    def _rewrite_result_figure_paths(
        self,
        result: dict[str, Any],
        figures: list[PythonSandboxFigure],
    ) -> dict[str, Any]:
        if not figures:
            return result

        figures_by_name = {figure.source_name: figure for figure in figures}
        rewritten = dict(result)
        raw_figures = rewritten.get("figures")
        if isinstance(raw_figures, list):
            rewritten_figures = []
            used_names = set()
            for item in raw_figures:
                if not isinstance(item, dict):
                    rewritten_figures.append(item)
                    continue
                source = (
                    item.get("path")
                    or item.get("file")
                    or item.get("filename")
                    or item.get("image_path")
                    or item.get("chart_path")
                )
                figure = figures_by_name.get(Path(str(source)).name) if source else None
                if figure is None:
                    rewritten_figures.append(item)
                    continue
                updated = dict(item)
                updated.update(
                    {
                        "chart_id": figure.chart_id,
                        "chart_url": figure.url,
                        "path": figure.url,
                        "source_name": figure.source_name,
                    }
                )
                updated.setdefault("title", figure.title)
                rewritten_figures.append(updated)
                used_names.add(figure.source_name)

            for figure in figures:
                if figure.source_name in used_names:
                    continue
                rewritten_figures.append(
                    {
                        "chart_id": figure.chart_id,
                        "chart_url": figure.url,
                        "path": figure.url,
                        "title": figure.title,
                        "source_name": figure.source_name,
                    }
                )
            rewritten["figures"] = rewritten_figures
        else:
            rewritten["figures"] = [
                {
                    "chart_id": figure.chart_id,
                    "chart_url": figure.url,
                    "path": figure.url,
                    "title": figure.title,
                    "source_name": figure.source_name,
                }
                for figure in figures
            ]

        if len(figures) == 1:
            rewritten.setdefault("chart_id", figures[0].chart_id)
            rewritten.setdefault("chart_url", figures[0].url)
            rewritten.setdefault("title", figures[0].title)
        return rewritten

    def _build_docker_command(
        self,
        input_dir: Path,
        work_dir: Path,
        output_dir: Path,
    ) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--pids-limit",
            "128",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=128m",
            "-e",
            "MPLCONFIGDIR=/tmp/matplotlib",
            "-v",
            f"{input_dir.resolve()}:/workspace/input:ro",
            "-v",
            f"{work_dir.resolve()}:/workspace/work:ro",
            "-v",
            f"{output_dir.resolve()}:/workspace/output:rw",
            self.image,
            "python",
            "/workspace/work/analysis.py",
        ]

    def _validate_python_code(self, python_code: str) -> None:
        try:
            tree = ast.parse(python_code)
        except SyntaxError as exc:
            raise ValueError(f"Python 代码语法错误：{exc}") from exc

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._validate_import(alias.name, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                self._validate_import(module_name, node.lineno)
            elif isinstance(node, ast.Call):
                self._validate_dynamic_import(node)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                self._validate_path_literal(node.value, node.lineno)

    def _validate_import(self, module_name: str, line_number: int) -> None:
        root_module = module_name.split(".", 1)[0]
        if root_module in PROHIBITED_IMPORT_MODULES:
            raise ValueError(
                f"Python 代码第 {line_number} 行导入了禁止模块：{root_module}"
            )

    def _validate_dynamic_import(self, node: ast.Call) -> None:
        function_name = self._call_name(node.func)
        if function_name not in {"__import__", "importlib.import_module"}:
            return
        if not node.args:
            return
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
            raise ValueError("Python 代码包含动态导入，无法静态确认安全性")
        self._validate_import(first_arg.value, node.lineno)

    def _validate_path_literal(self, value: str, line_number: int) -> None:
        normalized = value.replace("\\", "/")
        if normalized.startswith(ALLOWED_WORKSPACE_PREFIX):
            return
        if normalized.startswith("/") or WINDOWS_ABSOLUTE_PATH_PATTERN.match(value):
            raise ValueError(
                f"Python 代码第 {line_number} 行包含非 /workspace 绝对路径：{value}"
            )

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return ""

    def _normalize_python_code(self, python_code: str) -> str:
        code = python_code.strip()
        if code.startswith("```"):
            lines = code.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code = "\n".join(lines).strip()
        return code

    def _build_analysis_script(self, python_code: str) -> str:
        return f"{PYTHON_ANALYSIS_PREAMBLE}\n\n{python_code}"

    def _truncate(self, value: str | None) -> str:
        text = value or ""
        if len(text) <= MAX_STREAM_CHARS:
            return text
        return text[:MAX_STREAM_CHARS] + "\n...（输出已截断）"

    def _env_int(self, name: str, default: int) -> int:
        value = os.getenv(name, "").strip()
        if not value:
            return default
        try:
            parsed = int(value)
        except ValueError:
            return default
        return max(parsed, 1)

    def _run_health_command(
        self,
        name: str,
        command: list[str],
        fix: str,
    ) -> dict[str, Any]:
        try:
            completed = self.runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            return {
                "name": name,
                "ok": False,
                "message": "未找到 docker 命令",
                "fix": fix,
            }
        except subprocess.TimeoutExpired:
            return {
                "name": name,
                "ok": False,
                "message": "Docker 检查超时",
                "fix": fix,
            }

        stdout = self._truncate(completed.stdout).strip()
        stderr = self._truncate(completed.stderr).strip()
        return {
            "name": name,
            "ok": completed.returncode == 0,
            "message": stdout if completed.returncode == 0 else stderr or stdout,
            "fix": "" if completed.returncode == 0 else fix,
        }
