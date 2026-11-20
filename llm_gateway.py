import os
import time
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
    timeout=15.0,
)

FAST_MODEL = os.getenv("FAST_MODEL", "gpt-6-sol")
STRONG_MODEL = os.getenv("STRONG_MODEL", "gpt-6.2-sol")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "gpt-6.1-sol")

COMPLEX_KEYWORDS = [
    "取消", "退单", "退款", "改期", "改到", "修改时间",
    "投诉", "人工", "工单", "漏水", "坏了", "赔偿",
    "如果", "否则", "不行就", "顺便",
]


def route_model(user_text: str) -> tuple[str, str, str]:
    hit_keywords = [kw for kw in COMPLEX_KEYWORDS if kw in user_text]
    if hit_keywords:
        return "强模型", STRONG_MODEL, f"命中复杂操作关键词 {hit_keywords}"
    if len(user_text) > 35:
        return "强模型", STRONG_MODEL, f"输入较长({len(user_text)}字)，可能包含多步诉求"
    return "便宜模型", FAST_MODEL, "简单查询或常规咨询"


def call_llm_with_fallback(
    messages: list,
    tools: list,
    primary_model: str,
    max_retries: int = 2,
) -> dict:
    candidates = [primary_model]
    if FALLBACK_MODEL and FALLBACK_MODEL != primary_model:
        candidates.append(FALLBACK_MODEL)

    last_error = None
    total_retries = 0

    for idx, model_name in enumerate(candidates):
        is_fallback = idx > 0
        if is_fallback:
            print(f"  [兜底切换] 主模型连续失败，自动切换备用模型 -> {model_name}")

        attempts = 1 + max_retries if not is_fallback else 2
        for attempt in range(1, attempts + 1):
            start_ts = time.perf_counter()
            try:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                )
                latency_ms = int((time.perf_counter() - start_ts) * 1000)

                # 安全提取 Token 用量（兼容部分不返回 usage 的中转站）
                usage = getattr(response, "usage", None)
                prompt_tokens = (getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
                completion_tokens = (getattr(usage, "completion_tokens", 0) or 0) if usage else 0

                return {
                    "ok": True,
                    "response": response,
                    "used_model": model_name,
                    "fallback_triggered": is_fallback,
                    "retries": total_retries,
                    "latency_ms": latency_ms,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }
            except Exception as e:
                last_error = e
                if attempt < attempts:
                    total_retries += 1
                    wait_sec = attempt
                    print(f"  [自动重试] 模型 {model_name} 调用失败({type(e).__name__})，{wait_sec}s 后进行第 {attempt} 次重试...")
                    time.sleep(wait_sec)
                else:
                    print(f"  [模型失败] 模型 {model_name} 已耗尽重试次数: {e}")

    return {
        "ok": False,
        "error": str(last_error),
        "used_model": None,
        "fallback_triggered": True,
        "retries": total_retries,
        "latency_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }