# Dataset Failure Memory V1 设计

## 1. 背景

当前 Agent 已经具备 ScopeRouter、结构化 Planner、步骤级 allowed_tools、工具重试和 Artifact Store。  
但在 SQL 查询类任务中，仍可能出现同一个数据集上反复犯同类错误的问题，例如：

- 把不存在的字段写进 SQL：`no such column: order_date`
- 把自然语言里的业务名误当成字段：`customer_name`、`total_amount`
- 多表场景下没有正确 JOIN，或 JOIN 键写错
- 字段存在于另一张表，但模型在当前表里直接引用
- 字段名歧义：`ambiguous column name`

现有重试机制只能把失败结果放回上下文，让模型下一轮尝试修正；但一旦进入新的用户问题或新的会话，这些“错误经验”不会被长期复用。

V1 的目标不是做泛泛的 reflection，而是做 **dataset 级失败经验记忆**：

```text
工具调用失败
  -> 分析失败模式
  -> 重试修复
  -> 修复成功后写入数据集记忆
  -> 同一数据集后续问题自动注入相关经验
```

## 2. V1 目标

第一版只覆盖 SQL 类错误，重点解决 `query_data` 和 `generate_chart` 中 SQL 字段/表名/JOIN 错误反复出现的问题。

### 2.1 必须实现

- 捕获 `query_data`、`generate_chart`、`python_analysis` 参数中的 SQL 执行失败。
- 解析明确的 SQLite 错误模式：
  - `no such column`
  - `no such table`
  - `ambiguous column name`
  - `no such function`
- 在工具失败后生成结构化 failure reflection，用于指导本 step 的下一次 retry。
- 在后续 retry 成功后，把“失败模式 -> 成功修复经验”写入当前数据集的 `tool_memory.json`。
- 下次同一数据集、同一 schema 指纹下执行计划步骤前，注入最多 5 条相关经验。
- 前端 SSE 增加 memory 相关事件，展示“已应用历史经验”或“已记录本数据集经验”。
- 增加单元测试，覆盖错误解析、写入、读取、schema 指纹失效和 prompt 注入。

### 2.2 暂不实现

- 不做向量化 memory 检索。
- 不做跨数据集共享记忆。
- 不做长期用户偏好记忆。
- 不自动修改 schema 或表关系配置。
- 不把所有工具失败都写入记忆。
- 不在失败尚未被成功修复前写入 confirmed memory。

## 3. 核心原则

### 3.1 只记“已被成功修复”的经验

单次失败只能生成 retry hint，不直接永久写入 memory。  
只有满足以下条件才写入 confirmed memory：

```text
同一 step 中：
  上一次 SQL 失败
  -> 后续 retry 使用同一工具或同类 SQL 工具
  -> 工具成功返回
  -> 系统能关联失败 SQL 和成功 SQL
```

这样可以避免把模型的错误猜测写成长期记忆。

### 3.2 记忆必须绑定 schema 指纹

数据集被清洗、追加表或重建 SQLite 后，字段和表结构可能变化。  
所以每条记忆必须包含：

```text
dataset_id
schema_fingerprint
```

只有当前 schema 指纹一致时才注入。  
schema 指纹可由 `dataset_service.get_schema(dataset_id)` 的规范化文本 hash 得到。

### 3.3 注入的是经验摘要，不是完整报错

不要把完整工具结果、完整 SQL、完整 traceback 长期塞进上下文。  
注入内容应当短、明确、可操作，例如：

```text
- orders 表没有 order_date 字段；订单时间应使用 orders.order_purchase_timestamp。
- customers 表没有 customer_name 字段；客户标识可使用 customer_id 或 customer_unique_id。
```

## 4. 数据模型

新增文件：

```text
app/services/tool_memory_service.py
```

建议模型：

```python
class ToolFailureReflection(BaseModel):
    tool_name: str
    failure_type: Literal[
        "sql_column_mismatch",
        "sql_table_mismatch",
        "sql_ambiguous_column",
        "sql_function_mismatch",
        "sql_join_error",
        "unknown"
    ]
    failed_sql: str | None = None
    failed_identifier: str | None = None
    error_message: str
    retry_hint: str
    should_retry: bool = True
    should_remember_after_success: bool = True
```

```python
class ToolMemory(BaseModel):
    memory_id: str
    dataset_id: str
    schema_fingerprint: str
    tool_name: str
    memory_type: str
    failed_pattern: dict
    confirmed_fix: dict
    lesson: str
    created_at: str
    updated_at: str
    hit_count: int = 0
```

```python
class ToolMemoryStore(BaseModel):
    version: int = 1
    memories: list[ToolMemory] = []
```

## 5. 存储位置

每个数据集独立存储：

```text
app/storage/datasets/{dataset_id}/tool_memory.json
```

示例：

```json
{
  "version": 1,
  "memories": [
    {
      "memory_id": "mem_20260713_001",
      "dataset_id": "dataset_xxx",
      "schema_fingerprint": "sha256:...",
      "tool_name": "query_data",
      "memory_type": "sql_column_mismatch",
      "failed_pattern": {
        "wrong_column": "order_date",
        "failed_sql": "SELECT order_date, COUNT(*) FROM orders GROUP BY order_date",
        "error": "no such column: order_date"
      },
      "confirmed_fix": {
        "correct_columns": ["orders.order_purchase_timestamp"],
        "successful_sql": "SELECT date(order_purchase_timestamp) AS order_date, COUNT(*) FROM orders GROUP BY date(order_purchase_timestamp)"
      },
      "lesson": "orders 表没有 order_date 字段；订单时间应使用 orders.order_purchase_timestamp，可用 date(...) 提取日期。",
      "created_at": "2026-07-13T03:00:00Z",
      "updated_at": "2026-07-13T03:00:00Z",
      "hit_count": 0
    }
  ]
}
```

## 6. 错误解析规则

V1 先用规则解析，LLM reflection 可作为 V2。

```text
no such column: X
  -> sql_column_mismatch
  -> failed_identifier = X

no such table: T
  -> sql_table_mismatch
  -> failed_identifier = T

ambiguous column name: X
  -> sql_ambiguous_column
  -> failed_identifier = X

no such function: F
  -> sql_function_mismatch
  -> failed_identifier = F
```

解析来源：

- 工具返回文本中的异常信息
- `tool_args["sql"]`
- 当前 step goal
- 当前 schema text

V1 不要求自动找到唯一正确字段，只生成 retry hint：

```text
字段 X 不存在。请重新查看 schema 中的真实字段名，不要根据自然语言臆造字段；如果信息位于另一张表，请使用已确认外键进行 JOIN。
```

如果后续 retry 成功，再根据失败 SQL、成功 SQL 和错误字段生成 lesson。

## 7. Runtime 接入点

当前位置：

```text
app/agent/runtime.py
_execute_plan_step(...)
```

现有流程：

```text
model stream
  -> tool_calls
  -> invoke tool
  -> tool_end
  -> ToolMessage(result)
  -> 如果失败，failed_attempts += 1
```

V1 改为：

```text
model stream
  -> tool_calls
  -> invoke tool
  -> tool_end
  -> 如果失败：
       reflect_tool_failure(...)
       记录 pending_failure
       ToolMessage(result + retry_hint)
       yield memory_reflection
  -> 如果成功：
       如果存在 pending_failure：
          write_confirmed_memory(...)
          yield memory_write
       ToolMessage(result)
```

### 7.1 pending failure

在 `_execute_plan_step` 内维护：

```python
pending_failures: list[PendingToolFailure] = []
```

其中保存：

```text
tool_name
tool_args
result
reflection
created_at_round
```

当同一步后续工具成功时，尝试用最近一条 pending failure 生成 confirmed memory。

### 7.2 注入 memory

在每个 step 的 `_build_step_instruction(...)` 后追加：

```text
本数据集历史工具经验：
- ...
- ...
```

建议新增：

```python
memory_context = tool_memory_service.build_prompt_context(
    dataset_id=dataset_id,
    schema_text=schema_text,
    tool_names=step.allowed_tools,
    user_question=message,
    limit=5,
)
```

然后传入 `_build_step_instruction(...)`。

## 8. Prompt 注入格式

建议简短、强约束：

```text
本数据集历史工具经验（仅在相关时参考）：
1. orders 表没有 order_date 字段；订单时间应使用 orders.order_purchase_timestamp。
2. customers 表没有 customer_name 字段；客户标识可使用 customers.customer_unique_id。

请优先使用 schema 中存在的真实字段；不要臆造字段名。
```

如果没有可用记忆，不注入该段。

## 9. SSE 事件设计

新增两个事件。

### 9.1 memory_context

在 step start 后、模型执行前发送：

```json
{
  "type": "memory_context",
  "step_id": "step_1",
  "count": 2,
  "memories": [
    {
      "memory_id": "mem_xxx",
      "lesson": "orders 表没有 order_date 字段；订单时间应使用 orders.order_purchase_timestamp。"
    }
  ]
}
```

前端展示：

```text
已应用 2 条本数据集历史经验
```

### 9.2 memory_write

在 retry 成功并写入后发送：

```json
{
  "type": "memory_write",
  "step_id": "step_1",
  "memory": {
    "memory_id": "mem_xxx",
    "memory_type": "sql_column_mismatch",
    "lesson": "orders 表没有 order_date 字段；订单时间应使用 orders.order_purchase_timestamp。"
  }
}
```

前端展示：

```text
已记录本数据集经验：orders 表没有 order_date 字段...
```

## 10. 去重与上限

为避免 memory 膨胀：

- 每个数据集最多保存 100 条 memory。
- 注入时最多取 5 条。
- 同一 `memory_type + failed_identifier + lesson` 视为重复。
- 重复命中时只更新 `updated_at` 和 `hit_count`。
- 如果超过 100 条，优先删除 hit_count 低且 updated_at 最旧的记忆。

## 11. 相关性筛选

V1 不做 embedding。使用规则打分：

```text
+3 lesson 中出现用户问题关键词
+3 failed_identifier 出现在本次 SQL 或用户问题
+2 memory.tool_name 在当前 step.allowed_tools 中
+1 memory_type 是 SQL 类，当前 step intent 为 query/chart/advanced
+1 最近更新
```

得分最高的前 5 条注入。

如果规则实现成本想进一步降低，V1.0 可先只按：

```text
schema_fingerprint 一致
tool_name 在 allowed_tools
updated_at 倒序
limit 5
```

## 12. 测试计划

新增：

```text
tests/test_tool_memory_service.py
tests/test_runtime_tool_memory.py
```

### 12.1 ToolMemoryService 测试

- `no such column` 能解析为 `sql_column_mismatch`
- `no such table` 能解析为 `sql_table_mismatch`
- `ambiguous column name` 能解析为 `sql_ambiguous_column`
- retry 成功后写入 `tool_memory.json`
- schema 指纹不一致时不返回 memory
- 重复 memory 不新增，只更新 hit_count / updated_at
- 超过上限时能裁剪

### 12.2 Runtime 测试

- query_data 首次字段错误、第二次成功后写入 memory
- 同 dataset 同 schema 下一次 step instruction 包含 memory context
- schema 变化后 step instruction 不包含旧 memory
- 失败未修复时不写入 confirmed memory
- SSE 能发出 `memory_context` 和 `memory_write`

## 13. 第一版验收标准

使用一个测试数据集：

```text
orders(order_id, customer_id, order_purchase_timestamp)
customers(customer_id, customer_unique_id)
```

第一次用户问：

```text
按订单日期统计订单数量
```

模型错误生成：

```sql
SELECT order_date, COUNT(*) FROM orders GROUP BY order_date;
```

工具返回：

```text
no such column: order_date
```

系统生成 retry hint，模型改为：

```sql
SELECT date(order_purchase_timestamp) AS order_date, COUNT(*)
FROM orders
GROUP BY date(order_purchase_timestamp);
```

成功后写入 memory：

```text
orders 表没有 order_date 字段；订单时间应使用 orders.order_purchase_timestamp，可用 date(...) 提取日期。
```

下一次同数据集用户问：

```text
画一下每天订单量趋势
```

step instruction 自动注入该经验，模型优先使用 `order_purchase_timestamp`。

## 14. V2 扩展方向

- LLM reflection：从失败 SQL、成功 SQL、schema 中生成更自然的 lesson。
- SQL AST diff：比较失败 SQL 和成功 SQL 的字段、表、JOIN 差异。
- Python sandbox memory：记录 `result.json` 缺失、路径错误、中文字体问题等。
- Memory 评测集：比较有无 memory 时的工具失败率和 retry 次数。
- 可视化 memory 管理：前端允许查看、禁用或删除某条 dataset memory。
