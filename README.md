# Data Analyse Agent

一个面向数据分析任务的 Python Agent 项目。当前已完成阶段 1：项目骨架、阶段 2：数据上传、阶段 3：基础 SQL 查询工具、阶段 4：LangGraph Agent、阶段 5：图表生成、阶段 6：数据概览和分析，并已扩展对话管理、上下文压缩和多表数据集能力。

## 阶段 1：项目骨架

- 使用独立 conda 环境，Python 版本固定为 3.11。
- 采用 `src/` 目录组织应用代码。
- 提供基础 CLI 入口，便于后续扩展分析命令。
- 提供配置读取、环境变量示例、依赖清单和烟雾测试。

## 阶段 2：数据上传

- 实现 `POST /api/upload`，支持上传一个或多个 CSV、XLSX、XLS 文件，并合成为一个多表数据集。
- 上传文件保存到 `app/storage/datasets/{dataset_id}/`，使用 UUID 数据集目录隔离文件。
- 使用 Pandas 将上传文件读取为 DataFrame。
- 自动生成 `dataset_id`。
- 返回行数、列数、字段名、字段类型、缺失值统计、样例数据和 schema 文本。
- 提供简单前端页面，上传后展示字段名、行数和列数。
- 限制上传文件类型；默认不设置应用层文件大小上限，可通过 `DATA_ANALYSE_MAX_UPLOAD_MB` 配置 MB 级上传限制。
- Excel 会按 sheet 自动拆成多张数据表。
- 支持 `POST /api/datasets/{dataset_id}/tables` 向已有数据集追加 CSV 或 Excel 表。
- 支持重命名和删除数据集，也支持删除数据集中的单张数据表。

## 阶段 3：基础 SQL 查询工具

- 引入 DuckDB，将数据集中的所有表注册为可查询 SQL 表。
- 实现 `SQLService`，支持对指定 `dataset_id` 执行 SQL 查询。
- 多表数据集支持 `JOIN`、子查询和聚合；单表数据集继续兼容 `data_table` 表名。
- 实现 SQL 安全校验：只允许 `SELECT` / `WITH`，禁止写入、删表、读本地文件等危险操作。
- 实现 LangChain `query_data` StructuredTool。
- 实现 `POST /api/chat`，让模型根据数据集 schema 生成 SQL、调用工具，并用中文解释查询结果。
- 前端增加数据问答输入框，上传数据后可以直接提问。

## 阶段 4：LangGraph Agent

- 定义 `AgentState`，保存消息、数据集 ID、用户问题、schema、工具结果和最终回答。
- 创建 `agent_node`，负责调用绑定工具后的聊天模型。
- 使用 LangGraph `ToolNode` 执行 LangChain 工具。
- 实现 `should_continue` 条件边：有工具调用进入 `tools`，否则进入 `finish`。
- 完成 `agent -> tools -> agent -> finish` 闭环，让工具结果回传给模型生成最终回答。
- 新增 SSE 流式接口 `POST /api/chat/stream`，前端使用事件流展示工具调用和最终回答。
- 工具调用开始、工具返回结果、回答文本增量都会在对话框中按顺序展示。

## 阶段 5：图表生成

- 实现 `generate_chart` LangChain Tool。
- 支持 `bar`、`line`、`pie`、`scatter` 四类图表。
- 图表基于 SQL 查询结果生成，避免凭空绘图。
- 图表文件保存到 `app/storage/charts/`，文件名使用 `chart_id`。
- 通过 `/charts/{chart_id}.png` 访问图表图片。
- SSE 新增 `chart` 事件，前端收到后在对话框中展示图表。

## 阶段 6：数据概览和分析

- 实现 `profile_data` 工具，生成整体数据质量报告；多表数据集会按表分别输出。
- 实现缺失值分析，返回每个字段缺失数量和缺失率。
- 实现描述性统计，覆盖数值字段和非数值字段。
- 实现相关性分析，输出相关系数矩阵和强相关字段对。
- 实现异常值检测，使用 IQR 方法统计数值字段异常值数量和比例。
- 用户输入“帮我做一个数据质量分析”时，Agent 会调用分析工具并总结字段类型、缺失值、异常值等结果。

## 对话和数据集管理

- 上传的数据集会保存到 `app/storage/datasets/`，并写入本地 metadata。
- 服务重启后会自动恢复本地已保存的数据集列表，不需要每次重新上传。
- 一个数据集可以包含多张表，前端支持查看表列表和向当前数据集追加表。
- 前端支持一次选择多个文件创建数据集、重命名数据集、删除数据集、删除数据表。
- 每个对话会保存为 `app/storage/conversations/{conversation_id}.json`。
- 每个对话绑定一个固定 `dataset_id`。
- 前端支持选择本地数据集、新建对话、切换历史对话和恢复对话内容。
- 流式聊天时会保存用户消息、工具调用、工具结果、图表事件和 Agent 回答。

## 环境

本项目推荐使用独立命名 conda 环境，避免把依赖安装到 base 环境：

```powershell
conda create -n data-analyse-agent python=3.11 -y
conda activate data-analyse-agent
python -m pip install -r requirements-dev.txt
```

复制 `.env.example` 为 `.env`，并配置模型：

```powershell
copy .env.example .env
```

```env
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
# 不填或填 0 表示不限制上传大小
DATA_ANALYSE_MAX_UPLOAD_MB=
```

验证：

```powershell
python -m pytest
data-analyse-agent doctor
```

启动后端和前端：

```powershell
python run.py
```

浏览器访问：

```text
http://127.0.0.1:8000
```

上传接口：

```text
GET  http://127.0.0.1:8000/api/datasets
GET  http://127.0.0.1:8000/api/datasets/{dataset_id}
POST http://127.0.0.1:8000/api/upload
POST http://127.0.0.1:8000/api/datasets/{dataset_id}/tables
PATCH  http://127.0.0.1:8000/api/datasets/{dataset_id}
DELETE http://127.0.0.1:8000/api/datasets/{dataset_id}
DELETE http://127.0.0.1:8000/api/datasets/{dataset_id}/tables/{table_name}
```

聊天查询接口：

```text
GET   http://127.0.0.1:8000/api/conversations
POST  http://127.0.0.1:8000/api/conversations
GET   http://127.0.0.1:8000/api/conversations/{conversation_id}
PATCH http://127.0.0.1:8000/api/conversations/{conversation_id}/dataset
POST http://127.0.0.1:8000/api/chat
POST http://127.0.0.1:8000/api/chat/stream
```

请求示例：

```json
{
  "dataset_id": "上传接口返回的 dataset_id",
  "message": "统计每个类别的销售额总和。"
}
```

流式接口返回 `text/event-stream`，事件类型包括：

```text
status
tool_start
tool_end
chart
text_delta
error
done
```

## 目录结构

```text
.
├── app/
│   ├── agent/
│   │   ├── graph.py
│   │   ├── models.py
│   │   ├── prompts.py
│   │   ├── runtime.py
│   │   ├── state.py
│   │   └── tools.py
│   ├── api/
│   │   ├── chat.py
│   │   ├── conversations.py
│   │   └── upload.py
│   ├── services/
│   │   ├── analysis_service.py
│   │   ├── chart_service.py
│   │   ├── conversation_service.py
│   │   ├── dataset_service.py
│   │   └── sql_service.py
│   ├── storage/
│   │   ├── charts/
│   │   ├── conversations/
│   │   └── datasets/
│   └── main.py
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── src/
│   └── data_analyse_agent/
│       ├── __init__.py
│       ├── cli.py
│       └── config.py
├── tests/
│   └── test_smoke.py
├── .env.example
├── .gitignore
├── pyproject.toml
├── run.py
├── requirements.txt
└── requirements-dev.txt
```
