from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "output" / "pdf"


@dataclass
class Question:
    prompt: str
    score: int
    answer: str
    analysis: str
    source: str
    rubric: list[str] | None = None


@dataclass
class Paper:
    title: str
    subtitle: str
    filename: str
    focus: str
    fill: list[Question]
    short: list[Question]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _register_fonts()
    styles = _build_styles()
    papers = _build_papers()
    _validate_papers(papers)
    for paper in papers:
        _build_pdf(paper, styles)
    print("Generated PDFs:")
    for paper in papers:
        print(OUTPUT_DIR / paper.filename)


def _register_fonts() -> None:
    candidates = [
        Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\Noto Sans SC (TrueType).otf"),
    ]
    bold_candidates = [
        Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\Noto Sans SC Bold (TrueType).otf"),
        Path(r"C:\Windows\Fonts\Noto Sans SC Medium (TrueType).otf"),
    ]
    regular = next(path for path in candidates if path.exists())
    bold = next(path for path in bold_candidates if path.exists())
    pdfmetrics.registerFont(TTFont("CJK", str(regular)))
    pdfmetrics.registerFont(TTFont("CJK-Bold", str(bold)))
    pdfmetrics.registerFont(TTFont("CJK-Code", str(regular)))


def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    styles: dict[str, ParagraphStyle] = {}
    styles["title"] = ParagraphStyle(
        "ProjectTitle",
        parent=base["Title"],
        fontName="CJK-Bold",
        fontSize=24,
        leading=32,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#111827"),
        spaceAfter=12,
    )
    styles["subtitle"] = ParagraphStyle(
        "ProjectSubtitle",
        parent=base["Normal"],
        fontName="CJK",
        fontSize=13,
        leading=20,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#475569"),
        spaceAfter=10,
    )
    styles["h1"] = ParagraphStyle(
        "H1",
        parent=base["Heading1"],
        fontName="CJK-Bold",
        fontSize=18,
        leading=24,
        textColor=colors.HexColor("#1f2937"),
        spaceBefore=10,
        spaceAfter=8,
    )
    styles["h2"] = ParagraphStyle(
        "H2",
        parent=base["Heading2"],
        fontName="CJK-Bold",
        fontSize=14,
        leading=20,
        textColor=colors.HexColor("#334155"),
        spaceBefore=8,
        spaceAfter=6,
    )
    styles["body"] = ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontName="CJK",
        fontSize=10.5,
        leading=17,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#111827"),
        spaceAfter=5,
    )
    styles["small"] = ParagraphStyle(
        "Small",
        parent=styles["body"],
        fontSize=9,
        leading=14,
        textColor=colors.HexColor("#475569"),
    )
    styles["question"] = ParagraphStyle(
        "Question",
        parent=styles["body"],
        fontName="CJK-Bold",
        fontSize=11,
        leading=17,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=6,
        spaceAfter=4,
    )
    styles["label"] = ParagraphStyle(
        "Label",
        parent=styles["body"],
        fontName="CJK-Bold",
        fontSize=10,
        leading=15,
        textColor=colors.HexColor("#1d4ed8"),
        spaceBefore=3,
        spaceAfter=2,
    )
    styles["code"] = ParagraphStyle(
        "Code",
        parent=base["Code"],
        fontName="CJK-Code",
        fontSize=8.5,
        leading=12,
        backColor=colors.HexColor("#f8fafc"),
        borderColor=colors.HexColor("#e2e8f0"),
        borderWidth=0.5,
        borderPadding=4,
        leftIndent=0,
        wordWrap="CJK",
    )
    return styles


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_format_inline(text), style)


def _format_inline(text: str) -> str:
    parts = text.split("`")
    output = []
    for index, part in enumerate(parts):
        escaped = html.escape(part).replace("\n", "<br/>")
        if index % 2 == 1:
            output.append(f'<font name="CJK-Code" color="#0f172a">{escaped}</font>')
        else:
            output.append(escaped)
    return "".join(output)


def _build_pdf(paper: Paper, styles: dict[str, ParagraphStyle]) -> None:
    path = OUTPUT_DIR / paper.filename
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=paper.title,
        author="Data Analyse Agent Project",
    )

    story: list[Flowable] = []
    story.extend(_cover_story(paper, styles))
    story.append(PageBreak())
    story.extend(_contents_story(paper, styles))
    story.append(PageBreak())
    story.extend(_section_story("第一部分：填空题（共 40 分）", paper.fill, styles, start_index=1))
    story.append(PageBreak())
    story.extend(_section_story("第二部分：简答题与分析题（共 60 分）", paper.short, styles, start_index=1))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


def _footer(canvas, doc) -> None:  # type: ignore[no-untyped-def]
    canvas.saveState()
    canvas.setFont("CJK", 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawCentredString(A4[0] / 2, 9 * mm, f"第 {doc.page} 页")
    canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, "Data Analyse Agent 综合测试卷")
    canvas.restoreState()


def _cover_story(paper: Paper, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    story: list[Flowable] = [Spacer(1, 48 * mm)]
    story.append(_p("Data Analyse Agent", styles["title"]))
    story.append(_p(paper.title, styles["title"]))
    story.append(_p(paper.subtitle, styles["subtitle"]))
    story.append(Spacer(1, 10 * mm))
    meta = [
        ["项目名称", "Data Analyse Agent"],
        ["总分", "100 分"],
        ["题型", "填空题 40 分；简答题与分析题 60 分"],
        ["考查重点", paper.focus],
        ["版本说明", "依据当前项目代码、README、evals、tests 与实现说明生成"],
    ]
    table = Table(meta, colWidths=[34 * mm, 115 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "CJK"),
                ("FONTNAME", (0, 0), (0, -1), "CJK-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("LEADING", (0, 0), (-1, -1), 15),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eff6ff")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (1, 0), (1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 8 * mm))
    story.append(_p("使用说明：本 PDF 为“题目 + 标准答案 + 详细解析”的完整复习版。每题均标注项目知识点或代码位置，便于回到源码中复盘。", styles["body"]))
    return story


def _contents_story(paper: Paper, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    story = [_p("目录与分值校验", styles["h1"])]
    story.append(_p("一、填空题：20 道，每题 2 分，共 40 分。", styles["body"]))
    story.append(_p("二、简答题与分析题：10 道，每题 6 分，共 60 分。", styles["body"]))
    story.append(_p("总分校验：40 + 60 = 100 分。", styles["body"]))
    story.append(Spacer(1, 4 * mm))
    rows = [["部分", "题量", "单题分值", "小计"]]
    rows.append(["填空题", str(len(paper.fill)), "2 分", f"{sum(q.score for q in paper.fill)} 分"])
    rows.append(["简答题与分析题", str(len(paper.short)), "6 分", f"{sum(q.score for q in paper.short)} 分"])
    rows.append(["合计", str(len(paper.fill) + len(paper.short)), "-", f"{sum(q.score for q in paper.fill + paper.short)} 分"])
    table = Table(rows, colWidths=[45 * mm, 30 * mm, 35 * mm, 35 * mm])
    table.setStyle(_basic_table_style())
    story.append(table)
    story.append(Spacer(1, 6 * mm))
    story.append(_p("覆盖范围：项目背景、目录结构、数据集持久化、Manifest、SQLite、关系配置、API、ScopeRouter、Planner、工具策略、Python 沙箱、Artifact Store、上下文压缩、前端 SSE 展示、自动化评测与优化方向。", styles["body"]))
    return story


def _section_story(title: str, questions: list[Question], styles: dict[str, ParagraphStyle], start_index: int) -> list[Flowable]:
    story: list[Flowable] = [_p(title, styles["h1"])]
    for offset, question in enumerate(questions, start=start_index):
        story.append(_p(f"{offset}. 题目（{question.score} 分）：{question.prompt}", styles["question"]))
        story.append(_p("标准答案", styles["label"]))
        story.append(_p(question.answer, styles["body"]))
        if question.rubric:
            story.append(_p("评分标准", styles["label"]))
            items = [
                ListItem(_p(item, styles["small"]), leftIndent=8)
                for item in question.rubric
            ]
            story.append(ListFlowable(items, bulletType="bullet", start="circle", leftIndent=14))
        story.append(_p("详细解析", styles["label"]))
        story.append(_p(question.analysis, styles["body"]))
        story.append(_p(f"对应项目知识点或代码位置：{question.source}", styles["small"]))
        story.append(Spacer(1, 3 * mm))
    return story


def _basic_table_style() -> TableStyle:
    return TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), "CJK"),
            ("FONTNAME", (0, 0), (-1, 0), "CJK-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
            ("LEADING", (0, 0), (-1, -1), 14),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e0f2fe")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
    )


def _q(prompt: str, answer: str, analysis: str, source: str, score: int = 2) -> Question:
    return Question(prompt=prompt, score=score, answer=answer, analysis=analysis, source=source)


def _s(prompt: str, answer: str, analysis: str, source: str, rubric: list[str]) -> Question:
    return Question(prompt=prompt, score=6, answer=answer, analysis=analysis, source=source, rubric=rubric)


def _build_papers() -> list[Paper]:
    return [_paper_one(), _paper_two(), _paper_three()]


def _paper_one() -> Paper:
    fill = [
        _q("本项目的后端 Web 框架是 ______，浏览器前端由后端直接托管，不需要单独运行 Node 开发服务器。", "FastAPI", "README 的技术栈和启动说明明确写出后端使用 FastAPI、Uvicorn，前端是原生 HTML/CSS/JavaScript 并由 FastAPI 直接提供。理解这一点能区分本项目和前后端分离构建项目。", "README.md：技术栈、启动项目。"),
        _q("每个上传数据集都会生成一个独立的 SQLite 数据库文件，文件名是 ______。", "dataset.sqlite3", "DatasetService 中 `SQLITE_FILENAME = \"dataset.sqlite3\"`，README 本地存储结构也列出每个 dataset_id 目录下保存该文件。", "app/services/dataset_service.py；README.md：本地存储。"),
        _q("上传的原始文件保存在 ______ 目录，系统设计要求它们永不被修改。", "raw/", "项目采用 raw 与 processed 分层：raw 保存原始 CSV/Excel，processed 保存当前可被清洗和重建 SQLite 的数据。清洗只影响 processed。", "README.md：当前功能、本地存储；app/services/preprocessing_service.py。"),
        _q("每张逻辑表当前版本的数据保存在 ______ 目录，并会被清洗工具更新。", "processed/", "PreprocessingService 调用 DatasetService.replace_processed_tables 更新 processed 数据，并重建 SQLite。raw_unchanged=True 是返回结果中的关键保证。", "README.md：本地存储；app/services/preprocessing_service.py。"),
        _q("记录字段统计、清洗历史、主外键和索引配置的标准文件是 ______。", "manifest.json", "README 明确说明每个数据集包含 manifest.json。PreprocessingService 在清洗后读取并写回 Manifest，记录 cleaning_steps 和 processing_status。", "README.md：当前功能、本地存储；app/schemas/manifest.py。"),
        _q("单表数据集通过 SQLite 视图兼容的默认别名是 ______。", "data_table", "README 的使用界面部分说明单表可使用 data_table，多表必须使用 schema 中具体表名。DatasetService 中也有 `DEFAULT_SINGLE_TABLE_ALIAS = \"data_table\"`。", "README.md：使用界面；app/services/dataset_service.py。"),
        _q("上传或追加数据后，项目会自动分析主键、外键和索引候选，并由 ______ 给出受候选集合约束的推荐。", "LLM", "README 当前功能说明关系配置由统计候选和 LLM 推荐组成；但 LLM 只能在候选集合内推荐，业务语义仍需用户确认。", "README.md：当前功能、当前限制；app/services/relationship_service.py。"),
        _q("用户必须确认一次关系配置后才能使用 Agent，这一检查发生在聊天请求的 `_resolve_conversation` 中，会调用 ______。", "dataset_service.require_relationship_configuration(dataset_id)", "`app/api/chat.py` 的 `_resolve_conversation` 在已有 conversation 和新 dataset 两种路径都会调用该方法，保证未确认关系的数据集不能直接进入 Agent。", "app/api/chat.py：_resolve_conversation。"),
        _q("本项目用于执行只读 SQL 查询的服务类是 ______。", "SQLService", "SQLService 封装 SQLite 只读 URI、query_only、authorizer、超时和行数上限，是 query_data、generate_chart、python_analysis 的基础数据入口。", "app/services/sql_service.py；app/agent/tools.py。"),
        _q("SQLService 只允许以 ______ 或 ______ 开头的单条查询。", "SELECT；WITH", "`_validate_sql` 检查 normalized SQL 必须 startswith select 或 with，并禁止一条语句中包含额外分号。", "app/services/sql_service.py：_validate_sql。"),
        _q("图表生成后的 PNG 文件默认保存在 `app/storage/______` 目录，并通过 `/charts/{chart_id}.png` 访问。", "charts", "ChartService 生成 PNG 到 app/storage/charts，README 的 API 部分说明图表通过 /charts/{chart_id}.png 访问。", "app/services/chart_service.py；README.md：API。"),
        _q("负责创建、读取、保存对话 JSON，并支持恢复历史对话的服务是 ______。", "ConversationService", "chat.py 通过 conversation_service append_message/append_messages 保存用户消息、工具事件、plan、scope、artifact 和 assistant 文本。", "app/services/conversation_service.py；app/api/chat.py。"),
        _q("上下文窗口默认限制常量是 ______ tokens，达到阈值后会压缩早期对话。", "24000", "ContextService 中 `DEFAULT_CONTEXT_LIMIT_TOKENS = 24000`，压缩阈值默认是 0.8。前端侧栏展示 token 估算。", "app/services/context_service.py；frontend/index.html。"),
        _q("项目的自动化评测流水线入口脚本位于 ______。", "evals/runners/run_evals.py", "最近新增的 evals 目录包含 cases、runner 和 reports。runner 会生成临时电商 fixture 数据集并输出 latest.json。", "evals/README.md；evals/runners/run_evals.py。"),
        _q("项目用于启动 Web 服务的入口脚本是 ______。", "run.py", "README 启动项目部分给出的命令是 python run.py。该脚本启动 Uvicorn，并默认开启自动重载。", "README.md：启动项目；run.py。"),
        _q("项目的 Python 包配置文件是 ______，其中也配置了 pytest 的测试路径。", "pyproject.toml", "pyproject.toml 是现代 Python 项目配置入口，README 和测试输出都显示 rootdir/configfile 指向该文件。", "pyproject.toml；pytest 输出。"),
        _q("上传文件大小限制可通过环境变量 ______ 配置，留空或 0 表示不设置应用层上传大小限制。", "DATA_ANALYSE_MAX_UPLOAD_MB", "README 的 .env 示例中说明该变量；DatasetService 的 get_upload_size_limit_bytes 会读取并解析它。", "README.md：配置模型；app/services/dataset_service.py。"),
        _q("用于把旧数据集补建 SQLite 文件并检查表行数的脚本是 ______。", "scripts/materialize_sqlite.py", "README 测试和诊断部分说明 materialize_sqlite.py 可为旧数据集生成缺失 SQLite，并支持 --rebuild。", "README.md：测试和诊断；scripts/materialize_sqlite.py。"),
        _q("本项目自动测试命令是 ______，当前测试覆盖 SQL、Planner、ScopeRouter、Python 沙箱、Artifact Store 等模块。", "python -m pytest", "README 测试部分给出该命令，测试目录中包含 test_sql_service.py、test_planner_policy.py、test_artifact_store.py 等。", "README.md：测试和诊断；tests/。"),
        _q("当前项目没有用户认证和权限隔离，因此定位是 ______ 环境。", "本地单用户开发与实验", "README 当前限制明确说明项目没有用户认证和权限隔离，定位为本地单用户开发与实验环境。该点关系到安全边界判断。", "README.md：当前限制。"),
    ]
    short = [
        _s("说明本项目为什么适合作为“数据库系统原理”课程设计，而不仅是一个普通聊天应用。", "参考答案：项目围绕结构化数据管理展开，包含 CSV/Excel 上传、表名规范化、Manifest 元数据、SQLite 建库、主外键和索引配置、只读 SQL 查询、多表 JOIN、清洗后重建数据库、关系完整性验证和查询安全控制。Agent 只是交互层，核心数据仍由 SQLite、schema、manifest 和服务层管理。", "关键在于把“自然语言分析”落到数据库对象与操作上：数据进入 raw/processed 分层，经过 DatasetService 写入 SQLite，再由 SQLService 和工具层查询。关系配置和索引让它不只是文件分析脚本。容易误解的是把项目看成 LLM demo；实际上 README 与服务层代码表明数据库建模、持久化、安全查询是主干。", "README.md；app/services/dataset_service.py；app/services/sql_service.py；app/services/relationship_service.py。", ["指出结构化数据上传和 SQLite 建库 1.5 分", "指出 Manifest、关系配置、主外键/索引 1.5 分", "指出 SQLService 只读查询和 JOIN 能力 1 分", "指出 Agent 是交互层而数据库服务是核心 1 分", "结合课程目标说明价值 1 分"]),
        _s("梳理从上传多个 CSV/Excel 到可以被 Agent 查询的主要数据流。", "参考答案：上传请求进入 upload API，DatasetService 校验后创建 dataset_id 目录，保存 raw 文件并为每个逻辑表生成 processed CSV；随后重建 dataset.sqlite3，生成 schema 和 manifest，分析关系候选。用户确认关系后，SQLiteDDL/Storage 相关服务保存主外键和索引，Agent 读取 schema 并通过 SQLService 查询。", "该流程体现数据从文件到数据库再到 Agent 的转换。Excel 多 Sheet 会变多张表，sanitize_table_name/unique_table_name 处理表名。Agent 不能在关系配置未确认时运行，因为 chat.py 会检查 require_relationship_configuration。", "app/api/upload.py；app/services/dataset_service.py；app/services/sqlite_storage_service.py；app/api/chat.py。", ["上传 API 与 DatasetService 入口 1 分", "raw/processed 分层 1 分", "SQLite 重建与 schema/manifest 生成 1.5 分", "关系配置确认 1 分", "Agent 通过 SQLService 使用数据库 1.5 分"]),
        _s("分析 raw、processed、manifest、dataset.sqlite3 四者各自承担什么职责，以及为什么不能混为一谈。", "参考答案：raw 是不可变原始文件；processed 是当前可分析、可清洗的数据版本；manifest 记录字段、关系、索引和清洗历史等元数据；dataset.sqlite3 是供 SQL 查询和 Agent 使用的数据库物化结果。四者分离可支持回滚、重建、审计和课程中的数据一致性说明。", "如果直接修改 raw，会丢失原始依据；如果只有 SQLite，没有 processed 和 manifest，就难以追踪清洗历史与字段统计；如果只有 CSV，则 JOIN、索引和 SQL 安全查询能力不足。PreprocessingService 返回 raw_unchanged=True 正是设计意图。", "README.md：本地存储；app/services/preprocessing_service.py；app/schemas/manifest.py。", ["raw 不可变 1 分", "processed 是当前版本 1 分", "manifest 是元数据和历史 1.5 分", "SQLite 是查询物化层 1 分", "说明分离带来的回滚/审计/重建价值 1.5 分"]),
        _s("解释为什么关系配置必须在使用 Agent 前完成。", "参考答案：关系配置让系统明确主键、外键和索引，提升 schema 可靠性，使 Agent 在多表 JOIN、字段引用和解释数据结构时有依据。chat.py 的 _resolve_conversation 会调用 dataset_service.require_relationship_configuration，未确认时拒绝进入 Agent。这样避免模型在关系不明确时胡乱 JOIN 或错误解释表关系。", "关系配置并不等于 LLM 自动应用业务语义；README 限制说明 LLM 推荐仍需用户确认。该设计在工程上牺牲一些便利性，换来可解释、可控和课程答辩中更强的数据建模依据。", "app/api/chat.py：_resolve_conversation；README.md：当前功能、当前限制。", ["说明关系配置含主外键/索引 1.5 分", "说明 chat.py 的强制检查 1.5 分", "说明减少错误 JOIN 和误解释 1 分", "说明用户确认和 LLM 推荐边界 1 分", "分析便利性与可靠性的取舍 1 分"]),
        _s("比较 `/api/chat` 与 `/api/chat/stream` 两条聊天接口的差异。", "参考答案：`/api/chat` 是非流式接口，保留旧的 LangGraph 工具循环；`/api/chat/stream` 是浏览器默认链路，接入 ScopeRouter + Plan-and-Execute，通过 SSE 返回 context、scope、plan、tool_start、tool_end、artifact、chart、text_delta 等事件，并保存更细粒度的历史消息。", "README 当前限制也说明 Plan-and-Execute 目前只接入流式接口。非流式接口仍能调用 ask_data_agent，但不会呈现 plan 面板和 step 状态。答题时不能把两条接口混为一谈。", "app/api/chat.py；app/agent/runtime.py；README.md：API、当前限制。", ["指出非流式 LangGraph 旧链路 1.5 分", "指出流式 Plan-and-Execute 1.5 分", "列出 SSE 事件或保存内容 1.5 分", "说明前端默认使用流式 0.75 分", "说明当前限制 0.75 分"]),
        _s("说明 SQLService 的安全机制，并分析它仍有哪些边界。", "参考答案：SQLService 通过只读 SQLite URI、`PRAGMA query_only=ON`、SQLite authorizer、禁止 DROP/DELETE/UPDATE/INSERT/PRAGMA 等关键字、单语句限制、超时中断和行数上限来控制风险。边界是这些是本地单用户应用层安全，不等同于公网多租户安全；复杂 SQL 资源消耗、业务权限、认证和审计仍需补充。", "安全机制分应用层和数据库层两类。`_validate_sql` 是应用层过滤，`_authorize` 是 SQLite 层拒绝写操作。README 明确说明 SQL 安全校验不应作为面向不可信公网用户的完整安全边界。", "app/services/sql_service.py；README.md：支持的 Agent 工具、当前限制。", ["只读 URI/query_only 1 分", "authorizer 1 分", "关键字和单语句限制 1 分", "超时和行数限制 1 分", "指出安全边界不足 2 分"]),
        _s("如果用户删除数据集，为什么历史对话不会自动删除？这种设计有什么利弊？", "参考答案：README 明确说明删除数据集会删除其本地数据文件，但不会自动删除绑定该数据集的历史对话。优点是保留交互记录、答辩复盘和审计线索；缺点是对话再追问时可能因 dataset 不存在而无法继续执行，且需要 UI 或 API 提示数据缺失。", "这体现数据实体和对话实体分离。ConversationService 保存 JSON 历史，DatasetService 管理本地文件。更完善的设计可在对话列表标记“数据集已删除”，或提供级联删除选项。", "README.md：本地存储；app/services/conversation_service.py；app/services/dataset_service.py。", ["说明 README 行为 1.5 分", "说明对话和数据集分离 1 分", "分析优点 1 分", "分析缺点 1 分", "提出改进 1.5 分"]),
        _s("解释为什么项目新增了自动化评测流水线，以及第一版评测覆盖了哪些层。", "参考答案：评测流水线用于把 ScopeRouter、Planner、Tool Routing、SQL、Python 沙箱等关键能力转成可重复验证的指标，支持课程答辩和复试展示。第一版在 evals/cases 中包含 scope_router、planner、tool_routing、sql_correctness、python_analysis，用 run_evals.py 生成临时电商数据集并输出 latest.json。", "该流水线不是简单 pytest，而是面向系统能力的 benchmark 雏形。它把路由正确性、工具策略、SQL 执行安全、Python 静态安全和 Docker 冒烟分层，能暴露模型和工程逻辑问题。", "evals/README.md；evals/runners/run_evals.py；evals/cases/*.jsonl。", ["说明评测目的 1.5 分", "列出至少 4 类评测集 2 分", "说明 runner 生成 fixture 和报告 1 分", "说明对课程/复试价值 1 分", "区分 pytest 与系统评测 0.5 分"]),
        _s("从数据库课程角度，指出当前项目还可以补哪些内容来更贴近满分要求。", "参考答案：可补 ER 图和关系模式、范式分析、事务边界、清洗/重建 SQLite 的一致性策略、并发控制或 dataset-level lock、EXPLAIN QUERY PLAN 和索引性能对比、备份恢复策略、更多 SQL benchmark。", "这些方向来自数据库系统原理核心主题。项目已有 SQLite、关系、索引和 SQL 安全，但事务、并发、优化器和恢复机制在 README 中还不是主线。不能虚构已实现的并发控制，应作为优化方向提出。", "README.md：当前限制；app/services/sqlite_storage_service.py；evals/README.md。", ["提出建模/范式 1 分", "提出事务/一致性 1.2 分", "提出并发控制 1.2 分", "提出查询计划/索引实验 1.4 分", "明确这是优化方向而非已实现功能 1.2 分"]),
        _s("设计一个针对本项目数据层的故障排查步骤：用户说“我上传了数据但 Agent 不能开始分析”。", "参考答案：先检查数据集是否创建成功和 metadata/manifest 是否存在；再确认 relationship_status 是否已确认；查看 dataset.sqlite3 是否生成；调用 GET /api/datasets/{dataset_id} 检查 schema；看 chat.py 返回是否来自 require_relationship_configuration；必要时运行 materialize_sqlite.py 或重新打开关系配置。", "问题可能不在 LLM，而在数据生命周期。项目强制关系确认，并依赖 SQLite 物化数据库。排查要沿上传、manifest、关系配置、SQLite、聊天 API 逐层定位。", "app/api/chat.py；app/services/dataset_service.py；README.md：使用界面、测试和诊断。", ["检查数据集和 manifest 1 分", "检查关系配置 1.5 分", "检查 SQLite 文件 1 分", "检查 schema/API 1 分", "提出 materialize 或重配关系 1.5 分"]),
    ]
    return Paper(
        title="综合测试卷（一）：项目架构与数据管理",
        subtitle="基础理解、目录结构、数据持久化、数据库服务与 API",
        filename="data_analyse_agent_exam_01_architecture_data.pdf",
        focus="项目目标、数据生命周期、SQLite、Manifest、关系配置、API 与评测入口",
        fill=fill,
        short=short,
    )


def _paper_two() -> Paper:
    fill = [
        _q("浏览器默认使用的流式 Agent 主链路函数是 ______。", "stream_data_agent_events", "chat_stream 调用 stream_data_agent_events，并将其产生的事件格式化为 SSE。该函数实现 ScopeRouter、Planner、Step Executor、Synthesizer 的流程。", "app/api/chat.py；app/agent/runtime.py。"),
        _q("在流式链路中，进入 Planner 前首先由 ______ 判断问题是否适合当前数据集。", "ScopeRouter", "runtime.py 先 yield thinking，然后调用 classify_scope。只有 should_plan 为 true 才继续 build_execution_plan。", "app/agent/runtime.py；app/agent/scope_router.py。"),
        _q("ScopeRouter 支持的四种 scope 是 `in_scope`、`out_of_scope`、`needs_clarification` 和 ______。", "general_help", "scope_router.py 的 Literal 定义和 README 表格都列出这四类。general_help 用于系统用法、工具原理、Docker 等问题。", "app/agent/scope_router.py；README.md：ScopeRouter。"),
        _q("Planner 输出的执行计划模型类是 ______。", "ExecutionPlan", "planner.py 定义 ExecutionPlan，包含 plan_id、mode、primary_intent、user_goal、steps 和 final_response_requirements。", "app/agent/planner.py。"),
        _q("每个计划步骤的模型类是 ______，其中包含 intent、goal、allowed_tools、preferred_tool、depends_on 等字段。", "PlanStep", "PlanStep 在 model_validator 中会根据 intent 清洗 allowed_tools，并计算 retry_limit。", "app/agent/planner.py。"),
        _q("第一版 Planner 最多支持 ______ 个步骤。", "5", "tool_policy.py 中 `MAX_PLAN_STEPS = 5`，ExecutionPlan 的 validator 会截断 steps。", "app/agent/tool_policy.py；app/agent/planner.py。"),
        _q("控制 intent 到工具可见性映射的字典是 ______。", "INTENT_TOOL_POLICY", "tool_policy.py 中定义 query、chart、quality、advanced、cleaning、mixed 对应工具集合，运行时每 step 只绑定 allowed_tools。", "app/agent/tool_policy.py。"),
        _q("intent 为 `advanced` 时，允许的工具是 ______。", "python_analysis", "README 和 tool_policy.py 均说明 advanced 只允许 python_analysis，用于聚类、建模、时间序列、复杂统计等。", "README.md；app/agent/tool_policy.py。"),
        _q("intent 为 `chart` 时，允许的工具是 ______。", "generate_chart", "chart 意图只允许 generate_chart，避免基础图表问题被错误路由到 Python。", "app/agent/tool_policy.py。"),
        _q("清洗类工具默认重试次数是 ______。", "0", "TOOL_RETRY_LIMITS 中 suggest_cleaning、apply_cleaning、reset_cleaning 都是 0。清洗涉及数据修改或状态变化，失败不自动反复尝试。", "app/agent/tool_policy.py。"),
        _q("`python_analysis` 的失败重试次数是 ______。", "3", "TOOL_RETRY_LIMITS 中 python_analysis 为 3，符合之前设计：Python 代码生成/修复比 SQL 更需要重试。", "app/agent/tool_policy.py。"),
        _q("当前步骤绑定工具时，runtime 调用模型的 ______ 方法，只向模型暴露 selected_tools。", "bind_tools", "stream_data_agent_events 中 `model_with_tools = reason_model.bind_tools(selected_tools)`，是控制工具可见性的关键。", "app/agent/runtime.py。"),
        _q("每个 step 内最多工具调用轮数由常量 ______ 控制，当前值为 6。", "MAX_STEP_TOOL_ROUNDS", "runtime.py 中定义 MAX_STEP_TOOL_ROUNDS = 6，用于防止某个步骤陷入无限工具循环。", "app/agent/runtime.py。"),
        _q("当模型只返回 tool_calls 而没有正文解释时，系统会通过 ______ 生成工具调用原因。", "_generate_tool_reason", "_execute_plan_step 检查 response.content 为空时会 yield tool_reason，并调用 _generate_tool_reason。该函数隐藏 dataset_id、max_rows 等参数。", "app/agent/runtime.py。"),
        _q("判断工具结果是否成功的函数是 ______，它优先读取 JSON 中的 `ok` 字段。", "_tool_result_success", "_tool_result_success 会解析 JSON；若存在 ok 字段则按 bool(ok) 判定，比单纯关键词判断更可靠。", "app/agent/runtime.py。"),
        _q("工具执行结束后，runtime 会调用 ______ 把工具结果转换为 Artifact。", "build_tool_artifacts", "_execute_plan_step 在 yield tool_end 后调用 build_tool_artifacts，并继续 yield artifact 事件。", "app/agent/runtime.py；app/agent/artifacts.py。"),
        _q("从 Python 或图表工具结果中提取图表 SSE 事件的函数是 ______。", "_try_build_chart_events", "该函数解析 tool_result JSON，收集 payload.figures、result.figures 或 chart_url/chart_id，并去重后 yield chart 事件。", "app/agent/runtime.py。"),
        _q("`query_data` 工具成功时返回 DataFrame 的 ______ 格式文本。", "Markdown 表格", "make_query_data_tool 中 `return result.to_markdown(index=False)`。这也是 Artifact 中估算 SQL 表格预览行数的依据。", "app/agent/tools.py；app/agent/artifacts.py。"),
        _q("前端输入框上方用于展示执行计划的折叠面板元素 id 是 ______。", "plan-console", "index.html 中新增 details#plan-console，app.js 通过 showPlanConsole/updatePlanStepStatus 更新状态。", "frontend/index.html；frontend/app.js。"),
        _q("SSE 事件中，用于表示步骤开始和结束的事件分别是 ______ 和 ______。", "plan_step_start；plan_step_end", "runtime.py 在每个步骤执行前后 yield 两类事件，chat.py 保存为 role=plan_step，前端据此还原状态。", "app/agent/runtime.py；app/api/chat.py；frontend/app.js。"),
    ]
    short = [
        _s("完整梳理一次 `/api/chat/stream` 请求从进入 API 到前端收到最终回答的调用链。", "参考答案：chat_stream 解析 conversation/dataset，必要时压缩上下文，保存用户消息，发送 context 和 conversation 事件；调用 stream_data_agent_events；runtime 获取 schema 和 tools，先 ScopeRouter，再 Planner；按 step 绑定 allowed_tools，调用工具并发出 tool_start/tool_end/artifact/chart；最后 Synthesizer 汇总 text_delta，API 保存 assistant 文本和事件，前端按 SSE 更新聊天区和 plan-console。", "这条链路横跨 API、ContextService、DatasetService、Agent runtime、工具层、Artifact Store 和前端。答题应体现事件流而不是只说“模型回答”。容易漏掉的是 scope 不通过时不会进入 plan，也不会调用工具。", "app/api/chat.py；app/agent/runtime.py；frontend/app.js。", ["API 解析和上下文 1 分", "ScopeRouter 1 分", "Planner 和 step executor 1.2 分", "工具事件与 artifact/chart 1.3 分", "Synthesizer 和保存历史 1 分", "前端 SSE 展示 0.5 分"]),
        _s("解释“每个 step 只暴露 allowed_tools”如何实现，以及它解决了什么问题。", "参考答案：Planner 为每个 PlanStep 生成 intent 和 allowed_tools，PlanStep validator 用 tool_policy 清洗工具列表。runtime 每步构造 selected_tools 后调用 reason_model.bind_tools(selected_tools)，所以模型只能调用当前 step 允许的工具。它解决“所有问题都走 SQL”或“复杂问题误用简单工具”的问题，也降低误调用清洗/危险工具的风险。", "核心不是提示词一句话，而是运行时重新 bind_tools 的硬约束。allowed_tools 来自 INTENT_TOOL_POLICY，preferred_tool 不在 allowed_tools 时会被纠正。", "app/agent/planner.py；app/agent/tool_policy.py；app/agent/runtime.py。", ["说明 Planner/PlanStep 产生 allowed_tools 1.5 分", "说明 tool_policy 清洗 1 分", "说明 runtime bind_tools 1.5 分", "说明解决工具误路由 1.5 分", "说明安全收益 0.5 分"]),
        _s("比较 `query_data`、`generate_chart` 与 `python_analysis` 三个工具的数据流差异。", "参考答案：query_data 直接用 SQLService.query 执行只读 SQL 并返回 Markdown 表格；generate_chart 先 SQLService.query 得到 DataFrame，再 ChartService 生成 PNG 并返回 chart_url；python_analysis 先 SQLService.query_for_analysis 导出更多行，再 PythonSandboxService 写入 data.json/schema.json/analysis.py，Docker 执行后读取 result.json 并发布 figures。", "三者都依赖 SQLService，但后续处理不同。chart 是基础可视化，python_analysis 是复杂分析 fallback。max_rows 也不同，query_data 默认展示 100/上限 1000，python 分析可导出更多行。", "app/agent/tools.py；app/services/sql_service.py；app/services/chart_service.py；app/services/python_sandbox_service.py。", ["query_data 流程 1.5 分", "generate_chart 流程 1.5 分", "python_analysis 流程 2 分", "指出共同依赖 SQLService 0.5 分", "指出适用场景差异 0.5 分"]),
        _s("分析 `ScopeRouter` 为什么要放在 Planner 之前，而不是让 Planner 自己判断所有问题。", "参考答案：ScopeRouter 先判断问题是否和当前数据集相关，能拦截 out_of_scope、general_help、needs_clarification，避免 Planner 为股市、天气、系统用法等问题牵强生成 SQL/Python 计划。这样减少工具误调用、降低成本，并让用户得到更诚实的限制说明。", "之前“明年股市会好起来没啊”被错误规划成电商销售趋势分析，就是缺少前置 scope 的典型问题。ScopeRouter 的 rule 层和 LLM JSON fallback 共同工作，但第一版仍依赖关键词和 schema 线索。", "app/agent/scope_router.py；app/agent/runtime.py；README.md：ScopeRouter。", ["说明拦截范围 1.5 分", "说明避免错误 Planner 1.5 分", "说明成本和安全收益 1 分", "结合股市例子 1 分", "指出第一版局限 1 分"]),
        _s("说明 Planner 的 fallback 机制如何工作，以及它的价值和局限。", "参考答案：build_execution_plan 先尝试让模型输出 JSON 并校验为 ExecutionPlan；若校验失败、解析失败或异常，就调用 build_fallback_plan。fallback 通过关键词检测 intent，如画图、聚类、清洗、统计等，构造最多 5 个 PlanStep。价值是 LLM 不稳定时仍能运行；局限是关键词粗糙，复杂语义和字段依赖不如模型规划。", "fallback 是工程韧性设计。它不能替代高质量 Planner，但能保证基本问题不因 JSON 格式错误直接失败。清洗 intent 还会经过 _enforce_cleaning_safety。", "app/agent/planner.py；tests/test_planner_policy.py。", ["说明模型 JSON 路径 1 分", "说明异常时 fallback 1 分", "说明关键词检测 intent 1.5 分", "说明价值 1 分", "说明局限 1 分", "提到清洗安全 0.5 分"]),
        _s("解释工具调用失败后的重试策略如何体现不同工具风险。", "参考答案：retry_limit_for_tools 从 TOOL_RETRY_LIMITS 取最大值：python_analysis 为 3，query_data 和 generate_chart 为 2，清洗和固定分析为 0。Python 代码生成容易有语法/路径/result.json 问题，因此允许更多修复；SQL/图表可有限修正；清洗涉及状态变更，不应自动反复执行。", "runtime 中失败会增加 failed_attempts，超过 step.retry_limit 后追加 HumanMessage 告诉模型停止重试。工具风险越高，越需要谨慎；清洗 0 次反映了数据修改操作的保守策略。", "app/agent/tool_policy.py；app/agent/runtime.py。", ["列出 retry 数字 2 分", "说明 Python 为什么 3 1 分", "说明 SQL/图表为什么 2 1 分", "说明清洗为什么 0 1 分", "说明 runtime 超限处理 1 分"]),
        _s("分析 `tool_reason` 事件的作用，以及为什么要隐藏部分参数。", "参考答案：tool_reason 在工具调用前向用户解释本轮操作原因，提升可解释性。若模型只输出结构化 tool_calls 没有自然语言，runtime 调用 _generate_tool_reason。它构造 visible_args 时排除 dataset_id、max_rows，避免泄露不必要内部标识和噪声参数，同时保留 SQL、analysis_goal 等用户能理解的信息。", "这不是核心计算逻辑，而是交互体验和可审计性设计。它也在 prompt 中要求不要泛泛复述工具名称，若 LLM 调用失败则回退到 _build_tool_reason。", "app/agent/runtime.py：_generate_tool_reason、_build_tool_reason。", ["说明解释工具原因 1.5 分", "说明触发条件 1 分", "说明隐藏 dataset_id/max_rows 1 分", "说明用户体验/审计价值 1 分", "说明 fallback 1.5 分"]),
        _s("前端 plan-console 如何与后端 SSE 事件协作显示步骤状态？", "参考答案：后端 runtime 发出 plan、plan_step_start、plan_step_end；chat.py 将 plan_step 事件保存进对话。前端 app.js 收到 plan 时 showPlanConsole，收到 start 时 updatePlanStepStatus 为 running，收到 end 时根据 success 设为 done 或 failed。index.html 中 details#plan-console 位于输入框上方，可折叠展开。", "plan-console 不是单纯历史消息，它承担当前执行状态面板的职责。恢复历史时，renderConversationMessages 遇到 role=plan 和 role=plan_step 也会重建状态。", "frontend/index.html；frontend/app.js；app/api/chat.py；app/agent/runtime.py。", ["后端事件 1.5 分", "API 保存 plan_step 1 分", "前端函数和状态 2 分", "可折叠面板位置 0.75 分", "历史恢复 0.75 分"]),
        _s("说明同一轮最终总结和下一轮历史上下文在工具结果可见性上的差异。", "参考答案：同一轮内，runtime 会把完整 ToolMessage content 加入 messages，因此最终 Synthesizer 能看到当前轮完整工具结果；下一轮构造历史时，ContextService 会把历史 tool 转成 result_preview，并把 artifact 转成 summary + preview，不再注入完整 result.json、长 stdout/stderr 或完整 python_code。", "这个差异平衡了回答质量和上下文成本。同轮需要完整材料保证总结准确；跨轮只需事实索引，防止上下文膨胀。容易混淆的是 artifact 并没有完全替代同轮 ToolMessage。", "app/agent/runtime.py；app/services/context_service.py；app/agent/artifacts.py。", ["同轮完整 ToolMessage 2 分", "下一轮 result_preview/artifact 2 分", "说明设计取舍 1.5 分", "指出常见误解 0.5 分"]),
        _s("如果复杂分析问题被错误规划成 `query_data`，你会如何定位和修复？", "参考答案：先查看 scope 是否 in_scope；再检查 plan-console 中 primary_intent、steps 和 allowed_tools；查看 planner_eval/tool_routing_eval 是否缺对应用例；检查 planner.py 的关键词、系统提示和 tool_policy；必要时加入评测用例，如“聚类、热力图、时间序列”必须 advanced/python_analysis；最后运行 evals 和 pytest。", "这个问题可能来自 ScopeRouter、Planner、tool_policy 或提示词。修复不应只改 prompt，还要补评测防回归。若是模型输出 allowed_tools 错，PlanStep validator 会清洗；若 intent 本身错，需要改善 Planner 或 fallback。", "app/agent/planner.py；app/agent/tool_policy.py；evals/cases/tool_routing_eval.jsonl；evals/runners/run_evals.py。", ["定位 scope/plan 1 分", "检查 allowed_tools/tool_policy 1.2 分", "检查 Planner/fallback 1.2 分", "补评测 1.2 分", "运行回归 1 分", "区分 intent 错和 allowed_tools 错 0.4 分"]),
    ]
    return Paper(
        title="综合测试卷（二）：Agent 执行链路与工具协作",
        subtitle="ScopeRouter、Planner、Tool Policy、SSE、前端计划面板与工具调用",
        filename="data_analyse_agent_exam_02_agent_workflow.pdf",
        focus="Plan-and-Execute、工具可见性控制、SSE 事件、工具结果、前端状态与多轮上下文",
        fill=fill,
        short=short,
    )


def _paper_three() -> Paper:
    fill = [
        _q("复杂分析 fallback 使用的 Agent 工具名称是 ______。", "python_analysis", "README 和 tools.py 都说明 python_analysis 用于相关性热力图、聚类、复杂异常检测、时间序列、多步骤统计等。", "README.md；app/agent/tools.py。"),
        _q("Python 沙箱服务类是 ______。", "PythonSandboxService", "该类负责创建 run 目录、写入 input/data.json、schema.json、work/analysis.py，调用 docker run 并读取 output/result.json。", "app/services/python_sandbox_service.py。"),
        _q("Python 沙箱默认镜像名是 ______。", "data-analyse-agent-python-sandbox:latest", "python_sandbox_service.py 中 DEFAULT_SANDBOX_IMAGE 为该值，README 也给出 docker build 命令。", "app/services/python_sandbox_service.py；README.md：Python 沙箱 Docker 环境。"),
        _q("Python 沙箱要求脚本读取的输入 JSON 路径是 ______。", "/workspace/input/data.json", "PythonAnalysisArgs 描述和 schema.json 都要求脚本读取该路径。服务会把 input_dir 只读挂载到 /workspace/input。", "app/agent/tools.py；app/services/python_sandbox_service.py。"),
        _q("Python 沙箱要求脚本写出的结果文件路径是 ______。", "/workspace/output/result.json", "run_analysis 执行后检查 output/result.json 是否存在、大小是否超限、是否为 JSON 对象。", "app/services/python_sandbox_service.py。"),
        _q("沙箱基础静态检查禁止导入的模块包括 `socket`、`requests` 和 ______。", "subprocess", "PROHIBITED_IMPORT_MODULES = {socket, requests, subprocess}，防止网络访问和子进程逃逸。", "app/services/python_sandbox_service.py。"),
        _q("沙箱允许读写的容器绝对路径必须以 ______ 开头。", "/workspace", "_validate_python_code 会检查绝对路径字符串，非 /workspace 路径会被拒绝，Windows 绝对路径也会被检测。", "app/services/python_sandbox_service.py。"),
        _q("沙箱 result.json 最大体积限制常量是 ______ 字节。", "2 * 1024 * 1024", "MAX_RESULT_JSON_BYTES = 2 * 1024 * 1024。超过时会报错，避免模型上下文和前端被大 JSON 拖垮。", "app/services/python_sandbox_service.py。"),
        _q("stdout/stderr 截断长度常量是 ______ 字符。", "4000", "MAX_STREAM_CHARS = 4000，用于限制 Docker 脚本输出进入工具结果的大小。", "app/services/python_sandbox_service.py。"),
        _q("沙箱最多发布的图片数量是 ______。", "10", "MAX_FIGURES = 10，_publish_figures 只取排序后的前 10 个图片文件。", "app/services/python_sandbox_service.py。"),
        _q("沙箱健康检查 API 路径是 ______。", "/api/sandbox/health", "diagnostics.py 暴露 GET /sandbox/health，主应用挂载在 /api 下，因此 README 记录为 /api/sandbox/health。", "app/api/diagnostics.py；README.md：诊断。"),
        _q("健康检查依次检查 docker CLI、docker daemon 和 ______。", "sandbox_image", "PythonSandboxService.health_check 构造 docker_cli、docker_daemon、sandbox_image 三项检查，并给出 fix 命令。", "app/services/python_sandbox_service.py。"),
        _q("Artifact 的 Pydantic 模型类名是 ______。", "AnalysisArtifact", "artifacts.py 中 AnalysisArtifact 定义 artifact_id、step_id、type、title、summary、source_tool、success、preview、content。", "app/agent/artifacts.py。"),
        _q("Python 分析成功生成的 Artifact 类型是 ______。", "python_result", "_python_analysis_artifact 返回 type='python_result'，区别于 chart、table、json、text、error。", "app/agent/artifacts.py。"),
        _q("历史上下文中会被移除的工具参数包括 `dataset_id` 和 ______。", "python_code", "sanitize_tool_args_for_context 会跳过 dataset_id 和 python_code，避免完整脚本进入多轮上下文。", "app/agent/artifacts.py；app/services/context_service.py。"),
        _q("ContextService 默认压缩阈值常量是 ______。", "0.8", "DEFAULT_COMPACT_THRESHOLD = 0.8。get_context_stats 根据 estimated_tokens 与 threshold_tokens 判断是否压缩。", "app/services/context_service.py。"),
        _q("上下文压缩时保留最近消息数量的常量是 ______。", "12", "RECENT_MESSAGE_COUNT = 12。compact_if_needed 会把 active_messages 末尾 12 条保留，其余总结。", "app/services/context_service.py。"),
        _q("自动化评测报告默认写入 ______。", "evals/reports/latest.json", "run_evals.py 中 REPORTS_DIR 为 evals/reports，并写 latest.json。", "evals/runners/run_evals.py。"),
        _q("运行 Docker 冒烟评测需要给 runner 增加参数 ______。", "--run-docker", "evals/README.md 和 argparse 均说明默认跳过 Docker，--run-docker 才真正执行 PythonSandboxService.run_analysis。", "evals/README.md；evals/runners/run_evals.py。"),
        _q("当前评测流水线会临时构造的小型数据集名称标识是 ______。", "ecommerce_small", "run_evals.py 中 schema_texts 和 JSONL cases 使用 ecommerce_small，runner 会创建 orders、order_items、payments、products、customers、reviews 等表。", "evals/runners/run_evals.py；evals/cases/*.jsonl。"),
    ]
    short = [
        _s("梳理 `python_analysis` 从 SQL 到 Docker 再到图表和 Artifact 的完整流程。", "参考答案：工具先用 SQLService.query_for_analysis 执行只读 SQL 得到 DataFrame；PythonSandboxService 创建 run_id 目录，写 input/data.json、input/schema.json、work/analysis.py；构造 docker run，挂载 input/work/output；脚本写 /workspace/output/result.json 和图片；服务读取并校验 result.json，发布图片到 charts；工具返回 ok/run_id/result/figures/stdout/stderr/warnings；runtime 生成 Artifact 和 chart 事件。", "该流程是本项目复杂分析 fallback 的核心。SQL 负责取数，Docker 负责隔离动态代码，Artifact 负责跨轮摘要，chart 事件负责前端展示。容易漏掉的是 SQL 空结果会直接返回 ok=false，不执行沙箱。", "app/agent/tools.py；app/services/python_sandbox_service.py；app/agent/runtime.py；app/agent/artifacts.py。", ["SQL 取数 1 分", "run 目录和文件写入 1.2 分", "Docker 挂载执行 1.2 分", "result/figures 校验发布 1.2 分", "工具返回结构化结果 0.8 分", "artifact/chart 事件 0.6 分"]),
        _s("分析 Python 沙箱的安全机制和仍然存在的局限。", "参考答案：安全机制包括 Docker 隔离、只读挂载 input/work、output 可写、无网络、内存/CPU/超时配置、静态 AST 检查禁止 socket/requests/subprocess、限制 /workspace 外绝对路径、stdout/stderr 截断、result.json 大小和图片数量限制。局限是静态检查不是完整沙箱证明，Docker 权限和宿主机安全仍重要，恶意代码可能通过复杂方式消耗资源或绕过简单字符串检查。", "项目定位是本地单用户开发，不是公网多租户安全产品。安全设计是多层防线：SQL 层限制输入数据，Docker 层隔离执行，静态检查减少常见危险行为，输出限制保护上下文和 UI。", "app/services/python_sandbox_service.py；README.md：Python 沙箱、当前限制。", ["列出 Docker/挂载/资源限制 2 分", "列出静态检查 1.5 分", "列出输出限制 1 分", "说明局限 1 分", "结合本地单用户定位 0.5 分"]),
        _s("解释 Artifact Store 为什么能缓解多轮上下文膨胀。", "参考答案：每次工具结果被 build_tool_artifacts 转成轻量 AnalysisArtifact，只保留 type、title、summary、source_tool、preview、content 等摘要信息。下一轮 ContextService 对历史 tool 只注入 result_preview，并对 artifact 注入 summary + preview，同时移除 python_code、长 stdout/stderr 和完整大 JSON。", "Artifact Store 的目标不是替代所有原始数据，而是在跨轮对话中提供事实索引。当前同轮最终总结仍可看到完整 ToolMessage，跨轮则用 artifact 维持可追问性和低 token 成本。", "app/agent/artifacts.py；app/services/context_service.py；README.md：Artifact Store。", ["说明 artifact 字段 1.5 分", "说明 ContextService 注入策略 2 分", "说明移除大内容 1 分", "说明同轮/跨轮差异 1 分", "说明设计目标 0.5 分"]),
        _s("如果用户反馈“Python 图表生成了但前端 404”，你会如何定位？", "参考答案：先看 python_analysis result 中 figures 的 chart_url 是否为 /charts/{id}.png；检查 PythonSandboxService._publish_figures 是否把 output 图片复制到 app/storage/charts；确认 result.json 中 figures 的 path/file/chart_path 是否指向 /workspace/output 下的实际文件；检查 FastAPI 静态路由是否发布 /charts；查看服务器日志中的 404 路径是否仍是 /workspace/output/... 旧路径。", "这个问题曾出现过：如果直接把容器内 /workspace/output/correlation_analysis.png 给前端，浏览器访问后端当然 404。正确做法是发布到 app/storage/charts 并改写 chart_url。", "app/services/python_sandbox_service.py：_publish_figures、_rewrite_result_figure_paths；app/agent/runtime.py：_try_build_chart_events。", ["检查 chart_url 1 分", "检查发布到 charts 1.5 分", "检查 result.json figures 路径 1 分", "检查静态路由/日志 1 分", "说明 /workspace/output 不能直接给浏览器访问 1.5 分"]),
        _s("说明沙箱健康检查失败时应该给用户哪些明确修复信息。", "参考答案：如果 docker_cli 失败，提示安装 Docker Desktop 并确保 docker 在 PATH；如果 docker_daemon 失败，提示启动 Docker Desktop 并使用有权限访问 docker_engine 的终端；如果 sandbox_image 失败，提示执行 `docker build -t data-analyse-agent-python-sandbox:latest .\\docker\\python-sandbox` 或检查 PYTHON_SANDBOX_IMAGE。", "health_check 返回每个 check 的 ok、command 和 fix。修复信息必须具体到命令或操作，不能只说“Docker 不可用”。这对本地 Windows 环境尤其重要。", "app/services/python_sandbox_service.py：health_check；app/api/diagnostics.py；README.md：诊断。", ["docker CLI 修复 1.5 分", "daemon 权限/启动修复 1.5 分", "镜像构建修复 1.5 分", "提到 PYTHON_SANDBOX_IMAGE 0.75 分", "说明返回结构价值 0.75 分"]),
        _s("评测流水线为什么要分层，而不是只评最终答案？", "参考答案：项目由 ScopeRouter、Planner、Tool Routing、SQL、Python 沙箱、Artifact、前端事件组成。只评最终答案无法定位问题在哪层。分层评测能分别统计 scope accuracy、intent/tool 合法性、SQL 安全执行、Python 静态拦截、Docker 冒烟和 artifact schema，便于回归和展示工程可靠性。", "run_evals.py 的设计体现分层：scope 使用 classify_scope_by_rules，planner/tool_routing 使用 fallback plan，sql 用 SQLService 执行 reference_sql，python 用静态校验和可选 Docker。这样无需每次依赖 LLM，也能稳定暴露工程逻辑问题。", "evals/runners/run_evals.py；evals/cases/*.jsonl；evals/README.md。", ["说明系统多阶段 1.5 分", "说明最终答案不可定位 1 分", "列出评测层 2 分", "说明 runner 本地确定性 1 分", "说明回归价值 0.5 分"]),
        _s("如果要把当前 evals 扩展到 100 条以上，你会如何设计数据和指标？", "参考答案：保持 ecommerce_small 作为固定 fixture，同时增加更多中文问题模板；scope 约 30 条，planner/tool routing 各 25 条，SQL 20 条，Python 15 条，安全 10 条。指标包括 pass_rate、scope 准确率、must_use_tools 命中率、forbidden tool violation、SQL 执行正确率、Python 静态拦截率、Docker smoke、artifact 生成率。", "扩展评测不能只堆数量，要保持覆盖类型均衡并可自动判定。对 SQL 应尽量有 reference_sql 和 expected rows/columns；对 Python 应检查 result_schema、figures 和安全拒绝。", "evals/README.md；evals/runners/run_evals.py；evals/cases/*.jsonl。", ["数量和类别规划 1.5 分", "固定 fixture/可复现 1 分", "SQL 断言方式 1 分", "Python/安全断言 1 分", "指标设计 1.5 分"]),
        _s("分析当前上下文压缩策略的优点和潜在问题。", "参考答案：优点是估算 token、超过阈值时压缩早期消息，并保留最近 12 条，减少长对话成本；还会通过 artifact 和 result_preview 避免大结果进入历史。潜在问题是 token 估算可能不精确，压缩依赖模型调用可能失败或丢细节，summary 可能遗漏字段、SQL 或图表结论。", "ContextService 同时有模型 tokenizer 计数和字符估算 fallback。压缩失败时 fallback_summary 截取近期片段。更稳的未来方向是 artifact manifest 引用、按 artifact_id 检索、结构化摘要和更强的测试。", "app/services/context_service.py；app/agent/artifacts.py；tests/test_context_service.py。", ["优点 1.5 分", "保留最近消息和阈值 1 分", "artifact/result_preview 1 分", "潜在问题 1.5 分", "优化方向 1 分"]),
        _s("从安全角度比较 SQLService 和 PythonSandboxService 的防护重点。", "参考答案：SQLService 保护数据库不被写入或长时间占用，重点是 SELECT/WITH、禁止写操作、只读 URI、authorizer、超时和行数限制；PythonSandboxService 保护宿主机和运行环境，重点是 Docker 隔离、无网络、资源限制、禁止危险模块、路径限制和输出大小控制。二者对应不同风险面。", "SQL 是受限查询语言，风险是数据修改、注入写操作和资源消耗；Python 是通用代码，风险更大，需要容器和静态检查。python_analysis 先经过 SQLService 取数，再进入沙箱，形成串联防线。", "app/services/sql_service.py；app/services/python_sandbox_service.py；app/agent/tools.py。", ["SQLService 防护 2 分", "PythonSandboxService 防护 2 分", "风险面比较 1 分", "串联防线 1 分"]),
        _s("提出一个把本项目从课程设计升级为复试展示项目的优化方案。", "参考答案：可把项目包装为“安全可控的本地结构化数据分析 Agent 框架”。补充 100+ 条分层评测、README 结果表、ER 图和关系模式、索引/EXPLAIN 实验、plan 面板演示、Docker 沙箱健康检查截图、artifact 多轮追问案例，以及当前限制和未来 DAG/并行/重规划路线图。", "复试项目需要技术问题、方法、评测和可复现演示。不能只展示页面，还要证明设计选择有效：ScopeRouter 降低越界调用、tool policy 限制工具误用、artifact 降低上下文、sandbox 提升安全。", "README.md；evals/README.md；frontend/app.js；app/agent/*；app/services/*。", ["提出项目定位 1 分", "补评测和结果表 1.5 分", "补数据库课程材料 1 分", "补演示案例 1 分", "说明设计效果指标 1 分", "提出现实路线图 0.5 分"]),
    ]
    return Paper(
        title="综合测试卷（三）：安全机制、沙箱、上下文与评测优化",
        subtitle="Python 沙箱、Artifact Store、上下文压缩、自动化评测和工程改进",
        filename="data_analyse_agent_exam_03_security_evals.pdf",
        focus="Docker 沙箱、安全边界、Artifact、ContextService、自动化评测与复试展示优化",
        fill=fill,
        short=short,
    )


def _validate_papers(papers: list[Paper]) -> None:
    for paper in papers:
        fill_score = sum(question.score for question in paper.fill)
        short_score = sum(question.score for question in paper.short)
        total = fill_score + short_score
        if len(paper.fill) != 20:
            raise ValueError(f"{paper.title} fill count is {len(paper.fill)}, expected 20")
        if len(paper.short) != 10:
            raise ValueError(f"{paper.title} short count is {len(paper.short)}, expected 10")
        if fill_score != 40 or short_score != 60 or total != 100:
            raise ValueError(f"{paper.title} score mismatch: fill={fill_score}, short={short_score}, total={total}")
        for question in paper.fill + paper.short:
            for field_name in ("prompt", "answer", "analysis", "source"):
                if not getattr(question, field_name):
                    raise ValueError(f"{paper.title} has question missing {field_name}: {question}")
            if question.score <= 0:
                raise ValueError(f"{paper.title} has invalid score: {question}")


if __name__ == "__main__":
    main()
