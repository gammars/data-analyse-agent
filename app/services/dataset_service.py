import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv


DATASET_DIR = Path("app/storage/datasets")
ALLOWED_SUFFIXES = {".csv", ".xlsx", ".xls"}
MAX_UPLOAD_SIZE_BYTES = None
DEFAULT_SINGLE_TABLE_ALIAS = "data_table"


def get_upload_size_limit_bytes() -> int | None:
    load_dotenv()
    limit_mb = os.getenv("DATA_ANALYSE_MAX_UPLOAD_MB", "").strip()
    if not limit_mb:
        return MAX_UPLOAD_SIZE_BYTES

    try:
        parsed_limit = float(limit_mb)
    except ValueError:
        return MAX_UPLOAD_SIZE_BYTES

    if parsed_limit <= 0:
        return None
    return int(parsed_limit * 1024 * 1024)


def quote_sql_identifier(identifier: object) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def sanitize_table_name(name: str) -> str:
    value = re.sub(r"\W+", "_", name.strip(), flags=re.UNICODE).strip("_")
    if not value:
        return DEFAULT_SINGLE_TABLE_ALIAS
    if value[0].isdigit():
        value = f"t_{value}"
    return value[:64]


def unique_table_name(base_name: str, existing_names: set[str]) -> str:
    candidate = sanitize_table_name(base_name)
    if candidate not in existing_names:
        return candidate

    index = 2
    while f"{candidate}_{index}" in existing_names:
        index += 1
    return f"{candidate}_{index}"


@dataclass
class TableRecord:
    table_name: str
    filename: str
    path: Path
    suffix: str
    created_at: str
    sheet_name: str | None = None
    dataframe: pd.DataFrame | None = None


@dataclass
class DatasetRecord:
    dataset_id: str
    filename: str
    schema: str
    created_at: str
    tables: dict[str, TableRecord]


class DatasetService:
    """Save uploaded datasets and expose single-table or multi-table metadata."""

    def __init__(self, dataset_dir: Path = DATASET_DIR) -> None:
        self.dataset_dir = dataset_dir
        self.metadata_path = dataset_dir / "metadata.json"
        self.datasets: dict[str, DatasetRecord] = {}
        self._load_metadata()
        self._index_existing_files()

    def save_dataset(self, filename: str, content: bytes) -> DatasetRecord:
        return self.save_dataset_files([(filename, content)])

    def save_dataset_files(self, files: list[tuple[str, bytes]]) -> DatasetRecord:
        if not files:
            raise ValueError("请至少上传一个数据文件")

        for filename, content in files:
            suffix = Path(filename).suffix.lower()
            self._validate_upload(filename=filename, suffix=suffix, content=content)

        dataset_id = str(uuid.uuid4())
        dataset_path = self.dataset_dir / dataset_id
        dataset_path.mkdir(parents=True, exist_ok=True)
        dataset_name = self._build_dataset_name([filename for filename, _ in files])

        record = DatasetRecord(
            dataset_id=dataset_id,
            filename=dataset_name,
            schema="",
            created_at=self._now(),
            tables={},
        )
        existing_names: set[str] = set()
        for filename, content in files:
            suffix = Path(filename).suffix.lower()
            tables = self._materialize_uploaded_tables(
                dataset_path=dataset_path,
                filename=filename,
                content=content,
                suffix=suffix,
                existing_names=existing_names,
            )
            record.tables.update(tables)
            existing_names.update(tables)

        record.schema = self._build_schema(record)
        self.datasets[dataset_id] = record
        self._save_metadata()
        return record

    def append_table(self, dataset_id: str, filename: str, content: bytes) -> DatasetRecord:
        return self.append_tables(dataset_id, [(filename, content)])

    def append_tables(self, dataset_id: str, files: list[tuple[str, bytes]]) -> DatasetRecord:
        record = self._get_record(dataset_id)
        if not files:
            raise ValueError("请至少上传一个数据文件")

        for filename, content in files:
            suffix = Path(filename).suffix.lower()
            self._validate_upload(filename=filename, suffix=suffix, content=content)

        dataset_path = self.dataset_dir / dataset_id
        dataset_path.mkdir(parents=True, exist_ok=True)
        existing_names = set(record.tables)
        for filename, content in files:
            suffix = Path(filename).suffix.lower()
            tables = self._materialize_uploaded_tables(
                dataset_path=dataset_path,
                filename=filename,
                content=content,
                suffix=suffix,
                existing_names=existing_names,
            )
            record.tables.update(tables)
            existing_names.update(tables)

        record.schema = self._build_schema(record)
        self._save_metadata()
        return record

    def rename_dataset(self, dataset_id: str, name: str) -> DatasetRecord:
        record = self._get_record(dataset_id)
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("数据集名称不能为空")
        record.filename = clean_name
        record.schema = self._build_schema(record)
        self._save_metadata()
        return record

    def delete_dataset(self, dataset_id: str) -> None:
        record = self._get_record(dataset_id)
        dataset_path = self.dataset_dir / dataset_id

        if dataset_path.exists() and dataset_path.is_dir():
            shutil.rmtree(dataset_path)
        else:
            for table in record.tables.values():
                if table.path.exists() and self._is_inside_dataset_dir(table.path):
                    table.path.unlink()

        del self.datasets[dataset_id]
        self._save_metadata()

    def delete_table(self, dataset_id: str, table_name: str) -> DatasetRecord:
        record = self._get_record(dataset_id)
        if table_name not in record.tables:
            raise KeyError(f"数据表不存在：{table_name}")
        if len(record.tables) <= 1:
            raise ValueError("数据集至少需要保留一张表；如需删除全部数据，请删除整个数据集")

        table = record.tables.pop(table_name)
        if not any(other.path == table.path for other in record.tables.values()):
            if table.path.exists() and self._is_inside_dataset_dir(table.path):
                table.path.unlink()

        record.schema = self._build_schema(record)
        self._save_metadata()
        return record

    def list_datasets(self) -> list[dict[str, Any]]:
        return [
            self.get_summary(dataset_id)
            for dataset_id in sorted(
                self.datasets,
                key=lambda key: self.datasets[key].created_at,
                reverse=True,
            )
        ]

    def get_dataframe(self, dataset_id: str) -> pd.DataFrame:
        table = self._get_first_table(dataset_id)
        return self.get_table_dataframe(dataset_id, table.table_name)

    def get_table_dataframe(self, dataset_id: str, table_name: str) -> pd.DataFrame:
        record = self._get_record(dataset_id)
        try:
            table = record.tables[table_name]
        except KeyError as exc:
            raise KeyError(f"数据表不存在：{table_name}") from exc

        if table.dataframe is None:
            table.dataframe = self._read_table_dataframe(table)
        return table.dataframe

    def iter_tables(self, dataset_id: str) -> list[TableRecord]:
        return list(self._get_record(dataset_id).tables.values())

    def get_schema(self, dataset_id: str) -> str:
        record = self._get_record(dataset_id)
        if not record.schema:
            record.schema = self._build_schema(record)
        return record.schema

    def get_summary(self, dataset_id: str) -> dict[str, Any]:
        record = self._get_record(dataset_id)
        first_table = self._get_first_table(dataset_id)
        first_dataframe = self.get_table_dataframe(dataset_id, first_table.table_name)
        table_summaries = [self._table_summary(record.dataset_id, table) for table in record.tables.values()]

        return {
            "dataset_id": record.dataset_id,
            "filename": record.filename,
            "created_at": record.created_at,
            "table_count": len(record.tables),
            "row_count": int(len(first_dataframe)),
            "column_count": int(len(first_dataframe.columns)),
            "columns": self._column_summaries(first_dataframe),
            "sample_rows": first_dataframe.head(5).where(pd.notna(first_dataframe), None).to_dict("records"),
            "tables": table_summaries,
            "schema": self.get_schema(dataset_id),
        }

    def _get_record(self, dataset_id: str) -> DatasetRecord:
        try:
            return self.datasets[dataset_id]
        except KeyError as exc:
            raise KeyError(f"数据集不存在：{dataset_id}") from exc

    def _get_first_table(self, dataset_id: str) -> TableRecord:
        record = self._get_record(dataset_id)
        try:
            return next(iter(record.tables.values()))
        except StopIteration as exc:
            raise ValueError(f"数据集没有可用的数据表：{dataset_id}") from exc

    def _build_dataset_name(self, filenames: list[str]) -> str:
        names = [Path(filename).name for filename in filenames if filename]
        if len(names) == 1:
            return names[0]
        first_stem = Path(names[0]).stem if names else "多文件数据集"
        return f"{first_stem} 等 {len(names)} 个文件"

    def _materialize_uploaded_tables(
        self,
        dataset_path: Path,
        filename: str,
        content: bytes,
        suffix: str,
        existing_names: set[str],
    ) -> dict[str, TableRecord]:
        now = self._now()
        source_stem = sanitize_table_name(Path(filename).stem)

        if suffix == ".csv":
            table_name = unique_table_name(source_stem, existing_names)
            save_path = dataset_path / f"{table_name}{suffix}"
            save_path.write_bytes(content)
            dataframe = self._read_dataframe(save_path, suffix)
            return {
                table_name: TableRecord(
                    table_name=table_name,
                    filename=Path(filename).name,
                    path=save_path,
                    suffix=suffix,
                    created_at=now,
                    dataframe=dataframe,
                )
            }

        workbook_name = unique_table_name(source_stem, {path.stem for path in dataset_path.glob("*")})
        save_path = dataset_path / f"{workbook_name}{suffix}"
        save_path.write_bytes(content)
        sheets = pd.read_excel(save_path, sheet_name=None)

        tables: dict[str, TableRecord] = {}
        names = set(existing_names)
        for sheet_name, dataframe in sheets.items():
            table_name = unique_table_name(str(sheet_name), names)
            names.add(table_name)
            tables[table_name] = TableRecord(
                table_name=table_name,
                filename=Path(filename).name,
                path=save_path,
                suffix=suffix,
                sheet_name=str(sheet_name),
                created_at=now,
                dataframe=dataframe,
            )
        return tables

    def _load_metadata(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            return

        try:
            records = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return

        for item in records:
            dataset_id = item.get("dataset_id")
            if not dataset_id:
                continue

            if item.get("tables"):
                record = self._load_multitable_record(item)
            else:
                record = self._load_legacy_record(item)

            if record:
                self.datasets[dataset_id] = record

    def _load_multitable_record(self, item: dict[str, Any]) -> DatasetRecord | None:
        tables: dict[str, TableRecord] = {}
        for table_item in item.get("tables", []):
            table_name = table_item.get("table_name")
            path = Path(table_item.get("path", ""))
            suffix = table_item.get("suffix") or path.suffix.lower()
            if not table_name or not path.exists() or suffix not in ALLOWED_SUFFIXES:
                continue
            tables[table_name] = TableRecord(
                table_name=table_name,
                filename=table_item.get("filename", path.name),
                path=path,
                suffix=suffix,
                sheet_name=table_item.get("sheet_name"),
                created_at=table_item.get("created_at", item.get("created_at", self._now())),
            )

        if not tables:
            return None

        return DatasetRecord(
            dataset_id=item["dataset_id"],
            filename=item.get("filename", item["dataset_id"]),
            schema=item.get("schema", ""),
            created_at=item.get("created_at", self._now()),
            tables=tables,
        )

    def _load_legacy_record(self, item: dict[str, Any]) -> DatasetRecord | None:
        path = Path(item.get("path", ""))
        suffix = item.get("suffix") or path.suffix.lower()
        if not item.get("dataset_id") or not path.exists() or suffix not in ALLOWED_SUFFIXES:
            return None

        table_name = sanitize_table_name(path.stem)
        table = TableRecord(
            table_name=table_name,
            filename=item.get("filename", path.name),
            path=path,
            suffix=suffix,
            created_at=item.get("created_at", self._now()),
        )
        return DatasetRecord(
            dataset_id=item["dataset_id"],
            filename=item.get("filename", path.name),
            schema="",
            created_at=item.get("created_at", self._now()),
            tables={table_name: table},
        )

    def _save_metadata(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        records = [
            {
                "dataset_id": record.dataset_id,
                "filename": record.filename,
                "schema": record.schema,
                "created_at": record.created_at,
                "tables": [
                    {
                        "table_name": table.table_name,
                        "filename": table.filename,
                        "path": str(table.path),
                        "suffix": table.suffix,
                        "sheet_name": table.sheet_name,
                        "created_at": table.created_at,
                    }
                    for table in record.tables.values()
                ],
            }
            for record in self.datasets.values()
        ]
        self.metadata_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _index_existing_files(self) -> None:
        known_paths = {
            table.path.resolve()
            for record in self.datasets.values()
            for table in record.tables.values()
        }
        changed = False

        for path in self.dataset_dir.iterdir():
            suffix = path.suffix.lower()
            if not path.is_file() or suffix not in ALLOWED_SUFFIXES:
                continue
            if path.resolve() in known_paths:
                continue

            dataset_id = path.stem
            table_name = sanitize_table_name(path.stem)
            table = TableRecord(
                table_name=table_name,
                filename=path.name,
                path=path,
                suffix=suffix,
                created_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            )
            record = DatasetRecord(
                dataset_id=dataset_id,
                filename=path.name,
                schema="",
                created_at=table.created_at,
                tables={table_name: table},
            )
            try:
                table.dataframe = self._read_table_dataframe(table)
                record.schema = self._build_schema(record)
            except Exception:
                continue

            self.datasets[dataset_id] = record
            changed = True

        if changed:
            self._save_metadata()

    def _validate_upload(self, filename: str, suffix: str, content: bytes) -> None:
        if not filename:
            raise ValueError("上传文件缺少文件名")
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError("暂不支持该文件类型，仅支持 CSV、XLSX、XLS")
        if not content:
            raise ValueError("上传文件为空")
        upload_limit = get_upload_size_limit_bytes()
        if upload_limit is not None and len(content) > upload_limit:
            limit_mb = upload_limit / 1024 / 1024
            raise ValueError(f"上传文件超过 {limit_mb:g}MB 限制")

    def _read_dataframe(self, save_path: Path, suffix: str, sheet_name: str | None = None) -> pd.DataFrame:
        if suffix == ".csv":
            try:
                return pd.read_csv(save_path, encoding="utf-8-sig")
            except UnicodeDecodeError:
                return pd.read_csv(save_path, encoding="gbk")

        if sheet_name is None:
            return pd.read_excel(save_path)
        return pd.read_excel(save_path, sheet_name=sheet_name)

    def _read_table_dataframe(self, table: TableRecord) -> pd.DataFrame:
        return self._read_dataframe(table.path, table.suffix, table.sheet_name)

    def _build_schema(self, record: DatasetRecord) -> str:
        lines = [
            f"数据集 ID：{record.dataset_id}",
            f"数据集文件：{record.filename}",
            f"数据表数量：{len(record.tables)}",
            "",
            "SQL 说明：",
            "- 多表查询时，请使用下方每张表的 SQL表名。",
            f"- 如果数据集只有 1 张表，也可以使用兼容别名 {DEFAULT_SINGLE_TABLE_ALIAS}。",
        ]

        for table in record.tables.values():
            if table.dataframe is None:
                table.dataframe = self._read_table_dataframe(table)
            dataframe = table.dataframe
            source = table.filename
            if table.sheet_name:
                source = f"{source} / sheet: {table.sheet_name}"
            lines.extend(
                [
                    "",
                    f"## 表：{table.table_name}",
                    f"- SQL表名：{quote_sql_identifier(table.table_name)}",
                    f"- 来源：{source}",
                    f"- 行数：{len(dataframe)}",
                    f"- 列数：{len(dataframe.columns)}",
                    "- 字段信息：",
                ]
            )

            for column in dataframe.columns:
                missing_count = int(dataframe[column].isna().sum())
                lines.append(
                    "  - "
                    f"字段名：{column}; "
                    f"SQL引用：{quote_sql_identifier(column)}; "
                    f"类型：{dataframe[column].dtype}; "
                    f"缺失值：{missing_count} 个"
                )

            lines.append("- 样例数据：")
            lines.append(dataframe.head(5).to_markdown(index=False))

        return "\n".join(lines)

    def _table_summary(self, dataset_id: str, table: TableRecord) -> dict[str, Any]:
        dataframe = self.get_table_dataframe(dataset_id, table.table_name)
        return {
            "table_name": table.table_name,
            "sql_name": quote_sql_identifier(table.table_name),
            "filename": table.filename,
            "sheet_name": table.sheet_name,
            "row_count": int(len(dataframe)),
            "column_count": int(len(dataframe.columns)),
            "columns": self._column_summaries(dataframe),
            "sample_rows": dataframe.head(5).where(pd.notna(dataframe), None).to_dict("records"),
        }

    def _column_summaries(self, dataframe: pd.DataFrame) -> list[dict[str, Any]]:
        return [
            {
                "name": str(column),
                "dtype": str(dataframe[column].dtype),
                "missing_count": int(dataframe[column].isna().sum()),
            }
            for column in dataframe.columns
        ]

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _is_inside_dataset_dir(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.dataset_dir.resolve())
        except ValueError:
            return False
        return True


dataset_service = DatasetService()
