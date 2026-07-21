# Baseline v1 实验协议

## 1. 协议身份

- 协议版本：`baseline-protocol-v1`
- 系统版本：`baseline-v1`
- 冻结日期：`2026-07-21`（Asia/Shanghai）
- Git 分支：`main`
- 基础提交：`0e28896664da4847d98262175bd32545a85f0345`
- 机器可读配置：`evals/configs/baseline_v1.json`
- 冻结工作区快照：`evals/snapshots/baseline_v1/worktree_changes.zip`

本协议只描述并评估冻结的 `baseline-v1`。任何改变模型、温度、API 地址、Prompt
文本、工具策略、计划步数上限或工具轮数上限的实验，都必须使用新的系统或组件版本号，
并写入新的配置文件；不得覆盖本协议、基线配置、原始运行轨迹或基线报告。

## 2. 研究范围

### 2.1 当前基线

流式主链路固定为：

```text
ScopeRouter
  -> 一次性 Planner（最多生成 5 个步骤）
  -> 按步骤限制可见工具
  -> 工具执行与步骤内重试
  -> 最终答案汇总
```

基线通过 `/api/chat/stream` 对应的 `stream_data_agent_events` 执行。非流式
`/api/chat` 保留的旧 LangGraph 全工具循环不属于本实验基线，不能与流式结果混合统计。

### 2.2 主任务

主任务是**有确定标准答案的多表数据分析问题**，包括多表 JOIN、筛选、分组、聚合、
排序、Top-K、时间统计和可确定评分的图表数据生成任务。

每个评测样例必须提供：

- 唯一 `case_id`；
- 用户问题；
- 标准答案或可重复执行的 Oracle；
- 所需数据集版本与表集合；
- 允许的数值误差；
- 必需工具与禁止工具（如适用）。

### 2.3 明确排除

`baseline-v1` 暂不评价开放式报告、主观洞察质量、文风、报告完整性或“是否有启发”等
无法确定性复核的目标。开放式报告结果不得计入 Answer Accuracy。

## 3. 冻结配置

| 配置项 | 冻结值 |
|---|---|
| system version | `baseline-v1` |
| ScopeRouter Prompt | `scope-v1` |
| Planner Prompt | `planner-v1` |
| System/Tool Prompt | `system-tool-v1` |
| 模型 | `LongCat-2.0` |
| API 协议 | OpenAI-compatible |
| API 地址 | `https://api.longcat.chat/openai/v1` |
| temperature | `0.1` |
| streaming | `true` |
| 最大计划步骤 | `5` |
| 每个计划步骤最大工具轮数 | `6` |
| 旧非流式全局工具轮数 | `20`（不属于主评测链路） |

温度使用当前代码的真实值 `0.1`，而不是示例值 `0`。如果后续实验改为 `0`，必须新建
至少一个配置版本，例如 `baseline-v2`，不得修改 `baseline_v1.json`。

API Key 不属于可公开实验配置，不得出现在配置、Trace、报告或快照元数据中。

## 4. Prompt 版本与完整性

版本哈希统一使用 UTF-8 文本的 SHA-256：

| 组件 | 版本 | SHA-256 |
|---|---|---|
| Planner system prompt | `planner-v1` | `bdea4267ee88e898888f3416fa53ce2237e62a8d52bad3aecdc1d7a533139cba` |
| ScopeRouter system prompt | `scope-v1` | `42ec1cf10d797d897170aefebb1c30e5c38b5fc9afe52f8daa33206ff79e1b22` |
| Agent system/tool prompt | `system-tool-v1` | `d184403afc28f30f501bfebc0a5e5a9cad393d319b9e09650dcf8cee17aa30a9` |

Prompt 只要发生任何字符级变化，就必须：

1. 创建新的 Prompt 版本号；
2. 创建新的实验配置文件；
3. 重新计算 SHA-256；
4. 保留旧配置、旧 Trace 和旧报告；
5. 在结果表中把两个版本视为不同方法。

## 5. 指标定义

令评测集合包含 `N` 个样例。无标准答案、Oracle 失败或运行轨迹缺失的样例不得静默
计为正确，应标记为无效样例并单独报告数量。

### 5.1 Answer Accuracy

衡量最终答案是否与预先冻结的标准答案一致：

```text
Answer Accuracy = 正确样例数 / 有效评测样例数
```

评分规则按答案类型确定：

- 数值：满足样例指定的绝对误差或相对误差；
- 文本类别：规范化大小写、首尾空白后精确匹配允许集合；
- 表格：字段集合一致，按指定键排序或按无序集合比较，数值列使用指定容差；
- 图表：只评分生成图表所依据的结构化数据和指定图表类型，不主观评价美观程度。

工具执行成功但数值、口径、范围、JOIN 或最终汇总错误时，该样例计为错误。

### 5.2 Execution Success Rate

```text
Execution Success Rate = 满足执行成功条件的样例数 / 有效评测样例数
```

一个样例仅在以下条件全部满足时计为执行成功：

1. 流程产生 `done`，且没有未处理的顶层 `error`；
2. 至少一个任务所需工具成功结束；
3. 没有超过重试上限后仍未修复的关键工具错误；
4. 需要 Artifact 或图表的任务确实生成对应产物；
5. 最终回答非空。

Execution Success 不代表 Answer Accuracy，二者必须分别报告。

### 5.3 Tool Selection Accuracy

每个样例预先声明 `required_tools` 和 `forbidden_tools`：

```text
case_tool_correct =
    required_tools 是实际调用工具集合的子集
    且 forbidden_tools 与实际调用工具集合交集为空

Tool Selection Accuracy = tool_correct 样例数 / 含工具标注的样例数
```

同一工具重复调用不会提高该指标。需要多个工具的样例必须满足所有必需工具要求。

### 5.4 Average Tool Calls

一次实际发出的工具调用记为一次，包括失败调用和重试：

```text
Average Tool Calls = 所有有效样例的工具调用总数 / 有效样例数
```

ScopeRouter、Planner、调用理由生成和最终汇总属于模型调用，不属于工具调用。

### 5.5 Average Latency

单样例延迟从服务端开始处理评测请求时计时，到收到 `done` 或最终 `error` 时结束：

```text
latency_i = finished_at_i - started_at_i
Average Latency = sum(latency_i) / 有效样例数
```

单位统一为毫秒，同时报告中位数和 P95。超时任务使用实际超时时间计入延迟，并单独报告
超时率。数据集首次上传和关系配置时间不计入问答延迟。

### 5.6 Token Usage

分别累计一条任务链路中所有模型调用返回的 token usage：

```text
input_tokens_i  = sum(该样例所有模型调用的 prompt/input tokens)
output_tokens_i = sum(该样例所有模型调用的 completion/output tokens)
total_tokens_i  = input_tokens_i + output_tokens_i
```

报告总量、单样例均值和中位数。若 API 未返回真实 usage，该字段必须为 `null` 并标记
`token_source=unavailable`；估算 token 不得冒充 API 实测 token。

### 5.7 Estimated Cost

价格必须来自与实验日期、模型和 API 服务商绑定的冻结价目表：

```text
Estimated Cost_i =
    input_tokens_i  / 1,000,000 * input_price_per_million
  + output_tokens_i / 1,000,000 * output_price_per_million
```

单位和币种必须随结果保存。当前仓库没有经过核验的 LongCat-2.0 冻结价格，因此
`baseline_v1.json` 中价格为 `null`。在价格和真实 token usage 任一缺失时，Estimated Cost
必须报告为 `null`，不得报告为 0。补充价目表属于新增实验元数据，必须记录来源、查询日期
和价格版本。

## 6. 报告规则

结果报告只使用数值和可复核分类，不使用“效果很好”“基本正确”“分析合理”“比较智能”
等主观描述替代指标。失败样例必须保留，不得因结果不理想而从分母中删除。

至少报告：

- 样例总数、有效数、无效数和超时数；
- 七项主要指标；
- 按难度和任务类型分组的 Answer Accuracy；
- Answer 错误但 Execution 成功的样例数；
- 完整配置版本、Prompt 版本和 Git 快照标识。

## 7. 版本和快照策略

- `baseline_v1.json` 为只增不改的冻结配置。
- 后续修改使用 `baseline_v2.json`、`planner-v2`、`scope-v2` 等新名称。
- 每次正式实验创建新的 `experiment_id`，原始 Trace 不允许覆盖。
- 基础提交固定为 `0e28896664da4847d98262175bd32545a85f0345`。
- 冻结时的 18 个已修改/未跟踪状态项已完整复制到独立 ZIP 快照。
- 快照 SHA-256：`0a0aeb7c25b3e1e91f69cd58ee8a4409780cc4652a431ad6769e4a238be2c22c`。

快照包含冻结时的已修改和未跟踪工作区内容，不包含 `.git`、被 Git 忽略的 `.env`、API
Key、缓存和运行期存储目录。恢复时先检出基础提交，再解压工作区快照到仓库根目录。

