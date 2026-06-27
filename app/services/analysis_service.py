import pandas as pd

from app.services.dataset_service import DatasetService


class AnalysisService:
    """Provide bounded data profiling and quality analysis helpers."""

    def __init__(self, dataset_service: DatasetService) -> None:
        self.dataset_service = dataset_service

    def profile_data(self, dataset_id: str) -> str:
        sections = ["# 数据概览"]
        for table_name, dataframe in self._iter_dataframes(dataset_id):
            sections.extend(
                [
                    f"## 表：{table_name}",
                    self._shape_summary(dataframe),
                    self._field_types_for(dataframe, "### 字段类型"),
                    self._missing_value_analysis_for(dataframe, "### 缺失值分析"),
                    self._descriptive_statistics_for(dataframe, "### 描述性统计"),
                    self._correlation_analysis_for(dataframe, threshold=0.6, heading="### 相关性分析"),
                    self._outlier_detection_for(dataframe, "### 异常值检测"),
                ]
            )
        return "\n\n".join(section for section in sections if section)

    def field_types(self, dataset_id: str) -> str:
        sections = ["# 字段类型"]
        for table_name, dataframe in self._iter_dataframes(dataset_id):
            sections.append(f"## 表：{table_name}")
            sections.append(self._field_types_for(dataframe, "### 字段类型"))
        return "\n\n".join(sections)

    def missing_value_analysis(self, dataset_id: str) -> str:
        sections = ["# 缺失值分析"]
        for table_name, dataframe in self._iter_dataframes(dataset_id):
            sections.append(f"## 表：{table_name}")
            sections.append(self._missing_value_analysis_for(dataframe, "### 缺失值分析"))
        return "\n\n".join(sections)

    def descriptive_statistics(self, dataset_id: str) -> str:
        sections = ["# 描述性统计"]
        for table_name, dataframe in self._iter_dataframes(dataset_id):
            sections.append(f"## 表：{table_name}")
            sections.append(self._descriptive_statistics_for(dataframe, "### 描述性统计"))
        return "\n\n".join(sections)

    def correlation_analysis(self, dataset_id: str, threshold: float = 0.6) -> str:
        sections = ["# 相关性分析"]
        for table_name, dataframe in self._iter_dataframes(dataset_id):
            sections.append(f"## 表：{table_name}")
            sections.append(self._correlation_analysis_for(dataframe, threshold, "### 相关性分析"))
        return "\n\n".join(sections)

    def outlier_detection(self, dataset_id: str) -> str:
        sections = ["# 异常值检测"]
        for table_name, dataframe in self._iter_dataframes(dataset_id):
            sections.append(f"## 表：{table_name}")
            sections.append(self._outlier_detection_for(dataframe, "### 异常值检测"))
        return "\n\n".join(sections)

    def _iter_dataframes(self, dataset_id: str) -> list[tuple[str, pd.DataFrame]]:
        return [
            (table.table_name, self.dataset_service.get_table_dataframe(dataset_id, table.table_name))
            for table in self.dataset_service.iter_tables(dataset_id)
        ]

    def _field_types_for(self, dataframe: pd.DataFrame, heading: str) -> str:
        rows = [
            {
                "字段名": str(column),
                "类型": str(dataframe[column].dtype),
                "非空数量": int(dataframe[column].notna().sum()),
                "唯一值数量": int(dataframe[column].nunique(dropna=True)),
            }
            for column in dataframe.columns
        ]
        return heading + "\n" + pd.DataFrame(rows).to_markdown(index=False)

    def _missing_value_analysis_for(self, dataframe: pd.DataFrame, heading: str) -> str:
        total_rows = len(dataframe)
        rows = []

        for column in dataframe.columns:
            missing_count = int(dataframe[column].isna().sum())
            missing_rate = missing_count / total_rows if total_rows else 0
            rows.append(
                {
                    "字段名": str(column),
                    "缺失值数量": missing_count,
                    "缺失率": f"{missing_rate:.2%}",
                }
            )

        missing_df = pd.DataFrame(rows).sort_values("缺失值数量", ascending=False)
        total_missing = int(dataframe.isna().sum().sum())
        return (
            f"{heading}\n"
            f"- 总缺失值数量：{total_missing}\n"
            f"- 存在缺失值的字段数：{int((dataframe.isna().sum() > 0).sum())}\n\n"
            + missing_df.to_markdown(index=False)
        )

    def _descriptive_statistics_for(self, dataframe: pd.DataFrame, heading: str) -> str:
        numeric = dataframe.select_dtypes(include="number")
        categorical = dataframe.select_dtypes(exclude="number")
        sections = [heading]

        if numeric.empty:
            sections.append("没有可用于描述性统计的数值字段。")
        else:
            sections.append("#### 数值字段")
            sections.append(numeric.describe().transpose().round(4).to_markdown())

        if not categorical.empty:
            rows = []
            for column in categorical.columns:
                mode = categorical[column].mode(dropna=True)
                value_counts = categorical[column].value_counts(dropna=True)
                rows.append(
                    {
                        "字段名": str(column),
                        "唯一值数量": int(categorical[column].nunique(dropna=True)),
                        "最常见值": "" if mode.empty else str(mode.iloc[0]),
                        "最常见值出现次数": int(value_counts.iloc[0]) if not value_counts.empty else 0,
                    }
                )
            sections.append("#### 非数值字段")
            sections.append(pd.DataFrame(rows).to_markdown(index=False))

        return "\n\n".join(sections)

    def _correlation_analysis_for(self, dataframe: pd.DataFrame, threshold: float, heading: str) -> str:
        numeric = dataframe.select_dtypes(include="number")

        if len(numeric.columns) < 2:
            return f"{heading}\n数值字段少于 2 个，无法计算相关性。"

        corr = numeric.corr(numeric_only=True).round(4)
        pairs = []

        for index, left in enumerate(corr.columns):
            for right in corr.columns[index + 1 :]:
                value = corr.loc[left, right]
                if pd.notna(value) and abs(value) >= threshold:
                    pairs.append(
                        {
                            "字段A": str(left),
                            "字段B": str(right),
                            "相关系数": float(round(value, 4)),
                        }
                    )

        sections = [heading, "#### 相关系数矩阵", corr.to_markdown()]
        if pairs:
            sections.append(f"#### 强相关字段对（|r| >= {threshold}）")
            sections.append(pd.DataFrame(pairs).to_markdown(index=False))
        else:
            sections.append(f"未发现 |r| >= {threshold} 的强相关字段对。")

        return "\n\n".join(sections)

    def _outlier_detection_for(self, dataframe: pd.DataFrame, heading: str) -> str:
        numeric = dataframe.select_dtypes(include="number")

        if numeric.empty:
            return f"{heading}\n没有数值字段，无法基于 IQR 方法检测异常值。"

        rows = []
        for column in numeric.columns:
            series = numeric[column].dropna()
            if series.empty:
                rows.append(self._empty_outlier_row(column))
                continue

            q1 = float(series.quantile(0.25))
            q3 = float(series.quantile(0.75))
            iqr = q3 - q1
            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr
            outlier_count = int(((series < lower) | (series > upper)).sum())
            outlier_rate = outlier_count / len(series) if len(series) else 0

            rows.append(
                {
                    "字段名": str(column),
                    "下界": round(lower, 4),
                    "上界": round(upper, 4),
                    "异常值数量": outlier_count,
                    "异常值比例": f"{outlier_rate:.2%}",
                }
            )

        outlier_df = pd.DataFrame(rows).sort_values("异常值数量", ascending=False)
        return heading + "\n" + outlier_df.to_markdown(index=False)

    def _shape_summary(self, dataframe: pd.DataFrame) -> str:
        duplicated_rows = int(dataframe.duplicated().sum())
        return (
            f"- 行数：{len(dataframe)}\n"
            f"- 列数：{len(dataframe.columns)}\n"
            f"- 重复行数量：{duplicated_rows}"
        )

    def _empty_outlier_row(self, column: str) -> dict:
        return {
            "字段名": str(column),
            "下界": "",
            "上界": "",
            "异常值数量": 0,
            "异常值比例": "0.00%",
        }
