# 生产级智能工单处理 Agent 系统（ticket-agent-prod）

**在线演示**：[点击体验工单 Agent 与监控面板](https://ticket-agent-app-6lnv339bg2fuqw4dcdnxp5.streamlit.app/

基于 Python + FastAPI + SQLite + OpenAI Function Calling + Streamlit 构建的生产级工单处理 Agent，聚焦大模型从原型走向生产环境的核心工程能力：真实业务状态变更、模型路由降本、多级容错兜底、全链路可观测性与部署前自动化评测。

## 核心工程能力

1. **真实业务接口与规则硬校验（FastAPI + SQLite）**
   - 实现订单查询、取消退款、预约改期、人工工单创建 4 类业务接口。
   - 在业务后端强制执行状态与时间规则校验（已完成订单禁取消、距服务不足 2 小时禁自助改期），防止模型幻觉导致越权或违规写库。
2. **模型路由与多级容错网关（`llm_gateway.py`）**
   - 简单查询分流至轻量模型，改期/取消/售后投诉等复杂决策分流至强模型。
   - 内置指数退避重试、主备模型自动切换、单轮粘性切换（避免坏模型在多步循环中重复重试）及终极优雅降级，异常时用户侧零报错。
3. **成本追踪与全链路追踪面板（`tracer.py` + `dashboard.py`）**
   - 自动落库每次请求的 Token 消耗、费用、端到端耗时及每步 LLM/工具入参出参。
   - 支持按用户、功能模块、模型三个维度核算成本，并提供 Trace Explorer 轨迹回放。
4. **部署前自动化评测门禁（`eval.py`）**
   - 内置 10 条覆盖正常流转与边界拦截的评测集，运行前自动重置数据库隔离状态，设定 80% 通过率上线门禁。

## 自动化评测结果

| 评测指标 | 实测结果 |
| :--- | :--- |
| 综合通过率（准确率） | 10/10 (100.0%) |
| 模型路由准确率 | 100.0% |
| 工具调用准确率 | 100.0% |
| 平均端到端耗时 / P95 | 7366 ms / 12289 ms |
| 评测总费用（较全量强模型） | ¥1.65437（节省 17.6%） |

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 复制并配置 .env（OPENAI_API_KEY / OPENAI_BASE_URL / FAST_MODEL / STRONG_MODEL / FALLBACK_MODEL）
streamlit run dashboard.py