import uuid
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


CHART_DIR = Path("app/storage/charts")
SUPPORTED_CHART_TYPES = {"bar", "line", "pie", "scatter"}


@dataclass
class ChartRecord:
    chart_id: str
    chart_type: str
    title: str
    path: Path
    url: str


class ChartService:
    """Generate chart images from SQL query results."""

    def __init__(self, chart_dir: Path = CHART_DIR) -> None:
        self.chart_dir = chart_dir

    def generate_chart(
        self,
        dataframe: pd.DataFrame,
        chart_type: str,
        x: str,
        y: str,
        title: str,
    ) -> ChartRecord:
        normalized_type = chart_type.lower().strip()
        self._validate_chart_input(dataframe, normalized_type, x, y)

        chart_id = str(uuid.uuid4())
        save_path = self.chart_dir / f"{chart_id}.png"
        save_path.parent.mkdir(parents=True, exist_ok=True)

        plt.rcParams["font.sans-serif"] = [
            "Microsoft YaHei",
            "SimHei",
            "Arial Unicode MS",
            "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False

        fig, ax = plt.subplots(figsize=(10, 6), dpi=140)

        if normalized_type == "bar":
            ax.bar(dataframe[x].astype(str), dataframe[y])
            ax.set_xlabel(x)
            ax.set_ylabel(y)
            ax.tick_params(axis="x", rotation=45)
        elif normalized_type == "line":
            ax.plot(dataframe[x], dataframe[y], marker="o")
            ax.set_xlabel(x)
            ax.set_ylabel(y)
            ax.tick_params(axis="x", rotation=45)
        elif normalized_type == "scatter":
            ax.scatter(dataframe[x], dataframe[y])
            ax.set_xlabel(x)
            ax.set_ylabel(y)
        elif normalized_type == "pie":
            ax.pie(dataframe[y], labels=dataframe[x].astype(str), autopct="%1.1f%%")
            ax.axis("equal")

        ax.set_title(title)
        fig.tight_layout()
        fig.savefig(save_path, bbox_inches="tight")
        plt.close(fig)

        return ChartRecord(
            chart_id=chart_id,
            chart_type=normalized_type,
            title=title,
            path=save_path,
            url=f"/charts/{chart_id}.png",
        )

    def _validate_chart_input(self, dataframe: pd.DataFrame, chart_type: str, x: str, y: str) -> None:
        if chart_type not in SUPPORTED_CHART_TYPES:
            raise ValueError("暂不支持该图表类型，仅支持 bar、line、pie、scatter")
        if dataframe.empty:
            raise ValueError("图表数据为空，无法生成图表")
        if x not in dataframe.columns:
            raise ValueError(f"图表数据中不存在 x 字段：{x}")
        if y not in dataframe.columns:
            raise ValueError(f"图表数据中不存在 y 字段：{y}")
        if chart_type in {"bar", "line", "pie", "scatter"} and not pd.api.types.is_numeric_dtype(
            dataframe[y]
        ):
            raise ValueError(f"y 字段必须是数值类型：{y}")
        if chart_type == "scatter" and not pd.api.types.is_numeric_dtype(dataframe[x]):
            raise ValueError(f"散点图的 x 字段必须是数值类型：{x}")
