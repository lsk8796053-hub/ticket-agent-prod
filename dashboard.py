import json
import os
import socket
import threading
import time
import streamlit as st

st.set_page_config(page_title="工单 Agent 生产控制台", layout="wide")

# 1. 云端部署兼容：自动读取 Streamlit Cloud Secrets 注入环境变量（本地无此文件时自动跳过）
try:
    for k, v in st.secrets.items():
        if isinstance(v, str) and k not in os.environ:
            os.environ[k] = v
except Exception:
    pass


# 2. 单进程自启业务后端：检测 8000 端口，未启动则在后台线程拉起 FastAPI
@st.cache_resource
def ensure_backend_running():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", 8000)) == 0:
            return True  # 已在外部手动启动，直接复用

    import uvicorn
    from main import app as fastapi_app

    def _run_api():
        config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=8000, log_level="warning")
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # 防止子线程注册信号报错
        server.run()

    threading.Thread(target=_run_api, daemon=True).start()

    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", 8000)) == 0:
                return True
        time.sleep(0.1)
    return False


ensure_backend_running()

from agent import build_system_prompt, run_agent
from tracer import (
    get_overview_metrics,
    get_cost_by_dimension,
    list_recent_runs,
    get_run_steps,
)

st.title("工单处理 Agent · 生产控制台")

# 侧边栏：模拟不同真实用户，方便演示“按用户维度追踪成本”
with st.sidebar:
    st.header("用户会话设置")
    current_user = st.selectbox(
        "当前模拟用户 ID",
        ["user_zhangsan", "user_lisi", "user_wangwu"],
    )
    if st.button("清空当前对话上下文"):
        st.session_state.messages = [{"role": "system", "content": build_system_prompt()}]
        st.session_state.chat_ui = []
        st.rerun()

    st.divider()
    st.caption("测试订单参考：")
    st.caption("- ORD-001：待服务（可查/可改/可取消）")
    st.caption("- ORD-002：已完成（不可取消，需转工单）")
    st.caption("- ORD-003：1小时内待服务（不可自助改期）")
    st.caption("- ORD-005：待服务（可自助改期）")

# 初始化会话状态
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "system", "content": build_system_prompt()}]
if "chat_ui" not in st.session_state:
    st.session_state.chat_ui = []

tab_chat, tab_monitor = st.tabs(["在线工单客服", "成本追踪与全链路监控面板"])

# ==================== Tab 1: 在线对话测试 ====================
with tab_chat:
    for item in st.session_state.chat_ui:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])
            if item.get("meta"):
                m = item["meta"]
                st.caption(
                    f"Run: {m['run_id']} | 功能: {m['feature']} | 模型: {m['used_models']} "
                    f"| 耗时: {m['total_latency_ms']}ms | 费用: ¥{m['total_cost']:.6f}"
                )
                with st.expander("查看本次 Agent 执行轨迹"):
                    for s in m["steps"]:
                        icon = "🧠" if s["step_type"] == "llm" else "🔧"
                        st.markdown(
                            f"**Step {s['step_no']} {icon} `{s['name']}`** "
                            f"（耗时 `{s['latency_ms']}ms` | 状态 `{s['status']}`"
                            + (f" | 费用 `¥{s['cost']:.6f}`" if s["step_type"] == "llm" else "")
                            + "）"
                        )
                        st.json({"input": s["input_payload"], "output": s["output_payload"]})

    user_prompt = st.chat_input("输入客户诉求，例如：帮我查下 ORD-001，或者把 ORD-003 改到明天下午3点...")
    if user_prompt:
        st.session_state.chat_ui.append({"role": "user", "content": user_prompt})
        st.session_state.messages.append({"role": "user", "content": user_prompt})

        with st.chat_message("user"):
            st.markdown(user_prompt)

        with st.chat_message("assistant"):
            with st.spinner("Agent 正在路由模型并调用业务接口..."):
                res = run_agent(
                    st.session_state.messages,
                    user_prompt,
                    user_id=current_user,
                )
            st.markdown(res["final_reply"])
            st.caption(
                f"Run: {res['run_id']} | 功能: {res['feature']} | 模型: {res['used_models']} "
                f"| 耗时: {res['total_latency_ms']}ms | 费用: ¥{res['total_cost']:.6f}"
            )
            st.session_state.chat_ui.append({
                "role": "assistant",
                "content": res["final_reply"],
                "meta": res,
            })
            st.rerun()

# ==================== Tab 2: 成本与全链路追踪面板 ====================
with tab_monitor:
    if st.button("刷新监控数据"):
        st.rerun()

    metrics = get_overview_metrics()
    total_runs = metrics["total_runs"]
    fallback_rate = (metrics["fallback_runs"] / total_runs * 100) if total_runs else 0.0

    # 1. 顶部 4 个核心指标卡
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("累计请求数", f"{total_runs} 次")
    c2.metric("累计 LLM 费用", f"¥{metrics['total_cost']:.5f}")
    c3.metric("平均端到端耗时", f"{int(metrics['avg_latency_ms'])} ms")
    c4.metric("兜底切换触发率", f"{fallback_rate:.1f}%")

    st.divider()

    # 2. 按用户 / 按功能 / 按模型三个维度拆解成本
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.subheader("按用户统计成本")
        st.dataframe(get_cost_by_dimension("user_id"))
    with col_b:
        st.subheader("按功能模块统计成本")
        st.dataframe(get_cost_by_dimension("feature"))
    with col_c:
        st.subheader("按使用模型统计成本")
        st.dataframe(get_cost_by_dimension("used_models"))

    st.divider()

    # 3. 全链路追踪明细浏览器
    st.subheader("全链路追踪（Trace Explorer）")
    recent_runs = list_recent_runs(30)
    if not recent_runs:
        st.info("暂无调用记录，请先在「在线工单客服」里发几条消息。")
    else:
        options = {
            f"{r['created_at']} | {r['run_id']} | {r['user_id']} | [{r['feature']}] {r['user_input'][:22]}": r
            for r in recent_runs
        }
        selected_label = st.selectbox("选择一条历史请求查看每一步详情", list(options.keys()))
        selected_run = options[selected_label]

        st.markdown(
            f"**用户提问**：{selected_run['user_input']}  \n"
            f"**最终回复**：{selected_run['final_reply']}  \n"
            f"**路由档位**：`{selected_run['route_tier']}` | **实际模型**：`{selected_run['used_models']}` "
            f"| **总 Token**：`{selected_run['prompt_tokens']} + {selected_run['completion_tokens']}` "
            f"| **总费用**：`¥{selected_run['total_cost']:.6f}` | **总耗时**：`{selected_run['total_latency_ms']}ms`"
        )

        steps = get_run_steps(selected_run["run_id"])
        for s in steps:
            icon = "🧠 LLM 推理" if s["step_type"] == "llm" else "🔧 工具接口"
            with st.expander(
                f"Step {s['step_no']} · {icon} · {s['name']} · 耗时 {s['latency_ms']}ms · 状态 {s['status']}",
                expanded=True,
            ):
                left, right = st.columns(2)
                with left:
                    st.caption("输入 (Input)")
                    st.json(json.loads(s["input_payload"] or "{}"))
                with right:
                    st.caption("输出 (Output)")
                    st.json(json.loads(s["output_payload"] or "{}"))