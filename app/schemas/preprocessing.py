from typing import Literal

from pydantic import BaseModel, Field


CleaningOperationName = Literal[
    "drop_duplicate_rows",
    "drop_empty_rows",
    "drop_empty_columns",
    "trim_strings",
    "convert_type",
    "handle_missing",
    "sample_rows",
]

TargetType = Literal["string", "integer", "float", "boolean", "datetime"]
MissingStrategy = Literal[
    "keep",
    "drop_rows",
    "fill_constant",
    "fill_mean",
    "fill_median",
    "fill_mode",
]


class CleaningOperation(BaseModel):
    operation: CleaningOperationName = Field(..., description="安全清洗操作名称")
    column: str | None = Field(None, description="操作目标字段；部分操作可省略")
    target_type: TargetType | None = Field(None, description="convert_type 的目标类型")
    strategy: MissingStrategy | None = Field(None, description="handle_missing 的处理策略")
    value: str | int | float | bool | None = Field(None, description="常量填充值")
    errors: Literal["raise", "coerce"] = Field(
        "raise",
        description="类型转换失败时抛错或转为空值",
    )
    sample_size: int | None = Field(None, ge=1, description="sample_rows 保留的行数")
    random_state: int = Field(42, description="抽样随机种子")


class SuggestCleaningArgs(BaseModel):
    dataset_id: str = Field(..., description="数据集 ID")
    table_name: str | None = Field(None, description="指定表名；不填则检查全部表")


class ApplyCleaningArgs(BaseModel):
    dataset_id: str = Field(..., description="数据集 ID")
    table_name: str = Field(..., description="要清洗的具体表名")
    operations: list[CleaningOperation] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="按顺序执行的安全清洗操作",
    )


class ResetCleaningArgs(BaseModel):
    dataset_id: str = Field(..., description="数据集 ID")
    table_name: str | None = Field(
        None,
        description="指定表名；不填则将数据集全部表恢复为 raw 原始状态",
    )
