"""
LLM 集成 — 调用大模型生成讲解词、回答问题

默认模型: minimax-2.5
API: OpenAI 兼容格式

性能要点:
- 用 aiohttp 真异步, 全局 session 复用 (省 TLS 握手 200-500ms/次)
- timeout 8s (跟 QA 外层一致, 老的 30s 容易让用户长时间等)
- 同 prompt 并发去重: 同一时刻多个调用同一 prompt 只发一次 HTTP, 共享结果
- execution_logs 写库 fire-and-forget, 不阻塞返回
"""

import asyncio
import hashlib
import json
import logging
import urllib.request  # noqa: F401  — _build_anthropic_request 还在用 urllib 走 anthropic 协议
from typing import Optional

import aiohttp

from app.database import get_db

logger = logging.getLogger("hub.engine.llm")

# 全局 aiohttp session — 复用 TLS 连接, 跨请求省 200-500ms 握手时间
_aiohttp_session: aiohttp.ClientSession | None = None
_aiohttp_lock = asyncio.Lock()


async def _get_aiohttp_session() -> aiohttp.ClientSession:
    """全局共享 aiohttp.ClientSession - 跨调用复用 TLS 连接。"""
    global _aiohttp_session
    if _aiohttp_session is None or _aiohttp_session.closed:
        async with _aiohttp_lock:
            if _aiohttp_session is None or _aiohttp_session.closed:
                connector = aiohttp.TCPConnector(
                    limit=20, limit_per_host=10,
                    keepalive_timeout=60.0,
                    enable_cleanup_closed=True,
                )
                _aiohttp_session = aiohttp.ClientSession(connector=connector)
    return _aiohttp_session


# 同 prompt 并发去重: 同一时刻多 caller 调同 prompt 只发一次 HTTP, 共享 Future
# 避免短时间内重复问句各自打 LLM 浪费 (e.g. 双触发 ASR / 缓存预热并行)
_inflight: dict[str, asyncio.Future] = {}
_inflight_lock = asyncio.Lock()

# 默认配置，可通过 system_config 覆盖
_DEFAULT_CONFIG = {
    "llm_enabled": "0",
    "llm_api_url": "https://zhenze-huhehaote.cmecloud.cn/v1/chat/completions",
    "llm_api_key": "",
    "llm_model": "minimax-2.5",
}


async def _get_llm_config() -> dict:
    """从数据库读取 LLM 配置"""
    config = dict(_DEFAULT_CONFIG)
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT key, value FROM system_config WHERE key LIKE 'llm_%'"
            )
            for r in rows:
                if r["value"]:
                    config[r["key"]] = r["value"]
        finally:
            await db.close()
    except Exception as e:
        logger.warning("读取 LLM 配置失败: %s", e)
    return config


def _is_anthropic_endpoint(url: str) -> bool:
    """URL 路径包含 messages → 视为 Anthropic Messages API (火山方舟 /api/coding/v1/messages 同款).

    其余 (chat/completions) 走 OpenAI 兼容协议。
    """
    return "/messages" in (url or "")


def _build_anthropic_request(url: str, api_key: str, model: str,
                             messages: list, system_prompt: str,
                             max_tokens: int, temperature: float):
    """组装 Anthropic Messages API 请求.

    关键差异 vs OpenAI:
    - system 是顶层字段, 不能放进 messages
    - role 仅 user/assistant, 不接受 system
    - max_tokens 必填
    - 响应在 content[].text, 不是 choices[].message.content

    auth: 火山方舟 ANTHROPIC_AUTH_TOKEN 走 Authorization: Bearer
    """
    # 把 messages 中残留的 system role 抽到顶层
    sys_parts = [system_prompt] if system_prompt else []
    user_msgs = []
    for m in messages or []:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            if content:
                sys_parts.append(content)
        else:
            user_msgs.append({"role": role, "content": content})

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": user_msgs,
        "temperature": temperature,
    }
    if sys_parts:
        payload["system"] = "\n\n".join(sys_parts)

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "anthropic-version": "2023-06-01",
    }
    return urllib.request.Request(url, data=body, headers=headers, method="POST")


def _parse_anthropic_response(data: dict) -> str:
    """Anthropic /v1/messages 响应 → 文本"""
    parts = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "".join(parts).strip()


async def chat_completion(
    messages: list,
    system_prompt: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.7,
) -> Optional[str]:
    """调用 LLM 获取回答"""
    config = await _get_llm_config()

    if config.get("llm_enabled") != "1":
        return None

    url = config["llm_api_url"]
    api_key = config["llm_api_key"]
    model = config["llm_model"]

    use_anthropic = _is_anthropic_endpoint(url)

    # 构造 prompt key 用于 in-flight 去重 — 同一秒内多个 caller 同 prompt 共享一次 HTTP
    full_messages_for_key: list = []
    if system_prompt:
        full_messages_for_key.append({"role": "system", "content": system_prompt})
    full_messages_for_key.extend(messages)
    key_str = json.dumps({
        "url": url, "model": model, "msgs": full_messages_for_key,
        "max_tokens": max_tokens, "temperature": temperature,
    }, ensure_ascii=False, sort_keys=True)
    prompt_key = hashlib.md5(key_str.encode("utf-8")).hexdigest()

    # in-flight 去重: 已经有同请求在跑 → 等它的结果共享
    async with _inflight_lock:
        existing = _inflight.get(prompt_key)
        if existing is not None:
            logger.info("[LLM] in-flight 命中, 共享: key=%s", prompt_key[:8])
            try:
                return await existing
            except Exception:
                return None
        loop = asyncio.get_event_loop()
        my_future: asyncio.Future = loop.create_future()
        _inflight[prompt_key] = my_future

    prompt_preview = ""
    try:
        if use_anthropic:
            # anthropic 协议 — 用 aiohttp 真异步 + 全局 session 复用 TLS 连接
            sys_parts = [system_prompt] if system_prompt else []
            user_msgs = []
            for m in messages or []:
                role = m.get("role", "user")
                content = m.get("content", "")
                if role == "system":
                    if content:
                        sys_parts.append(content)
                else:
                    user_msgs.append({"role": role, "content": content})
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": user_msgs,
                "temperature": temperature,
            }
            if sys_parts:
                payload["system"] = "\n\n".join(sys_parts)
            if messages:
                prompt_preview = str(messages[-1].get("content", ""))[:80]
            logger.info("[LLM] 调用 %s | 模型=%s | 协议=anthropic | 提示=%s...",
                        url[:60], model, prompt_preview)
            try:
                session = await _get_aiohttp_session()
                async with session.post(
                    url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                        "anthropic-version": "2023-06-01",
                    },
                    timeout=aiohttp.ClientTimeout(total=8.0),
                ) as resp:
                    data = await resp.json(content_type=None)
                result = _parse_anthropic_response(data)
            except Exception as e:
                logger.error("[LLM] anthropic 调用失败: %s", e)
                result = None
        else:
            # OpenAI 兼容 — aiohttp 真异步, 复用全局 session 省 TLS 握手
            full_messages = list(full_messages_for_key)
            prompt_preview = str(full_messages[-1].get("content", ""))[:80] if full_messages else ""
            logger.info("[LLM] 调用 %s | 模型=%s | 协议=openai | 提示=%s...",
                        url[:60], model, prompt_preview)
            try:
                session = await _get_aiohttp_session()
                async with session.post(
                    url,
                    json={
                        "model": model,
                        "messages": full_messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    },
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    timeout=aiohttp.ClientTimeout(total=8.0),
                ) as resp:
                    data = await resp.json(content_type=None)
                content = data["choices"][0]["message"]["content"]
                result = content.strip()
            except Exception as e:
                logger.error("[LLM] openai 调用失败: %s", e)
                result = None

        if result:
            logger.info("[LLM] 响应成功 (%d字): %s...", len(result), result[:100])
            # fire-and-forget 写日志, 不阻塞返回
            asyncio.create_task(_log_llm_call(model, prompt_preview, result))
        # 完成 future, 让等待的 caller 拿到结果
        if not my_future.done():
            my_future.set_result(result)
        return result
    except Exception as e:
        logger.error("[LLM] 调用异常: %s", e)
        if not my_future.done():
            my_future.set_result(None)
        return None
    finally:
        async with _inflight_lock:
            _inflight.pop(prompt_key, None)


async def _log_llm_call(model: str, prompt_preview: str, result: str):
    """fire-and-forget 写 execution_logs.llm_call. 失败静默不影响主路径。"""
    try:
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO execution_logs (log_type, source, target, action, detail, status, created_at)
                   VALUES ('llm_call', 'llm', ?, ?, ?, 'ok', datetime('now','localtime'))""",
                (model, prompt_preview, result[:200]),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception:
        pass


async def generate_narration(resource_name: str, resource_category: str = "",
                              exhibit_name: str = "") -> Optional[str]:
    """根据资源名称生成讲解词.

    exhibit_name 非空且与 resource_name 不同时, 把它作为上下文传给 LLM,
    生成的讲解词会"展项→资源"两层对齐, 比裸资源名更贴近展厅场景。
    典型场景: 资源 narration_text 为空, 用展项名+资源名让 LLM 现场生成。
    """
    if exhibit_name and exhibit_name != resource_name:
        prompt = (
            f"你是展厅讲解员, 现在给访客讲解其中一项内容「{resource_name}」。\n"
            f"请生成一段 50-80 字的讲解词:\n"
            f"- 围绕「{resource_name}」展开\n"
            f"- 语言自然流畅, 适合现场语音播报\n"
            f"- 不要使用任何特殊符号或Markdown格式\n"
            f"- 直接输出讲解词正文, 不要前缀如\"好的\""
        )
    else:
        prompt = (
            f"你是一个展厅讲解员，请为以下展项生成一段简洁的讲解词（50-80字）：\n\n"
            f"展项名称：{resource_name}\n"
            f"类型：{resource_category or '展品'}\n\n"
            f"要求：\n"
            f"- 语言自然流畅，适合语音播报\n"
            f"- 简洁明了，突出重点\n"
            f"- 不要使用特殊符号"
        )

    return await chat_completion(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=256,
        temperature=0.7,
    )


async def answer_question(question: str, context: str = "") -> Optional[str]:
    """回答访客问题"""
    system = """你是展厅的AI讲解助手。请根据上下文信息回答访客的问题。
回答要简洁（50-150字），适合语音播报，语气友好专业。
如果不确定，诚实说明。"""

    messages = []
    if context:
        messages.append({"role": "system", "content": f"当前展厅信息：{context}"})
    messages.append({"role": "user", "content": question})

    return await chat_completion(
        messages=messages,
        system_prompt=system,
        max_tokens=512,
        temperature=0.7,
    )
