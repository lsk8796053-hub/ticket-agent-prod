import json
import time
import uuid
from datetime import datetime
from llm_gateway import route_model, call_llm_with_fallback
from tools import TOOLS_SCHEMA, execute_tool
from tracer import init_trace_db, calc_cost, record_run

init_trace_db()


def build_system_prompt() -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""你是一个专业、高效的在线服务平台智能客服与工单处理 Agent。
当前系统时间是：{now_str}。

工作原则：
1. 严禁编造订单信息或处理结果，必须通过调用工具获取真实数据并执行操作。
2. 当用户提到相对时间（如“明天下午3点”“后天上午10点”）时，请根据当前系统时间自行换算为 YYYY-MM-DD HH:MM 格式后再调用工具。
3. 当业务接口因规则拦截操作失败（如距离服务不足 2 小时无法改期、已完成订单无法取消）时，请礼貌向用户说明原因；如果用户明确要求转人工或属于售后投诉，直接调用 create_ticket 创建人工工单并告知工单号。
4. 回答简洁清晰，直接给出处理结果和关键编号。"""


def infer_feature(tools_called: list) -> str:
    """根据本次对话实际调用的工具，自动归类所属功能模块，用于成本核算"""
    if "create_ticket" in tools_called:
        return "人工工单"
    if "cancel_order" in tools_called:
        return "订单取消"
    if "reschedule_order" in tools_called:
        return "订单改期"
    if "get_order" in tools_called:
        return "订单查询"
    return "常规咨询"


def run_agent(
    messages: list,
    user_text: str,
    user_id: str = "cli_user",
    max_steps: int = 6,
) -> dict:
    run_id = f"RUN-{uuid.uuid4().hex[:8].upper()}"
    run_start = time.perf_counter()

    tier, primary_model, reason = route_model(user_text)
    print(f"  [模型路由] 档位={tier} | 选中={primary_model} | 原因={reason}")

    current_model = primary_model  # 支持单轮内粘性切换，避免坏模型每步重复重试
    used_models = []
    tools_called = []
    trace_steps = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cost = 0.0
    any_fallback = False
    status = "success"
    final_reply = ""

    for step in range(1, max_steps + 1):
        result = call_llm_with_fallback(
            messages=messages,
            tools=TOOLS_SCHEMA,
            primary_model=current_model,
        )

        if not result["ok"]:
            any_fallback = True
            status = "degraded"
            trace_steps.append({
                "step_no": step,
                "step_type": "llm",
                "name": current_model,
                "input_payload": {"route_reason": reason},
                "output_payload": {"error": result["error"]},
                "latency_ms": result["latency_ms"],
                "status": "error",
            })
            final_reply = "抱歉，智能客服通道暂时拥挤，请稍后再试；如遇紧急订单问题，可直接致电人工热线 400-800-1234。"
            break

        used_model = result["used_model"]
        if used_model not in used_models:
            used_models.append(used_model)

        # 如果本步触发了切模型，本轮后续步骤直接沿用该可用模型
        if result["fallback_triggered"]:
            any_fallback = True
            current_model = used_model

        p_tok = result["prompt_tokens"]
        c_tok = result["completion_tokens"]
        step_cost = calc_cost(used_model, p_tok, c_tok)
        total_prompt_tokens += p_tok
        total_completion_tokens += c_tok
        total_cost = round(total_cost + step_cost, 6)

        print(
            f"  [Step {step} 网关] 模型={used_model} | 耗时={result['latency_ms']}ms "
            f"| Token={p_tok}+{c_tok} | 费用=¥{step_cost:.6f}"
        )

        msg = result["response"].choices[0].message
        assistant_msg = {"role": "assistant", "content": msg.content or ""}
        tool_call_names = []
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
            tool_call_names = [tc.function.name for tc in msg.tool_calls]
        messages.append(assistant_msg)

        trace_steps.append({
            "step_no": step,
            "step_type": "llm",
            "name": used_model,
            "input_payload": {"messages_count": len(messages) - 1, "retries": result["retries"]},
            "output_payload": {
                "decision": f"调用工具 {tool_call_names}" if tool_call_names else "生成最终回复",
                "content_preview": (msg.content or "")[:120],
            },
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "cost": step_cost,
            "latency_ms": result["latency_ms"],
            "status": "fallback_ok" if result["fallback_triggered"] else "ok",
        })

        if not msg.tool_calls:
            final_reply = msg.content or ""
            break

        for tc in msg.tool_calls:
            tool_name = tc.function.name
            tool_args = json.loads(tc.function.arguments or "{}")
            tools_called.append(tool_name)
            print(f"  [Step {step} 调用工具] {tool_name}({tool_args})")

            t_start = time.perf_counter()
            tool_result = execute_tool(tool_name, tool_args)
            tool_ms = int((time.perf_counter() - t_start) * 1000)
            print(f"  [Step {step} 接口返回] ({tool_ms}ms) {tool_result}")

            try:
                parsed_out = json.loads(tool_result)
            except json.JSONDecodeError:
                parsed_out = {"raw": tool_result}

            trace_steps.append({
                "step_no": step,
                "step_type": "tool",
                "name": tool_name,
                "input_payload": tool_args,
                "output_payload": parsed_out,
                "latency_ms": tool_ms,
                "status": "ok" if parsed_out.get("success") else "biz_rejected",
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_result,
            })
    else:
        status = "max_steps_exceeded"
        final_reply = "抱歉，当前请求处理步骤过多，已为您暂停操作，建议直接联系人工客服协助处理。"

    total_latency_ms = int((time.perf_counter() - run_start) * 1000)
    feature = infer_feature(tools_called)

    # 整轮结束，统一将汇总与步骤轨迹写入 traces.db
    run_record = {
        "run_id": run_id,
        "user_id": user_id,
        "feature": feature,
        "user_input": user_text,
        "final_reply": final_reply,
        "route_tier": tier,
        "primary_model": primary_model,
        "used_models": ",".join(used_models) if used_models else "none",
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_cost": total_cost,
        "total_latency_ms": total_latency_ms,
        "fallback_triggered": any_fallback,
        "status": status,
    }
    record_run(run_record, trace_steps)

    return {
        **run_record,
        "steps": trace_steps,
    }


def main():
    print("工单处理 Agent（路由+兜底+成本追踪版）已启动（输入 exit 退出）\n")
    messages = [{"role": "system", "content": build_system_prompt()}]

    while True:
        user_input = input("用户: ").strip()
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break

        messages.append({"role": "user", "content": user_input})
        res = run_agent(messages, user_input, user_id="cli_user")
        print(
            f"Agent: {res['final_reply']}\n"
            f"  [本次账单] ID={res['run_id']} | 功能={res['feature']} "
            f"| 总耗时={res['total_latency_ms']}ms | 总费用=¥{res['total_cost']:.6f}\n"
        )


if __name__ == "__main__":
    main()