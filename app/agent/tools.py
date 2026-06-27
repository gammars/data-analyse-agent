import json

from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from app.services.analysis_service import AnalysisService
from app.services.chart_service import ChartService
from app.services.sql_service import SQLService


class QueryDataArgs(BaseModel):
    dataset_id: str = Field(..., description="数据集 ID")
    sql: str = Field(
        ...,
        description=(
            "只允许 SELECT / WITH 查询。必须使用当前 schema 中列出的 SQL表名和字段 SQL引用；"
            "多表数据集不能使用 data_table。"
        ),
    )
    max_rows: int = Field(100, description="最多返回多少行，最大不超过 1000")


class GenerateChartArgs(BaseModel):
    dataset_id: str = Field(..., description="数据集 ID")
    sql: str = Field(..., description="用于生成图表数据的 SELECT / WITH SQL；多表时使用 schema 中的 SQL表名")
    chart_type: str = Field(..., description="图表类型：bar、line、pie、scatter")
    x: str = Field(..., description="SQL 查询结果中作为 x 轴、分类标签或饼图标签的字段名")
    y: str = Field(..., description="SQL 查询结果中作为 y 轴、数值或饼图数值的字段名")
    title: str = Field(..., description="图表标题")


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
                "请检查是否使用了 schema 中的具体 SQL表名和字段 SQL引用写法，并修正 SQL 后重试。"
            )

        if result.empty:
            return "查询成功，但结果为空。"

        return result.to_markdown(index=False)

    return StructuredTool.from_function(
        name="query_data",
        description=(
            "对上传数据集执行安全 SQL 查询。数据集可包含多张表；"
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


def build_tools(
    sql_service: SQLService,
    chart_service: ChartService,
    analysis_service: AnalysisService,
) -> list[StructuredTool]:
    return [
        make_query_data_tool(sql_service),
        make_profile_data_tool(analysis_service),
        make_missing_value_analysis_tool(analysis_service),
        make_descriptive_statistics_tool(analysis_service),
        make_correlation_analysis_tool(analysis_service),
        make_outlier_detection_tool(analysis_service),
        make_generate_chart_tool(sql_service, chart_service),
    ]
