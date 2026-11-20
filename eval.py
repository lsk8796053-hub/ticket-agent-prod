import json
import sys
from datetime import datetime
from db import get_conn, init_db
from agent import build_system_prompt, run_agent
from llm_gateway import STRONG_MODEL
from tracer import calc_cost

PASS_THRESHOLD = 0.80  # 上线门禁：通过率低于 80% 禁止部署

# 10 条覆盖路由、业务规则、边界兜底的标准评测集
EVAL_CASES = [
    {
        "id": "E01",
        "name": "简单查单-走便宜模型",
        "input": "帮我查下 ORD-001",
        "expected_tier": "便宜模型",
        "expected_tools": ["get_order"],
        "must_contain_any": ["空调", "199", "待服务"],
    },
    {
        "id": "E02",
        "name": "查询不存在订单-不编造",
        "input": "查一下 ORD-999 的状态",
        "expected_tier": "便宜模型",
        "expected_tools": ["get_order"],
        "must_contain_any": ["找不到", "不存在", "未查到", "没有"],
    },
    {
        "id": "E03",
        "name": "常规咨询-不乱调工具",
        "input": "你好，请问你能帮我做什么？",
        "expected_tier": "便宜模型",
        "expected_tools": [],
        "must_contain_any": ["订单", "改期", "取消", "查询", "工单"],
    },
    {
        "id": "E04",
        "name": "待服务订单正常取消",
        "input": "帮我取消 ORD-001，临时出差不在家",
        "expected_tier": "强模型",
        "expected_tools": ["cancel_order"],
        "must_contain_any": ["已取消", "199", "退款"],
    },
    {
        "id": "E05",
        "name": "已完成订单投诉-转人工工单",
        "input": "帮我取消 ORD-002，修完水管还在漏水我要投诉",
        "expected_tier": "强模型",
        "expected_tools": ["create_ticket"],
        "must_contain_any": ["TKT-", "工单"],
    },
    {
        "id": "E06",
        "name": "已取消订单防重复取消",
        "input": "把 ORD-004 帮我取消一下",
        "expected_tier": "强模型",
        "expected_tools": [],  # 查单直接识别或调取消被拦截均可，主要验收结果
        "must_contain_any": ["已取消", "已经是取消", "无需", "重复"],
    },
    {
        "id": "E07",
        "name": "自然语言正常改期",
        "input": "把 ORD-005 改期到后天上午10点",
        "expected_tier": "强模型",
        "expected_tools": ["reschedule_order"],
        "must_contain_any": ["改期成功", "10:00", "已调整", "修改"],
    },
    {
        "id": "E08",
        "name": "2小时内改期并要求转人工",
        "input": "帮我把 ORD-003 改到明天下午3点，改不了就帮我建工单转人工",
        "expected_tier": "强模型",
        "expected_tools": ["create_ticket"],
        "must_contain_any": ["TKT-", "工单"],
    },
    {
        "id": "E09",
        "name": "2小时内改期-规则拦截提示",
        "input": "帮我把 ORD-003 改到后天下午2点",
        "expected_tier": "强模型",
        "expected_tools": [],  # 先查单算时间或直接调改期被拦均可
        "must_contain_any": ["2 小时", "2小时", "不足", "无法自助改期", "人工"],
    },
    {
        "id": "E10",
        "name": "无订单号纯投诉-创建工单",
        "input": "你们平台一直没人接电话，我要投诉，直接帮我建个工单",
        "expected_tier": "强模型",
        "expected_tools": ["create_ticket"],
        "must_contain_any": ["TKT-", "工单"],
    },
]


def reset_business_db():
    """清空业务表并重新灌入初始 5 条订单，保证每次评测环境 100% 一致"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM tickets")
        conn.commit()
    finally:
        conn.close()
    init_db()


def evaluate_case(case: dict) -> dict:
    # 每条用例独立会话上下文，避免串台
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": case["input"]},
    ]
    res = run_agent(messages, case["input"], user_id="eval_bot")

    actual_tools = [s["name"] for s in res["steps"] if s["step_type"] == "tool"]
    reply = res["final_reply"]

    # 1. 检查路由档位
    route_ok = res["route_tier"] == case["expected_tier"]

    # 2. 检查工具调用（如果是闲聊题，要求严格不调任何工具；否则要求命中期望工具）
    if case["id"] == "E03":
        tool_ok = len(actual_tools) == 0
    elif case["expected_tools"]:
        tool_ok = all(t in actual_tools for t in case["expected_tools"])
    else:
        tool_ok = True

    # 3. 检查最终回复是否包含正确业务结论
    content_ok = any(kw in reply for kw in case["must_contain_any"])

    passed = route_ok and tool_ok and content_ok and res["status"] == "success"

    # 估算如果全部无脑走强模型需要花多少钱，用于计算路由省钱比例
    all_strong_cost = calc_cost(STRONG_MODEL, res["prompt_tokens"], res["completion_tokens"])

    reasons = []
    if not route_ok:
        reasons.append(f"路由不符(期望:{case['expected_tier']}, 实际:{res['route_tier']})")
    if not tool_ok:
        reasons.append(f"工具不符(期望:{case['expected_tools']}, 实际:{actual_tools})")
    if not content_ok:
        reasons.append(f"回复未命中关键词{case['must_contain_any']}")
    if res["status"] != "success":
        reasons.append(f"执行状态异常({res['status']})")

    return {
        "id": case["id"],
        "name": case["name"],
        "passed": passed,
        "route_ok": route_ok,
        "tool_ok": tool_ok,
        "content_ok": content_ok,
        "actual_tier": res["route_tier"],
        "actual_tools": actual_tools,
        "latency_ms": res["total_latency_ms"],
        "cost": res["total_cost"],
        "all_strong_cost": all_strong_cost,
        "fail_reasons": reasons,
        "run_id": res["run_id"],
    }


def main():
    print("=" * 65)
    print(f"开始执行部署前自动评测集（共 {len(EVAL_CASES)} 条用例）...")
    print("=" * 65)

    reset_business_db()
    results = []

    for case in EVAL_CASES:
        print(f"\n>>> 运行 [{case['id']}] {case['name']}")
        r = evaluate_case(case)
        results.append(r)
        tag = "PASS" if r["passed"] else "FAIL"
        print(
            f"<<< 结果: [{tag}] | 耗时: {r['latency_ms']}ms | 费用: ¥{r['cost']:.5f} "
            f"| 工具: {r['actual_tools']}"
        )
        if r["fail_reasons"]:
            print(f"    失败原因: {'; '.join(r['fail_reasons'])}")

    # 评测结束后恢复一份干净的业务数据，方便日常演示
    reset_business_db()

    total = len(results)
    passed_cnt = sum(1 for r in results if r["passed"])
    route_ok_cnt = sum(1 for r in results if r["route_ok"])
    tool_ok_cnt = sum(1 for r in results if r["tool_ok"])

    pass_rate = passed_cnt / total
    route_acc = route_ok_cnt / total
    tool_acc = tool_ok_cnt / total

    latencies = sorted(r["latency_ms"] for r in results)
    avg_latency = int(sum(latencies) / total)
    p95_latency = latencies[min(int(total * 0.95), total - 1)]

    total_cost = round(sum(r["cost"] for r in results), 6)
    baseline_strong_cost = round(sum(r["all_strong_cost"] for r in results), 6)
    cost_saved_pct = (
        (1 - total_cost / baseline_strong_cost) * 100
        if baseline_strong_cost > 0
        else 0.0
    )

    summary = {
        "evaluated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_cases": total,
        "passed_cases": passed_cnt,
        "pass_rate_pct": round(pass_rate * 100, 1),
        "route_accuracy_pct": round(route_acc * 100, 1),
        "tool_accuracy_pct": round(tool_acc * 100, 1),
        "avg_latency_ms": avg_latency,
        "p95_latency_ms": p95_latency,
        "total_cost_cny": total_cost,
        "all_strong_baseline_cost_cny": baseline_strong_cost,
        "routing_cost_saved_pct": round(cost_saved_pct, 1),
        "gate_passed": pass_rate >= PASS_THRESHOLD,
        "details": results,
    }

    with open("eval_report.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 65)
    print("评测报告汇总（已保存至 eval_report.json）")
    print("=" * 65)
    print(f"综合通过率（准确率） : {passed_cnt}/{total} ({summary['pass_rate_pct']}%)")
    print(f"模型路由准确率       : {summary['route_accuracy_pct']}%")
    print(f"工具调用准确率       : {summary['tool_accuracy_pct']}%")
    print(f"平均端到端耗时       : {avg_latency} ms（P95: {p95_latency} ms）")
    print(f"本次评测总费用       : ¥{total_cost:.5f}（全用强模型预估 ¥{baseline_strong_cost:.5f}）")
    print(f"模型路由预估节省成本 : {summary['routing_cost_saved_pct']}%")
    print("=" * 65)

    if not summary["gate_passed"]:
        print(f"部署门禁未通过：当前通过率 {summary['pass_rate_pct']}% 低于阈值 {PASS_THRESHOLD * 100:.0f}%！")
        sys.exit(1)

    print("部署门禁通过：所有核心指标达标，允许发布上线！")


if __name__ == "__main__":
    main()