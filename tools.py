import json
import httpx

API_BASE_URL = "http://127.0.0.1:8000"

# 1. 给大模型看的“工具说明书”（JSON Schema）
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "根据订单号查询订单的详细信息，包括客户姓名、服务类型、预约时间、金额和当前状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "订单编号，例如 ORD-001",
                    }
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "取消指定订单并触发退款。仅“待服务”状态的订单允许直接取消。",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "订单编号，例如 ORD-001",
                    },
                    "reason": {
                        "type": "string",
                        "description": "用户取消订单的原因",
                    },
                },
                "required": ["order_id", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_order",
            "description": "修改订单的预约服务时间。仅“待服务”且距离原预约时间超过 2 小时的订单允许自助改期。",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "订单编号，例如 ORD-005",
                    },
                    "new_time": {
                        "type": "string",
                        "description": "新的预约时间，必须严格遵循 YYYY-MM-DD HH:MM 格式，例如 2026-11-26 15:00",
                    },
                },
                "required": ["order_id", "new_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_ticket",
            "description": "创建人工升级工单。当用户投诉、或者系统规则不允许自助取消/改期（例如已完成订单、距离服务不足2小时）时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "关联的订单编号，如果没有相关订单可不填",
                    },
                    "issue_type": {
                        "type": "string",
                        "description": "问题类型，例如：紧急改期、售后投诉、特殊退款、其他咨询",
                    },
                    "summary": {
                        "type": "string",
                        "description": "用户诉求和订单情况的简要总结，方便人工客服接手",
                    },
                },
                "required": ["issue_type", "summary"],
            },
        },
    },
]


# 2. 真正发 HTTP 请求去调用第 1 课写好的 FastAPI 业务接口
def execute_tool(tool_name: str, args: dict) -> str:
    try:
        with httpx.Client(base_url=API_BASE_URL, timeout=10.0) as client:
            if tool_name == "get_order":
                resp = client.get(f"/orders/{args['order_id']}")
            elif tool_name == "cancel_order":
                resp = client.post(
                    f"/orders/{args['order_id']}/cancel",
                    json={"reason": args["reason"]},
                )
            elif tool_name == "reschedule_order":
                resp = client.post(
                    f"/orders/{args['order_id']}/reschedule",
                    json={"new_time": args["new_time"]},
                )
            elif tool_name == "create_ticket":
                resp = client.post(
                    "/tickets",
                    json={
                        "order_id": args.get("order_id"),
                        "issue_type": args["issue_type"],
                        "summary": args["summary"],
                    },
                )
            else:
                return json.dumps({"success": False, "message": f"未知工具: {tool_name}"}, ensure_ascii=False)

            return json.dumps(resp.json(), ensure_ascii=False)
    except httpx.RequestError as e:
        return json.dumps(
            {"success": False, "message": f"无法连接业务接口服务，请确认 uvicorn 已启动: {e}"},
            ensure_ascii=False,
        )