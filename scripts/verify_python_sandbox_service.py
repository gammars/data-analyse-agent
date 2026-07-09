from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.python_sandbox_service import PythonSandboxService


def main() -> None:
    code = """
import json
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt

data = json.loads(Path("/workspace/input/data.json").read_text(encoding="utf-8"))
df = pd.DataFrame(data)
figure_path = Path("/workspace/output/correlation_analysis.png")
summary = df.groupby("category", as_index=False)["amount"].sum()
fig, ax = plt.subplots(figsize=(6, 4), dpi=120)
ax.bar(summary["category"], summary["amount"])
ax.set_title("Sandbox verification chart")
ax.set_xlabel("category")
ax.set_ylabel("amount")
fig.tight_layout()
fig.savefig(figure_path)
plt.close(fig)
Path("/workspace/output/result.json").write_text(
    json.dumps(
        {
            "summary": "python sandbox service ok",
            "total_amount": int(df["amount"].sum()),
            "row_count": int(len(df)),
            "figures": [
                {
                    "title": "沙箱验证图",
                    "path": str(figure_path),
                }
            ],
        },
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
""".strip()

    result = PythonSandboxService().run_analysis(
        dataframe=pd.DataFrame(
            [
                {"category": "A", "amount": 10},
                {"category": "A", "amount": 20},
                {"category": "B", "amount": 5},
            ]
        ),
        analysis_goal="验证 PythonSandboxService 能通过 Docker 执行脚本并读取 result.json",
        python_code=code,
    )
    print(
        json.dumps(
            {
                "result": result.result,
                "figures": [
                    {
                        "chart_id": figure.chart_id,
                        "chart_url": figure.url,
                        "title": figure.title,
                    }
                    for figure in result.figures
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
