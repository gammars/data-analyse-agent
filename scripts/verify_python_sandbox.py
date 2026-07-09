from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path


IMAGE_NAME = "data-analyse-agent-python-sandbox:latest"


def _docker_path(path: Path) -> str:
    return str(path.resolve())


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="daa-sandbox-") as temporary_dir:
        root = Path(temporary_dir)
        input_dir = root / "input"
        output_dir = root / "output"
        work_dir = root / "work"
        input_dir.mkdir()
        output_dir.mkdir()
        work_dir.mkdir()

        (input_dir / "data.json").write_text(
            json.dumps(
                [
                    {"category": "A", "amount": 10},
                    {"category": "A", "amount": 20},
                    {"category": "B", "amount": 5},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (work_dir / "analysis.py").write_text(
            """
import json
from pathlib import Path

import pandas as pd

data = json.loads(Path("/workspace/input/data.json").read_text(encoding="utf-8"))
df = pd.DataFrame(data)
result = df.groupby("category", as_index=False)["amount"].sum().to_dict("records")
Path("/workspace/output/result.json").write_text(
    json.dumps({"summary": "sandbox ok", "rows": result}, ensure_ascii=False),
    encoding="utf-8",
)
""".strip(),
            encoding="utf-8",
        )

        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "128",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=128m",
            "-v",
            f"{_docker_path(input_dir)}:/workspace/input:ro",
            "-v",
            f"{_docker_path(work_dir)}:/workspace/work:ro",
            "-v",
            f"{_docker_path(output_dir)}:/workspace/output:rw",
            IMAGE_NAME,
            "python",
            "/workspace/work/analysis.py",
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if completed.returncode != 0:
            raise SystemExit(
                "Python sandbox verification failed.\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )

        result_path = output_dir / "result.json"
        if not result_path.exists():
            raise SystemExit("Python sandbox verification failed: result.json was not created.")

        print(result_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
