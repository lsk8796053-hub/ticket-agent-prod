import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Optional

DB_PATH = "business.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据表，并写入 5 条不同状态的模拟订单"""
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                service_type TEXT NOT NULL,
                scheduled_time TEXT NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id TEXT PRIMARY KEY,
                order_id TEXT,
                issue_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        now = datetime.now()
        seed_orders = [
            ("ORD-001", "张三", "空调深度清洗", (now + timedelta(days=2)).strftime("%Y-%m-%d 14:00"), 199.0, "待服务"),
            ("ORD-002", "李四", "水管漏水维修", (now - timedelta(days=2)).strftime("%Y-%m-%d 10:00"), 150.0, "已完成"),
            ("ORD-003", "王五", "全屋保洁3小时", (now + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"), 240.0, "待服务"),
            ("ORD-004", "赵六", "洗衣机拆洗", (now + timedelta(days=3)).strftime("%Y-%m-%d 09:00"), 168.0, "已取消"),
            ("ORD-005", "钱七", "油烟机清洗", (now + timedelta(days=5)).strftime("%Y-%m-%d 16:00"), 220.0, "待服务"),
        ]

        conn.executemany(
            "INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?, ?)",
            seed_orders,
        )
        conn.commit()
    finally:
        conn.close()


def get_order_by_id(order_id: str):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_order_status(order_id: str, new_status: str):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE orders SET status = ? WHERE order_id = ?",
            (new_status, order_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_order_time(order_id: str, new_time: str):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE orders SET scheduled_time = ? WHERE order_id = ?",
            (new_time, order_id),
        )
        conn.commit()
    finally:
        conn.close()


def create_ticket(order_id: Optional[str], issue_type: str, summary: str):
    ticket_id = f"TKT-{uuid.uuid4().hex[:6].upper()}"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "待人工处理"

    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO tickets VALUES (?, ?, ?, ?, ?, ?)",
            (ticket_id, order_id, issue_type, summary, status, created_at),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "ticket_id": ticket_id,
        "order_id": order_id,
        "issue_type": issue_type,
        "summary": summary,
        "status": status,
        "created_at": created_at,
    }


def list_tickets():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM tickets ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()