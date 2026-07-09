import json

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from app.services.analysis_service import AnalysisService
from app.services.chart_service import ChartService
from app.services.preprocessing_service import PreprocessingService
from app.services.python_sandbox_service import PythonSandboxService
from app.services.sql_service import SQLService
from app.schemas.preprocessing import (
    ApplyCleaningArgs,
    CleaningOperation,
    ResetCleaningArgs,
    SuggestCleaningArgs,
)


class QueryDataArgs(BaseModel):
    dataset_id: str = Field(..., description="数据集 ID")
    sql: str = Field(
        ...,
        description=(
            "只允许 SQLite SELECT / WITH 查询。必须使用当前 schema 中列出的 SQL表名和字段 SQL引用；"
            "多表数据集不能使用 data_table。"
        ),
    )
    max_rows: int = Field(100, description="最多返回多少行，最大不超过 1000")


class GenerateChartArgs(BaseModel):
    dataset_id: str = Field(..., description="数据集 ID")
    sql: str = Field(..., description="用于生成图表数据的 SQLite SELECT / WITH SQL；多表时使用 schema 中的 SQL表名")
    chart_type: str = Field(..., description="图表类型：bar、line、pie、scatter")
    x: str = Field(..., description="SQL 查询结果中作为 x 轴、分类标签或饼图标签的字段名")
    y: str = Field(..., description="SQL 查询结果中作为 y 轴、数值或饼图数值的字段名")
    title: str = Field(..., description="图表标题")


class PythonAnalysisArgs(BaseModel):
    dataset_id: str = Field(..., description="数据集 ID")
    sql: str = Field(
        ...,
        description=(
            "用于导出分析数据的 SQLite SELECT / WITH SQL。必须使用当前 schema 中列出的表名和字段；"
            "不要写 INSERT、UPDATE、DELETE、CREATE、PRAGMA 等非只读语句。"
        ),
    )
    analysis_goal: str = Field(..., description="本次 Python 分析要回答的具体问题")
    python_code: str = Field(
        ...,
        description=(
            "要在 Docker 沙箱中执行的 Python 代码。代码必须读取 /workspace/input/data.json，"
            "并将 JSON 对象写入 /workspace/output/result.json。"
        ),
    )
    max_rows: int = Field(50000, description="最多导出多少行 SQL 结果用于 Python 分析")


class DatasetOnlyArgs(BaseModel):
    dataset_id: str = Field(..., description="数据集 ID")


class CorrelationAnalysisArgs(BaseModel):
    dataset_id: str = Field(..., description="数据集 ID")
    threshold: float = Field(0.6, description="强相关字段对阈值，默认 0.6")


def make_query_data_tool(sql_service: SQLService) -> StructuredTool:
    def _run(dataset_id: str, sql: str, max_rows: int = 100) -> str:
        try:
            result = sql_service.query(dataset_id=dataset_id, sql=sql, max_rows=max_rows)
        except Exception as exc:
            return (
                "SQL 查询执行失败："
                f"{exc}\n"
                "请检查 SQLite 方言、schema 中的具体 SQL表名和字段 SQL引用写法，并修正后重试。"
            )

        if result.empty:
            return "查询成功，但结果为空。"

        return result.to_markdown(index=False)

    return StructuredTool.from_function(
        name="query_data",
        description=(
            "对上传数据集的 SQLite 数据库执行安全只读查询。数据集可包含多张表；"
            "请使用 schema 中给出的 SQL表名和字段 SQL引用。多表数据集不能使用 data_table。"
        ),
        func=_run,
        args_schema=QueryDataArgs,
    )


def make_profile_data_tool(analysis_service: AnalysisService) -> StructuredTool:
    def _run(dataset_id: str) -> str:
        return analysis_service.profile_data(dataset_id)

    return StructuredTool.from_function(
        name="profile_data",
        description=(
            "生成数据集整体质量概览，包括字段类型、缺失值、描述性统计、相关性和异常值摘要。"
            "当用户要求数据质量分析、数据概览、字段情况或整体分析时优先调用。"
        ),
        func=_run,
        args_schema=DatasetOnlyArgs,
    )


def make_missing_value_analysis_tool(analysis_service: AnalysisService) -> StructuredTool:
    def _run(dataset_id: str) -> str:
        return analysis_service.missing_value_analysis(dataset_id)

    return StructuredTool.from_function(
        name="missing_value_analysis",
        description="分析数据集中每个字段的缺失值数量和缺失率。",
        func=_run,
        args_schema=DatasetOnlyArgs,
    )


def make_descriptive_statistics_tool(analysis_service: AnalysisService) -> StructuredTool:
    def _run(dataset_id: str) -> str:
        return analysis_service.descriptive_statistics(dataset_id)

    return StructuredTool.from_function(
        name="descriptive_statistics",
        description="生成数值字段和非数值字段的描述性统计。",
        func=_run,
        args_schema=DatasetOnlyArgs,
    )


def make_correlation_analysis_tool(analysis_service: AnalysisService) -> StructuredTool:
    def _run(dataset_id: str, threshold: float = 0.6) -> str:
        return analysis_service.correlation_analysis(dataset_id, threshold=threshold)

    return StructuredTool.from_function(
        name="correlation_analysis",
        description="计算数值字段之间的相关系数矩阵，并列出强相关字段对。",
        func=_run,
        args_schema=CorrelationAnalysisArgs,
    )


def make_outlier_detection_tool(analysis_service: AnalysisService) -> StructuredTool:
    def _run(dataset_id: str) -> str:
        return analysis_service.outlier_detection(dataset_id)

    return StructuredTool.from_function(
        name="outlier_detection",
        description="使用 IQR 方法检测数值字段中的异常值数量和比例。",
        func=_run,
        args_schema=DatasetOnlyArgs,
    )


def make_suggest_cleaning_tool(
    preprocessing_service: PreprocessingService,
) -> StructuredTool:
    def _run(dataset_id: str, table_name: str | None = None) -> str:
        try:
            result = preprocessing_service.suggest_cleaning(dataset_id, table_name)
        except Exception as exc:
            return f"生成清洗建议失败：{exc}"
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        name="suggest_cleaning",
        description=(
            "只检查 processed 数据并生成清洗建议，不修改任何数据。"
            "用户要求检查数据质量、提出清洗方案或尚未明确确认修改时优先调用。"
        ),
        func=_run,
        args_schema=SuggestCleaningArgs,
    )


def make_apply_cleaning_tool(
    preprocessing_service: PreprocessingService,
) -> StructuredTool:
    def _run(
        dataset_id: str,
        table_name: str,
        operations: list[CleaningOperation | dict],
    ) -> str:
        try:
            validated_operations = [
                operation
                if isinstance(operation, CleaningOperation)
                else CleaningOperation.model_validate(operation)
                for operation in operations
            ]
            result = preprocessing_service.apply_cleaning(
                dataset_id,
                table_name,
                validated_operations,
            )
        except Exception as exc:
            return f"执行数据清洗失败：{exc}"
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        name="apply_cleaning",
        description=(
            "按顺序执行经过用户明确确认的安全清洗操作，只修改 processed 数据，"
            "随后更新 manifest 并重建 SQLite；绝不修改 raw 原件。"
        ),
        func=_run,
        args_schema=ApplyCleaningArgs,
    )


def make_reset_cleaning_tool(
    preprocessing_service: PreprocessingService,
) -> StructuredTool:
    def _run(dataset_id: str, table_name: str | None = None) -> str:
        try:
            result = preprocessing_service.reset_cleaning(dataset_id, table_name)
        except Exception as exc:
            return f"恢复原始数据失败：{exc}"
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        name="reset_cleaning",
        description=(
            "将指定表或整个数据集的 processed 数据恢复为 raw 原始状态，"
            "清空对应清洗历史并重建 SQLite。"
        ),
        func=_run,
        args_schema=ResetCleaningArgs,
    )


def make_generate_chart_tool(sql_service: SQLService, chart_service: ChartService) -> StructuredTool:
    def _run(
        dataset_id: str,
        sql: str,
        chart_type: str,
        x: str,
        y: str,
        title: str,
    ) -> str:
        try:
            result = sql_service.query(dataset_id=dataset_id, sql=sql, max_rows=1000)
            chart = chart_service.generate_chart(
                dataframe=result,
                chart_type=chart_type,
                x=x,
                y=y,
                title=title,
            )
        except Exception as exc:
            return (
                "图表生成失败："
                f"{exc}\n"
                "请检查 SQL、图表类型，以及 x/y 字段是否存在并适合绘图。"
            )

        return json.dumps(
            {
                "chart_id": chart.chart_id,
                "chart_type": chart.chart_type,
                "title": chart.title,
                "chart_path": str(chart.path),
                "chart_url": chart.url,
                "message": f"图表已生成：{chart.title}",
            },
            ensure_ascii=False,
        )

    return StructuredTool.from_function(
        name="generate_chart",
        description=(
            "根据 SQL 查询结果生成图表。支持 bar、line、pie、scatter。"
            "SQL 查询结果中必须包含 x 和 y 参数指定的字段。"
        ),
        func=_run,
        args_schema=GenerateChartArgs,
    )


def make_python_analysis_tool(
    sql_service: SQLService,
    python_sandbox_service: PythonSandboxService,
) -> StructuredTool:
    def _run(
        dataset_id: str,
        sql: str,
        analysis_goal: str,
        python_code: str,
        max_rows: int = 50000,
    ) -> str:
        try:
            dataframe = sql_service.query_for_analysis(
                dataset_id=dataset_id,
                sql=sql,
                max_rows=max_rows,
            )
            if dataframe.empty:
                return "Python 沙箱分析未执行：SQL 查询成功，但结果为空。"
            sandbox_result = python_sandbox_service.run_analysis(
                dataframe=dataframe,
                python_code=python_code,
                analysis_goal=analysis_goal,
            )
        except Exception as exc:
            return (
                "Python 沙箱分析失败："
                f"{exc}\n"
                "请检查 SQL 是否为 SQLite 只读 SELECT/WITH，Python 代码是否读取 "
                "/workspace/input/data.json，并写出 /workspace/output/result.json。"
            )

        return json.dumps(
            {
                "run_id": sandbox_result.run_id,
                "analysis_goal": sandbox_result.analysis_goal,
                "input_rows": sandbox_result.input_rows,
                "result": sandbox_result.result,
                "figures": [
                    {
                        "chart_id": figure.chart_id,
                        "chart_url": figure.url,
                        "title": figure.title,
                        "source_name": figure.source_name,
                    }
                    for figure in sandbox_result.figures
                ],
                "stdout": sandbox_result.stdout,
                "stderr": sandbox_result.stderr,
                "message": "Python 沙箱分析已完成。",
            },
            ensure_ascii=False,
        )

    return StructuredTool.from_function(
        name="python_analysis",
        description=(
            "复杂分析 fallback：先执行只读 SQL 导出 JSON，再在 Docker 沙箱中运行生成的 Python 脚本。"
            "适合相关性热力图、聚类、复杂异常值检测、时间序列趋势、多步骤统计和普通 SQL 难以完成的分析。"
            "Python 代码必须读取 /workspace/input/data.json，并写入 /workspace/output/result.json。"
        ),
        func=_run,
        args_schema=PythonAnalysisArgs,
    )


def build_tools(
    sql_service: SQLService,
    chart_service: ChartService,
    analysis_service: AnalysisService,
) -> list[StructuredTool]:
    preprocessing_service = PreprocessingService(analysis_service.dataset_service)
    python_sandbox_service = PythonSandboxService()
    return [
        make_query_data_tool(sql_service),
        make_profile_data_tool(analysis_service),
        make_missing_value_analysis_tool(analysis_service),
        make_descriptive_statistics_tool(analysis_service),
        make_correlation_analysis_tool(analysis_service),
        make_outlier_detection_tool(analysis_service),
        make_suggest_cleaning_tool(preprocessing_service),
        make_apply_cleaning_tool(preprocessing_service),
        make_reset_cleaning_tool(preprocessing_service),
        make_generate_chart_tool(sql_service, chart_service),
        make_python_analysis_tool(sql_service, python_sandbox_service),
    ]
