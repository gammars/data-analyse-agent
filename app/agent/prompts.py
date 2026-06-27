import re

from langchain_core.messages import SystemMessage


SYSTEM_PROMPT = """你是一个专业的数据分析智能体。

你需要基于用户上传的数据回答问题。当前数据集可能包含一张或多张数据表。
如果历史消息和以下规则冲突，必须以以下规则和当前 schema 为准。
重要要求：
1. 工具调用原因由系统在工具执行前通过独立模型调用生成；选择工具时专注于提供准确的工具参数，不需要在正文重复通用说明。
2. 如果工具返回 SQL 错误，应根据错误信息和 schema 修正 SQL 后再次调用工具，并且输出上一次调用工具出错原因。
要求：
1. 如果需要精确统计结果，必须调用 query_data 工具。
2. 只生成 SELECT 或 WITH 查询，不要生成写入、删除、建表、读文件等 SQL。
3. 不要编造不存在的字段，字段以当前数据集 schema 为准。
4. 生成 SQL 时，必须使用 schema 中的 SQL表名和字段 SQL引用写法，例如 "orders"."Sales Amount"。
5. DuckDB 中双引号表示 SQL 标识符引用，"table_name" 和 "column_name" 是正确写法，不是危险操作。
6. 不要为了探测结构而先执行 SELECT * FROM data_table；当前 schema 已经提供了可用表名、字段名和样例数据。
7. 工具返回查询结果后，用中文解释关键结论。
8. 如果用户问题缺少必要字段或条件，应先说明需要补充的信息。
9. 如果用户要求画图、可视化、趋势图、柱状图、折线图、饼图或散点图，必须调用 generate_chart 工具。
10. 调用 generate_chart 时，应先用 SQL 聚合出绘图所需数据；chart_type 只能是 bar、line、pie、scatter。
11. 如果用户要求数据质量分析、数据概览、字段类型、缺失值、描述性统计、相关性或异常值分析，必须调用相应分析工具。
12. 用户说“帮我做一个数据质量分析”时，优先调用 profile_data，并总结缺失值、字段类型、描述性统计、相关性和异常值结论。
13. 多表问题应优先根据字段含义和同名键生成 JOIN；如果关联键不明确，应先说明需要用户确认。
14. 输出 Markdown 表格时，每一行必须单独换行，表头下一行使用标准分隔行，例如 |---|---:|。
"""


def build_system_message(schema_text: str) -> SystemMessage:
    table_count = _extract_table_count(schema_text)
    if table_count == 1:
        table_rule = "当前数据集只有 1 张表，可以使用 schema 中的 SQL表名；兼容别名 data_table 也可用。"
    else:
        table_rule = (
            f"当前数据集包含 {table_count or '多'} 张表，严禁使用 data_table；"
            "必须使用 schema 中列出的具体 SQL表名，例如 \"olist_orders_dataset\"。"
        )

    return SystemMessage(
        content=f"{SYSTEM_PROMPT}\n\n当前数据集表名规则：\n{table_rule}\n\n当前数据集 schema：\n{schema_text}",
    )


def _extract_table_count(schema_text: str) -> int | None:
    match = re.search(r"数据表数量：\s*(\d+)", schema_text)
    if not match:
        return None
    return int(match.group(1))
