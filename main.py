from datetime import datetime
from typing import Optional
from fastapi import FastAPI
from pydantic import BaseModel
from db import (
    init_db,
    get_order_by_id,
    update_order_status,
    update_order_time,
    create_ticket,
    list_tickets,
)

app = FastAPI(title="工单与预约业务系统")

init_db()


class CancelRequest(BaseModel):
    reason: str


class RescheduleRequest(BaseModel):
    new_time: str  # 格式要求：YYYY-MM-DD HH:MM


class TicketRequest(BaseModel):
    order_id: Optional[str] = None
    issue_type: str
    summary: str


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    order = get_order_by_id(order_id)
    if not order:
        return {"success": False, "message": f"找不到订单 {order_id}"}
    return {"success": True, "data": order}


@app.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str, req: CancelRequest):
    order = get_order_by_id(order_id)
    if not order:
        return {"success": False, "message": f"找不到订单 {order_id}"}

    if order["status"] == "已完成":
        return {
            "success": False,
            "message": "该订单已完成，无法直接取消，请创建售后工单转人工处理",
        }
    if order["status"] == "已取消":
        return {
            "success": False,
            "message": "该订单已经是取消状态，请勿重复操作",
        }

    update_order_status(order_id, "已取消")
    return {
        "success": True,
        "message": f"订单 {order_id} 已取消，退款 {order['amount']} 元将原路退回",
        "reason": req.reason,
    }


@app.post("/orders/{order_id}/reschedule")
def reschedule_order(order_id: str, req: RescheduleRequest):
    order = get_order_by_id(order_id)
    if not order:
        return {"success": False, "message": f"找不到订单 {order_id}"}

    if order["status"] != "待服务":
        return {
            "success": False,
            "message": f"当前订单状态为「{order['status']}」，仅「待服务」订单允许改期",
        }

    # 业务规则：距离原预约时间不足 2 小时禁止自助改期
    current_dt = datetime.strptime(order["scheduled_time"], "%Y-%m-%d %H:%M")
    hours_left = (current_dt - datetime.now()).total_seconds() / 3600
    if hours_left < 2:
        return {
            "success": False,
            "message": "距离服务开始不足 2 小时，无法自助改期，请创建人工工单协调处理",
        }

    # 校验用户传入的新时间格式和合法性
    try:
        new_dt = datetime.strptime(req.new_time, "%Y-%m-%d %H:%M")
    except ValueError:
        return {
            "success": False,
            "message": "时间格式错误，请使用 YYYY-MM-DD HH:MM（例如 2026-11-25 15:00）",
        }

    if new_dt <= datetime.now():
        return {"success": False, "message": "新预约时间必须是未来时间"}

    old_time = order["scheduled_time"]
    update_order_time(order_id, req.new_time)
    return {
        "success": True,
        "message": f"订单 {order_id} 改期成功",
        "old_time": old_time,
        "new_time": req.new_time,
    }


@app.post("/tickets")
def create_manual_ticket(req: TicketRequest):
    ticket = create_ticket(req.order_id, req.issue_type, req.summary)
    return {
        "success": True,
        "message": f"已创建人工工单 {ticket['ticket_id']}，客服将在 2 小时内联系处理",
        "data": ticket,
    }


@app.get("/tickets")
def get_all_tickets():
    return {"success": True, "data": list_tickets()}