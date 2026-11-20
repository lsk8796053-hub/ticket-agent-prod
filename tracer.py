import json
import sqlite3
from datetime import datetime

TRACE_DB_PATH = "traces.db"

# 模型参考单价：(输入单价, 输出单价)，单位：元 / 百万 Token（可按实际中转站费率微调）
MODEL_PRICING = {
    "gpt-6-sol": (1.0, 2.0),
    "gpt-6.1-sol": (5.0, 15.0),
    "gpt-6.2-sol": (15.0, 45.0),
}
DEFAULT_PRICING = (5.0, 15.0)


def get_trace_conn():
    conn = sqlite3.connect(TRACE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_trace_db():
    conn = get_trace_conn()
    try:
        # 1. 请求总表：记录每一次对话的整体指标（用户、功能、总费用、总耗时）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                feature TEXT NOT NULL,
                user_input TEXT NOT NULL,
                final_reply TEXT NOT NULL,
                route_tier TEXT NOT NULL,
                primary_model TEXT NOT NULL,
                used_models TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                total_cost REAL NOT NULL,
                total_latency_ms INTEGER NOT NULL,
                fallback_triggered INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # 2. 步骤明细表：记录每一次对话里，每一步调了什么模型或工具
        conn.execute("""
            CREATE TABLE IF NOT EXISTS steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                step_no INTEGER NOT NULL,
                step_type TEXT NOT NULL,
                name TEXT NOT NULL,
                input_payload TEXT,
                output_payload TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                cost REAL DEFAULT 0.0,
                latency_ms INTEGER DEFAULT 0,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def calc_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_price, out_price = MODEL_PRICING.get(model_name, DEFAULT_PRICING)
    cost = (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000
    return round(cost, 6)


def record_run(run_data: dict, steps_data: list):
    """一次请求结束后，将汇总数据与所有步骤明细一起落库"""
    init_trace_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_trace_conn()
    try:
        conn.execute(
            """
            INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_data["run_id"],
                run_data["user_id"],
                run_data["feature"],
                run_data["user_input"],
                run_data["final_reply"],
                run_data["route_tier"],
                run_data["primary_model"],
                run_data["used_models"],
                run_data["prompt_tokens"],
                run_data["completion_tokens"],
                run_data["total_cost"],
                run_data["total_latency_ms"],
                1 if run_data["fallback_triggered"] else 0,
                run_data["status"],
                now_str,
            ),
        )

        for s in steps_data:
            conn.execute(
                """
                INSERT INTO steps (
                    run_id, step_no, step_type, name, input_payload, output_payload,
                    prompt_tokens, completion_tokens, cost, latency_ms, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_data["run_id"],
                    s["step_no"],
                    s["step_type"],
                    s["name"],
                    json.dumps(s.get("input_payload"), ensure_ascii=False),
                    json.dumps(s.get("output_payload"), ensure_ascii=False),
                    s.get("prompt_tokens", 0),
                    s.get("completion_tokens", 0),
                    s.get("cost", 0.0),
                    s.get("latency_ms", 0),
                    s.get("status", "ok"),
                    now_str,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_overview_metrics() -> dict:
    init_trace_db()
    conn = get_trace_conn()
    try:
        row = conn.execute("""
            SELECT
                COUNT(*) AS total_runs,
                COALESCE(SUM(total_cost), 0) AS total_cost,
                COALESCE(AVG(total_latency_ms), 0) AS avg_latency_ms,
                COALESCE(SUM(fallback_triggered), 0) AS fallback_runs,
                COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total_tokens
            FROM runs
        """).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_cost_by_dimension(column: str) -> list:
    """按指定维度（user_id / feature / used_models）聚合费用与耗时"""
    if column not in {"user_id", "feature", "used_models"}:
        raise ValueError("不支持的聚合维度")
    init_trace_db()
    conn = get_trace_conn()
    try:
        rows = conn.execute(f"""
            SELECT
                {column} AS 维度,
                COUNT(*) AS 请求次数,
                SUM(prompt_tokens + completion_tokens) AS 总Token,
                ROUND(SUM(total_cost), 6) AS 总费用_元,
                ROUND(AVG(total_latency_ms), 0) AS 平均耗时_ms
            FROM runs
            GROUP BY {column}
            ORDER BY 总费用_元 DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_recent_runs(limit: int = 50) -> list:
    init_trace_db()
    conn = get_trace_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_run_steps(run_id: str) -> list:
    init_trace_db()
    conn = get_trace_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY id ASC", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()