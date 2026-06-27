from datetime import datetime, timezone
from typing import Any

import pandas as pd
from pandas.api.types import is_numeric_dtype

from app.schemas.manifest import CleaningStepManifest
from app.schemas.preprocessing import CleaningOperation
from app.services.dataset_service import DatasetService


class PreprocessingService:
    """Suggest and apply bounded cleaning operations to processed dataset tables."""

    def __init__(self, dataset_service: DatasetService) -> None:
        self.dataset_service = dataset_service

    def suggest_cleaning(
        self,
        dataset_id: str,
        table_name: str | None = None,
    ) -> dict[str, Any]:
        tables = self._select_tables(dataset_id, table_name)
        reports = [
            self._suggest_for_table(
                table.table_name,
                self.dataset_service.get_table_dataframe(dataset_id, table.table_name),
            )
            for table in tables
        ]
        return {
            "dataset_id": dataset_id,
            "mode": "suggest_only",
            "message": "以下内容仅为建议，尚未修改 processed 数据。",
            "tables": reports,
        }

    def apply_cleaning(
        self,
        dataset_id: str,
        table_name: str,
        operations: list[CleaningOperation],
    ) -> dict[str, Any]:
        if not operations:
            raise ValueError("请至少提供一个清洗操作")

        original = self.dataset_service.get_table_dataframe(dataset_id, table_name)
        dataframe = original.copy(deep=True)
        operation_results = []

        for operation in operations:
            before_rows = int(len(dataframe))
            before_columns = int(len(dataframe.columns))
            dataframe = self._apply_operation(dataframe, operation)
            if len(dataframe.columns) == 0:
                raise ValueError("清洗操作不能删除数据表的全部字段")
            dataframe = dataframe.reset_index(drop=True)
            operation_results.append(
                {
                    "operation": operation.operation,
                    "parameters": operation.model_dump(exclude_none=True),
                    "before_rows": before_rows,
                    "after_rows": int(len(dataframe)),
                    "before_columns": before_columns,
                    "after_columns": int(len(dataframe.columns)),
                }
            )

        record = self.dataset_service.replace_processed_tables(
            dataset_id,
            {table_name: dataframe},
        )
        manifest = self.dataset_service.manifests.load(record.manifest_path)
        if manifest is None:
            raise RuntimeError("清洗完成，但无法读取数据集 Manifest")

        table_manifest = next(
            (table for table in manifest.tables if table.table_name == table_name),
            None,
        )
        if table_manifest is None:
            raise RuntimeError(f"Manifest 中不存在数据表：{table_name}")

        applied_at = self._now()
        for operation, result in zip(operations, operation_results, strict=True):
            parameters = operation.model_dump(exclude_none=True)
            parameters.pop("operation", None)
            table_manifest.cleaning_steps.append(
                CleaningStepManifest(
                    operation=operation.operation,
                    parameters=parameters,
                    applied_at=applied_at,
                    applied_by="tool",
                    before_rows=result["before_rows"],
                    after_rows=result["after_rows"],
                )
            )
            if operation.column:
                column_manifest = next(
                    (
                        column
                        for column in table_manifest.columns
                        if column.name == operation.column
                    ),
                    None,
                )
                if column_manifest:
                    column_manifest.cleaning_rules.append(
                        {"operation": operation.operation, **parameters}
                    )

        table_manifest.cleaning_status = "applied"
        manifest.processing_status = "processed"
        manifest.updated_at = applied_at
        self.dataset_service.manifests.write(record.manifest_path, manifest)

        return {
            "dataset_id": dataset_id,
            "table_name": table_name,
            "before_rows": int(len(original)),
            "after_rows": int(len(dataframe)),
            "before_columns": int(len(original.columns)),
            "after_columns": int(len(dataframe.columns)),
            "operations": operation_results,
            "sqlite_rebuilt": True,
            "raw_unchanged": True,
        }

    def reset_cleaning(
        self,
        dataset_id: str,
        table_name: str | None = None,
    ) -> dict[str, Any]:
        tables = self._select_tables(dataset_id, table_name)
        updates = {
            table.table_name: self.dataset_service.get_raw_table_dataframe(
                dataset_id,
                table.table_name,
            )
            for table in tables
        }
        record = self.dataset_service.replace_processed_tables(dataset_id, updates)
        manifest = self.dataset_service.manifests.load(record.manifest_path)
        if manifest is None:
            raise RuntimeError("恢复完成，但无法读取数据集 Manifest")

        reset_names = set(updates)
        for table_manifest in manifest.tables:
            if table_manifest.table_name not in reset_names:
                continue
            table_manifest.cleaning_status = "not_started"
            table_manifest.cleaning_steps = []
            for column in table_manifest.columns:
                column.cleaning_rules = []

        manifest.processing_status = (
            "processed"
            if any(table.cleaning_steps for table in manifest.tables)
            else "not_started"
        )
        manifest.updated_at = self._now()
        self.dataset_service.manifests.write(record.manifest_path, manifest)

        return {
            "dataset_id": dataset_id,
            "reset_tables": sorted(reset_names),
            "sqlite_rebuilt": True,
            "raw_unchanged": True,
        }

    def _select_tables(self, dataset_id: str, table_name: str | None) -> list:
        tables = self.dataset_service.iter_tables(dataset_id)
        if table_name is None:
            return tables
        selected = [table for table in tables if table.table_name == table_name]
        if not selected:
            raise KeyError(f"数据表不存在：{table_name}")
        return selected

    def _suggest_for_table(
        self,
        table_name: str,
        dataframe: pd.DataFrame,
    ) -> dict[str, Any]:
        suggestions = []
        duplicate_rows = int(dataframe.duplicated().sum())
        empty_rows = int(dataframe.isna().all(axis=1).sum())
        empty_columns = [
            str(column) for column in dataframe.columns if dataframe[column].isna().all()
        ]

        if duplicate_rows:
            suggestions.append(
                {
                    "reason": f"发现 {duplicate_rows} 行完全重复记录，请确认是否删除。",
                    "operation": {"operation": "drop_duplicate_rows"},
                }
            )
        if empty_rows:
            suggestions.append(
                {
                    "reason": f"发现 {empty_rows} 行全空记录。",
                    "operation": {"operation": "drop_empty_rows"},
                }
            )
        if empty_columns:
            suggestions.append(
                {
                    "reason": f"以下字段全部为空：{', '.join(empty_columns)}。",
                    "operation": {"operation": "drop_empty_columns"},
                }
            )

        for column in dataframe.columns:
            series = dataframe[column]
            column_name = str(column)
            missing_count = int(series.isna().sum())
            if missing_count:
                strategy = "fill_median" if is_numeric_dtype(series) else "fill_mode"
                suggestions.append(
                    {
                        "reason": (
                            f"字段 {column_name} 有 {missing_count} 个缺失值；"
                            "填充或删除可能影响业务含义，需要用户确认。"
                        ),
                        "operation": {
                            "operation": "handle_missing",
                            "column": column_name,
                            "strategy": strategy,
                        },
                        "alternatives": ["keep", "drop_rows", "fill_constant"],
                    }
                )

            if not self._is_string_series(series):
                continue
            non_null = series.dropna()
            if non_null.empty:
                continue
            text = non_null.astype(str)
            whitespace_count = int((text != text.str.strip()).sum())
            if whitespace_count:
                suggestions.append(
                    {
                        "reason": f"字段 {column_name} 有 {whitespace_count} 个值包含首尾空格。",
                        "operation": {
                            "operation": "trim_strings",
                            "column": column_name,
                        },
                    }
                )

            sample = text.head(2000)
            normalized = {value.strip().casefold() for value in sample}
            boolean_values = {"true", "false", "yes", "no", "y", "n", "是", "否", "0", "1"}
            if normalized and normalized <= boolean_values:
                suggestions.append(
                    {
                        "reason": f"字段 {column_name} 的值看起来可以转换为布尔类型。",
                        "confidence": 1.0,
                        "operation": {
                            "operation": "convert_type",
                            "column": column_name,
                            "target_type": "boolean",
                        },
                    }
                )
                continue

            numeric_ratio = float(pd.to_numeric(sample, errors="coerce").notna().mean())
            if numeric_ratio >= 0.95:
                suggestions.append(
                    {
                        "reason": f"字段 {column_name} 有 {numeric_ratio:.1%} 的样本可解析为数值。",
                        "confidence": round(numeric_ratio, 4),
                        "operation": {
                            "operation": "convert_type",
                            "column": column_name,
                            "target_type": "float",
                            "errors": "coerce",
                        },
                    }
                )
                continue

            if self._looks_like_datetime(column_name, sample):
                datetime_ratio = float(
                    pd.to_datetime(sample, errors="coerce").notna().mean()
                )
                if datetime_ratio >= 0.9:
                    suggestions.append(
                        {
                            "reason": f"字段 {column_name} 有 {datetime_ratio:.1%} 的样本可解析为日期。",
                            "confidence": round(datetime_ratio, 4),
                            "operation": {
                                "operation": "convert_type",
                                "column": column_name,
                                "target_type": "datetime",
                                "errors": "coerce",
                            },
                        }
                    )

        return {
            "table_name": table_name,
            "row_count": int(len(dataframe)),
            "column_count": int(len(dataframe.columns)),
            "duplicate_rows": duplicate_rows,
            "empty_rows": empty_rows,
            "empty_columns": empty_columns,
            "suggestions": suggestions,
        }

    def _apply_operation(
        self,
        dataframe: pd.DataFrame,
        operation: CleaningOperation,
    ) -> pd.DataFrame:
        name = operation.operation
        if name == "drop_duplicate_rows":
            return dataframe.drop_duplicates()
        if name == "drop_empty_rows":
            return dataframe.dropna(how="all")
        if name == "drop_empty_columns":
            return dataframe.dropna(axis=1, how="all")
        if name == "trim_strings":
            columns = (
                [self._require_column(dataframe, operation.column)]
                if operation.column
                else [column for column in dataframe.columns if self._is_string_series(dataframe[column])]
            )
            result = dataframe.copy()
            for column in columns:
                result[column] = result[column].map(
                    lambda value: value.strip() if isinstance(value, str) else value
                )
            return result
        if name == "convert_type":
            column = self._require_column(dataframe, operation.column)
            if not operation.target_type:
                raise ValueError("convert_type 必须提供 target_type")
            result = dataframe.copy()
            result[column] = self._convert_series(
                result[column],
                operation.target_type,
                operation.errors,
            )
            return result
        if name == "handle_missing":
            column = self._require_column(dataframe, operation.column)
            if not operation.strategy:
                raise ValueError("handle_missing 必须提供 strategy")
            return self._handle_missing(dataframe, column, operation)
        if name == "sample_rows":
            if operation.sample_size is None:
                raise ValueError("sample_rows 必须提供 sample_size")
            if operation.sample_size > len(dataframe):
                raise ValueError("sample_size 不能超过当前数据表行数")
            return dataframe.sample(
                n=operation.sample_size,
                random_state=operation.random_state,
            )
        raise ValueError(f"不支持的清洗操作：{name}")

    def _handle_missing(
        self,
        dataframe: pd.DataFrame,
        column: str,
        operation: CleaningOperation,
    ) -> pd.DataFrame:
        strategy = operation.strategy
        if strategy == "keep":
            return dataframe
        if strategy == "drop_rows":
            return dataframe.dropna(subset=[column])

        result = dataframe.copy()
        if strategy == "fill_constant":
            if operation.value is None:
                raise ValueError("fill_constant 必须提供非空 value")
            fill_value = operation.value
        elif strategy in {"fill_mean", "fill_median"}:
            if not is_numeric_dtype(result[column]):
                raise ValueError(f"字段 {column} 不是数值类型，不能使用 {strategy}")
            fill_value = (
                result[column].mean()
                if strategy == "fill_mean"
                else result[column].median()
            )
        elif strategy == "fill_mode":
            modes = result[column].mode(dropna=True)
            if modes.empty:
                raise ValueError(f"字段 {column} 没有可用于填充的众数")
            fill_value = modes.iloc[0]
        else:
            raise ValueError(f"不支持的缺失值策略：{strategy}")
        result[column] = result[column].fillna(fill_value)
        return result

    def _convert_series(
        self,
        series: pd.Series,
        target_type: str,
        errors: str,
    ) -> pd.Series:
        if target_type == "string":
            return series.astype("string")
        if target_type == "float":
            return pd.to_numeric(series, errors=errors).astype("Float64")
        if target_type == "integer":
            numeric = pd.to_numeric(series, errors=errors)
            non_null = numeric.dropna()
            if not ((non_null % 1) == 0).all():
                raise ValueError("字段包含非整数数值，不能安全转换为 integer")
            return numeric.astype("Int64")
        if target_type == "datetime":
            return pd.to_datetime(series, errors=errors)
        if target_type == "boolean":
            mapping = {
                "true": True,
                "yes": True,
                "y": True,
                "是": True,
                "1": True,
                "false": False,
                "no": False,
                "n": False,
                "否": False,
                "0": False,
            }
            converted = series.map(
                lambda value: (
                    pd.NA
                    if pd.isna(value)
                    else mapping.get(str(value).strip().casefold(), pd.NA)
                )
            )
            if errors == "raise" and converted.isna().sum() > series.isna().sum():
                raise ValueError("字段包含无法识别的布尔值")
            return converted.astype("boolean")
        raise ValueError(f"不支持的目标类型：{target_type}")

    def _require_column(self, dataframe: pd.DataFrame, column: str | None) -> str:
        if not column:
            raise ValueError("该清洗操作必须指定 column")
        if column not in dataframe.columns:
            raise ValueError(f"数据表中不存在字段：{column}")
        return column

    def _is_string_series(self, series: pd.Series) -> bool:
        return bool(
            pd.api.types.is_object_dtype(series)
            or pd.api.types.is_string_dtype(series)
        )

    def _looks_like_datetime(self, column_name: str, sample: pd.Series) -> bool:
        name = column_name.casefold()
        name_hint = any(
            token in name
            for token in ("date", "time", "日期", "时间", "created", "updated")
        )
        value_hint = bool(sample.str.contains(r"[-/:]", regex=True).mean() >= 0.8)
        return name_hint or value_hint

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
