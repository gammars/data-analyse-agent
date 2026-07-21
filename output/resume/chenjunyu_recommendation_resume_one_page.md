# 陈俊宇

15396273386 | 811096909@qq.com | 厦门，中国

## 教育背景

**厦门大学（985）** | 计算机科学与技术 本科 | 2023.09 - 2027.06（预计）  
GPA：3.86/4.0；专业排名：3/145（前 3%）；CET-4：608，CET-6：548  
核心课程：数据结构（96）、计算机图形学（93）、面向对象程序设计（93）、概率统计（92）、线性代数（93）、计算机网络（90）、计算机组成原理（90）、算法设计与分析（90）

## 项目经历

**基于证据路由与分层检索的 Agentic RAG 知识库问答系统** | 个人项目 | 2026.03 - 至今

- 面向课程资料、保研经验与科研文档等长文档问答场景，针对普通 RAG 检索覆盖不足、证据碎片化、问题歧义时易强行回答等问题，设计并实现私有知识库问答系统。
- 构建 PDF/Word/Excel/HTML 解析、文本清洗与 L1/L2/L3 三级父子分块链路，采用 L3 叶子块精细召回、L1/L2 父块上下文恢复策略，兼顾检索精度与上下文完整性。
- 基于 Milvus 实现 dense embedding + BM25 混合检索，使用 RRF 融合语义与关键词检索结果，并结合 Auto-merging 与可选 rerank 优化证据排序。
- 基于 LangGraph 构建自适应 RAG 状态图与 evidence routing 机制，支持复杂问题拆解检索、证据去重合成、answer/rewrite/clarify/scope_select/no_knowledge 路由、HITL 中断恢复、SSE 流式输出与 trace 可视化。

**Data Analyse Agent 本地结构化数据分析智能体** | 个人项目 | 2026.06 - 至今

- 针对本地多表数据分析中“大模型不了解表关系、易误用 SQL、复杂统计能力不足、生成代码执行不安全”的问题，设计安全可控、可解释、可评测的数据分析 Agent。
- 实现 FastAPI + LangChain/LangGraph + SQLite + Pandas 架构，将 CSV/Excel 物化为 SQLite 数据集，维护 Manifest、Schema 和表关系，并通过 SQLService 只读 URI、authorizer、单语句校验、超时和行数限制保障查询安全。
- 设计 ScopeRouter + 结构化 Planner，在执行前识别问题是否与数据集相关，并按 query/chart/quality/advanced/cleaning/mixed 动态限制每步可见工具和重试次数，减少无关问题误调用数据库和工具滥用。
- 实现 PythonSandboxService，将 SQL 结果导出为 JSON 后交由 Docker 沙箱执行 Python 分析脚本，支持相关性、异常值、聚类、时间序列和复杂图表；构建自动化评测流水线，完整模式 31/31 通过，单元测试 58 项通过。

**NIPT 胎儿 Y 染色体浓度影响因素识别与个性化检测时点优化** | 国家级一等奖项目 | 2025.09

- 围绕 NIPT 中胎儿 Y 染色体浓度影响因素识别与个性化检测时点优化，负责前两问数学建模、算法设计及论文核心章节撰写。
- 完成男胎 NIPT 数据预处理，包括孕周字符串数值化、BMI 复核、重复检测记录合并、妊娠期代表性 BMI 构造与 Y 染色体浓度 4% 达标时间提取。
- 建立以孕周、BMI 为固定效应、孕妇 ID 为随机截距的线性混合效应模型（LMM），结合 JB 正态性检验、QQ 图、Spearman 相关分析与 ANOVA 显著性检验评估变量影响。
- 设计基于 log-rank 统计量的递归二分 BMI 分组算法，结合 Kaplan-Meier 生存函数、综合风险函数与蒙特卡洛模拟，在 267 名男胎孕妇样本上给出分组 NIPT 检测时点建议并评估误差扰动风险。

## 荣誉奖项

2025.09 全国大学生数学建模竞赛 国家级一等奖 | 2026 芙蓉科创奖学金 | 2025 蓝桥杯省级二等奖 | 2024、2025 学业优秀奖学金 | 2024 三好学生

## 技能

Python、SQL、Pandas、NumPy、Matplotlib、scikit-learn、FastAPI、LangChain、LangGraph、SQLite、Milvus、BM25、dense embedding、RRF、Docker、Git、pytest、SSE、自动化评测
