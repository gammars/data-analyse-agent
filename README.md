# Data Analyse Agent

一个基于 FastAPI、LangChain、LangGraph、Pandas 和 SQLite 的本地数据分析智能体。

用户可以上传 CSV 或 Excel 文件，把多个文件组织为同一个多表数据集，然后通过自然语言让 Agent 生成只读 SQL、执行查询、解释结果、完成数据质量分析并生成图表。项目自带浏览器界面，前端是由 FastAPI 直接托管的原生 HTML/CSS/JavaScript，不需要 Node.js 或 `npm run dev`。

> 本文档描述的是仓库当前已经实现的功能。

## 当前功能

- 上传一个或多个 CSV、XLSX、XLS 文件创建数据集。
- 每个数据集会同时生成独立的 `dataset.sqlite3`，其中包含该数据集的全部表。
- 原始文件保存在 `raw/` 且不被修改；每张逻辑表在 `processed/` 中保存为独立 CSV。
- 每个数据集包含标准 `manifest.json`，记录字段统计、清洗历史、主外键和索引配置。
- 上传或追加数据后自动分析主键、外键和索引候选，并由 LLM 给出受候选集合约束的推荐和理由。
- 关系配置是新数据集的必经步骤；用户可明确选择不建立某类约束，但必须确认一次才能使用 Agent。
- 自动生成可复现的 `schema.sql` 和 `indexes.sql`；Agent 从 SQLite PRAGMA 读取实际关系结构。
- 向当前数据集继续追加文件；Excel 的每个 Sheet 会成为一张表。
- 本地保存、重命名和删除数据集，并可删除数据集中的单张表。
- 自动识别字段类型、缺失值、样例数据和可供 Agent 使用的 Schema。
- 使用 SQLite 只读连接对单表或多表数据执行 SQL，支持 JOIN、聚合和子查询。
- 使用 LangChain StructuredTool 暴露 SQL、分析和图表工具。
- 使用 LangGraph 实现 `agent -> tools -> agent -> finish` 工具循环。
- 使用 SSE 流式展示模型文本、思考状态、工具调用、工具结果和图表。
- 支持柱状图、折线图、饼图和散点图，生成的 PNG 文件保存在本地。
- 支持数据概览、缺失值、描述性统计、相关性和 IQR 异常值分析。
- 支持通过 Agent Tools 生成清洗建议、执行确认后的安全清洗，并从 raw 撤销恢复。
- 对话以 JSON 保存，可恢复、切换和删除；每个对话绑定一个数据集。
- 显示上下文窗口估算，并在达到阈值后压缩早期对话。

## 技术栈

| 模块 | 技术 |
|---|---|
| 后端 | Python 3.11、FastAPI、Uvicorn |
| Agent | LangChain、LangGraph、OpenAI-compatible API |
| 数据处理 | Pandas、NumPy、SciPy、scikit-learn |
| 数据库存储 | 每个数据集一个 SQLite3 文件 |
| SQL 查询 | SQLite 只读 URI、query_only、authorizer、超时中断 |
| 图表 | Matplotlib、Seaborn |
| 前端 | HTML、CSS、JavaScript、Fetch API、SSE |
| 持久化 | 本地数据文件、JSON 对话、PNG 图表 |

## 工作流程

```text
上传 CSV / Excel
       ↓
Pandas 读取文件、生成 Schema 并写入 SQLite
       ↓
用户输入自然语言问题
       ↓
LangGraph Agent 选择工具并生成参数
       ↓
SQLite 只读查询 / 数据分析 / 图表生成
       ↓
工具结果返回模型
       ↓
流式输出中文结论并保存对话
```

当工具调用模型只返回结构化 `tool_calls` 而没有正文时，系统会额外发起一次不绑定工具的模型调用，根据用户问题、实际 SQL 和上一次工具结果生成调用原因。因此，一轮工具调用通常会产生两次模型 API 请求。

## 环境要求

- Python `3.11`
- 可用的 OpenAI-compatible 大模型 API
- 支持 Function Calling / Tool Calling 和流式响应的模型

项目在 Windows 下开发，后端代码也可以在其他支持 Python 3.11 的系统上运行。

## 安装


```powershell
pip install -r requirements.txt
```

如果还需要使用 `data-analyse-agent doctor` CLI，可安装本项目：

```powershell
pip install -e .
data-analyse-agent doctor
```

## 配置模型

复制环境变量示例：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```env
APP_ENV=development
LOG_LEVEL=INFO
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# 留空或设置为 0 表示不设置应用层上传大小限制
DATA_ANALYSE_MAX_UPLOAD_MB=
```

注意：普通聊天接口测试成功不代表 Agent 一定可用。模型还必须正确支持 OpenAI-compatible Tool Calling 和流式工具调用。不要提交包含真实密钥的 `.env`。

## 启动项目

```powershell
python run.py
```

打开浏览器访问：

```text
http://127.0.0.1:8000
```

`run.py` 默认启用 Uvicorn 自动重载。前端由 FastAPI 直接提供，不需要单独启动前端服务。

## Python 沙箱 Docker 环境

复杂分析 fallback 会使用独立 Docker 镜像执行生成的 Python 脚本。后端仍然运行在本机 Conda 环境中，Docker 只负责隔离执行动态分析代码。

构建沙箱镜像：

```powershell
docker build -t data-analyse-agent-python-sandbox:latest .\docker\python-sandbox
```

验证沙箱能读取输入 JSON 并写出结果：

```powershell
python .\scripts\verify_python_sandbox.py
```

验证后端 `PythonSandboxService` 能调用 Docker 沙箱：

```powershell
python .\scripts\verify_python_sandbox_service.py
```

如果普通终端提示无法连接 `npipe:////./pipe/docker_engine`，请确认 Docker Desktop 已启动，并以有 Docker 权限的终端运行后端。镜像名称可通过 `.env` 覆盖：

```env
PYTHON_SANDBOX_IMAGE=data-analyse-agent-python-sandbox:latest
PYTHON_SANDBOX_TIMEOUT_SECONDS=60
PYTHON_SANDBOX_MEMORY=512m
PYTHON_SANDBOX_CPUS=1
PYTHON_SANDBOX_MAX_ROWS=50000
```

## 使用界面

1. 点击“上传并创建新数据集”，可一次选择多个 CSV 或 Excel 文件。
2. 在左侧选择当前数据集；需要增加表时点击“追加文件到当前数据集”。
3. 上传处理完成后会自动打开“关系配置”弹窗；检查 AI 推荐，选择主键、外键和索引，然后点击“确认配置并继续”。
4. 在输入框中提出问题，例如：`统计每个类别的销售额总和。`
5. Agent 会依次展示调用原因、工具卡片、查询结果处理状态和最终回答。
6. 可在左侧恢复或删除历史对话，也可以切换当前数据集。

单表数据集通过 SQLite 视图兼容 `data_table` 别名；多表数据集必须使用 Schema 中的具体表名。包含空格、换行或特殊字符的字段会使用 SQLite 双引号标识符，例如：

```sql
SELECT "订单状态", COUNT(*) AS "订单数"
FROM "orders"
GROUP BY "订单状态";
```

## 支持的 Agent 工具

| 工具 | 用途 |
|---|---|
| `query_data` | 执行只读 SELECT / WITH SQL |
| `generate_chart` | 根据 SQL 结果生成 bar、line、pie、scatter 图表 |
| `profile_data` | 输出整体数据质量和结构概览 |
| `missing_value_analysis` | 分析字段缺失数量和缺失率 |
| `descriptive_statistics` | 生成数值和非数值字段描述性统计 |
| `correlation_analysis` | 计算数值字段相关性及强相关字段对 |
| `outlier_detection` | 使用 IQR 方法检测异常值 |
| `python_analysis` | 将只读 SQL 结果导出为 JSON，并在 Docker 沙箱中执行生成的 Python 分析脚本 |
| `suggest_cleaning` | 检查 processed 数据并生成建议，不修改数据 |
| `apply_cleaning` | 执行用户确认的预定义清洗操作并重建 SQLite |
| `reset_cleaning` | 从 raw 恢复指定表或整个数据集 |

`apply_cleaning` 只允许以下预定义操作：

```text
drop_duplicate_rows
drop_empty_rows
drop_empty_columns
trim_strings
convert_type
handle_missing
sample_rows
```

推荐对话流程：先说“检查这个数据集并给出清洗建议，不要修改数据”，确认具体表和操作后再要求执行。所有清洗只修改 `processed/`，`raw/` 原件不会改变；执行后 Manifest 会记录操作历史并自动重建 SQLite。

SQLService 使用只读 URI 打开数据库，启用 `PRAGMA query_only=ON` 和 SQLite authorizer。它只允许以 `SELECT` 或 `WITH` 开头的单条查询，限制执行时间，并在数据库层将返回行数限制为最多 1000 行。

## 本地存储

```text
app/storage/
├── datasets/
│   ├── metadata.json
│   └── {dataset_id}/
│       ├── raw/             # 上传的原始 CSV / Excel，永不修改
│       ├── processed/       # 每张逻辑表对应的当前 CSV
│       ├── manifest.json    # 字段、清洗、关系和索引配置
│       ├── relationship_advice.json # 基于当前候选缓存的 LLM 建议
│       ├── schema.sql       # 根据 Manifest 生成的建表语句
│       ├── indexes.sql      # 根据 Manifest 生成的索引语句
│       └── dataset.sqlite3  # 带已确认约束和索引的数据库
├── conversations/
│   └── {conversation_id}.json
├── charts/
│   └── {chart_id}.png
└── python_runs/
    └── {run_id}/
        ├── input/
        │   ├── data.json
        │   └── schema.json
        ├── work/
        │   └── analysis.py
        └── output/
            └── result.json
```

这些目录默认被 `.gitignore` 忽略。迁移或打包项目时，需要根据用途单独备份数据集、对话、图表和 Python 沙箱运行记录。删除数据集会删除其本地数据文件，但不会自动删除已经绑定该数据集的历史对话。

## API

### 数据集

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/datasets` | 获取本地数据集列表 |
| `POST` | `/api/upload` | 上传一个或多个文件创建数据集，表单字段为 `files` |
| `GET` | `/api/datasets/{dataset_id}` | 获取数据集 Schema 和表信息 |
| `GET` | `/api/datasets/{dataset_id}/manifest` | 获取数据集 Manifest |
| `GET` | `/api/datasets/{dataset_id}/relationships` | 获取已保存关系及验证结果 |
| `GET` | `/api/datasets/{dataset_id}/relationships/suggestions` | 生成统计候选和 LLM 关系建议，可用 `refresh_llm=true` 强制刷新 |
| `GET` | `/api/datasets/{dataset_id}/relationships/validation` | 验证当前关系完整性 |
| `PUT` | `/api/datasets/{dataset_id}/relationships` | 确认、保存关系并重建 SQLite |
| `PATCH` | `/api/datasets/{dataset_id}` | 重命名数据集 |
| `DELETE` | `/api/datasets/{dataset_id}` | 删除数据集及本地文件 |
| `POST` | `/api/datasets/{dataset_id}/tables` | 追加文件或数据表 |
| `DELETE` | `/api/datasets/{dataset_id}/tables/{table_name}` | 删除指定表 |

上传示例：

```powershell
curl.exe -X POST "http://127.0.0.1:8000/api/upload" `
  -F "files=@D:\data\orders.csv" `
  -F "files=@D:\data\customers.csv"
```

### 对话和聊天

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/conversations` | 获取对话列表 |
| `POST` | `/api/conversations` | 创建并绑定数据集的对话 |
| `GET` | `/api/conversations/{conversation_id}` | 恢复对话 |
| `PATCH` | `/api/conversations/{conversation_id}/dataset` | 切换对话数据集 |
| `DELETE` | `/api/conversations/{conversation_id}` | 删除对话 JSON |
| `POST` | `/api/chat` | 非流式 Agent 查询 |
| `POST` | `/api/chat/stream` | SSE 流式 Agent 查询 |

请求示例：

```json
{
  "dataset_id": "上传后返回的 dataset_id",
  "message": "统计每个类别的销售额总和。"
}
```

继续已有对话时传入 `conversation_id`，服务端会使用该对话绑定的数据集：

```json
{
  "conversation_id": "已有的 conversation_id",
  "message": "再画一张柱状图。"
}
```

流式接口可能返回以下事件：

```text
context_compacting
context
conversation
status
thinking
tool_reason
tool_start
tool_end
chart
text_delta
error
done
```

图表文件通过 `/charts/{chart_id}.png` 访问。

### 诊断

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/sandbox/health` | 检查 Docker CLI、Docker daemon 和 Python 沙箱镜像是否可用 |

如果检查失败，响应中会包含对应的 `fix` 修复命令或操作提示。

## 测试和诊断

运行自动测试：

```powershell
python -m pytest
```

测试大模型普通聊天接口：

```powershell
python scripts/test_llm_api.py --list-models --timeout 60
```

启动项目后，测试上传和聊天 API：

```powershell
python scripts/test_chat_api.py --stream --timeout 180
```

为旧数据集生成缺失的 SQLite 文件并检查每张表的行数：

```powershell
python scripts/materialize_sqlite.py
```

强制重新构建已有 SQLite 文件：

```powershell
python scripts/materialize_sqlite.py --rebuild
```

如果 `pytest` 在 Windows 上提示无法写入 `.pytest_cache`，但测试结果仍为 `passed`，通常只是缓存目录权限警告，不影响测试本身。

## 项目结构

```text
.
├── app/
│   ├── agent/               # LangGraph、提示词、模型和工具
│   ├── api/                 # 数据集、聊天和对话 API
│   ├── services/            # 数据、SQL、分析、图表和上下文服务
│   ├── schemas/             # Manifest 等结构化数据契约
│   ├── storage/             # 本地数据、对话和图表
│   └── main.py
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── scripts/
│   ├── materialize_sqlite.py
│   ├── test_chat_api.py
│   └── test_llm_api.py
├── src/data_analyse_agent/  # CLI 和基础配置
├── tests/
├── .env.example
├── pyproject.toml
├── requirements.txt
└── run.py
```

## 当前限制

- LLM 只能从唯一率、字段名、类型和值包含关系产生的候选中推荐，业务语义仍需用户确认，系统不会自动应用。
- 清洗 Tools 只提供预定义操作，不支持任意 Python、自定义表达式或复杂业务清洗脚本。
- SQL 安全校验以应用层规则为主，不应作为面向不可信公网用户的完整安全边界。
- 上传文件会先读入内存，大文件会增加内存占用和处理时间。
- 项目没有用户认证和权限隔离，当前定位是本地单用户开发与实验环境。
- 上下文 Token 数量是近似估算，压缩过程还会产生额外模型调用。
- 自动测试已覆盖 SQLite 建库、主外键和索引重建、关系完整性、多表 JOIN、只读安全、查询超时、清洗建议、应用与重置、类型持久化和工具原因生成；上传 API、完整 Agent 和图表流程仍需继续补充。

如用于数据库课程实验，还需要补充公开数据来源说明、课程案例的关系配置、业务案例截图及实验报告。
