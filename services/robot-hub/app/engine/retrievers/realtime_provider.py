"""
RealtimeProvider — 实时信息(天气/时间/日期)

要求: 真实数据源, 不允许 LLM 猜
- 天气主源: 高德 Web 服务 weatherInfo (https://lbs.amap.com/api/webservice/guide/api/weatherinfo/)
            实况 extensions=base, 预报 extensions=all (今天/明天/后天/大后天)
- 天气兜底: wttr.in (高德失败/没配 key 时退路, 不需 API key)
- 时间/日期: Python datetime (本机)

豹小秘和宇树两边都通过 answer_engine.answer 拿到本 retriever, 公用一份输出。
"""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import aiohttp

from app.database import get_db

logger = logging.getLogger("hub.engine.retriever.realtime")

_DEFAULT_CITY = "南京"
_WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]
_AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
_AMAP_TIMEOUT = aiohttp.ClientTimeout(total=3.0)  # 4→3s, 高德正常 <800ms

# 内存缓存 — 同 city 同类型(实况/预报) 5 分钟内复用. 现场常问"南京天气"/"今天天气"
# 命中即时返回, 不再调高德 API. weather 数据 5 分钟更新已足够准确。
import time as _time_module
_WEATHER_CACHE: dict[tuple, tuple[float, dict]] = {}
_WEATHER_TTL_SEC = 300.0  # 5 min


def _local_time_phrase() -> str:
    now = datetime.now()
    h = now.hour
    period = "上午" if h < 12 else ("中午" if h < 14 else ("下午" if h < 18 else "晚上"))
    h12 = h if h <= 12 else h - 12
    if h == 0:
        h12 = 12
    return f"现在是{period}{h12}点{now.minute:02d}分。"


def _local_date_phrase(offset_days: int = 0) -> str:
    d = datetime.now() + timedelta(days=offset_days)
    weekday = _WEEKDAY_CN[d.weekday()]
    when = "今天" if offset_days == 0 else ("明天" if offset_days == 1 else ("昨天" if offset_days == -1 else d.strftime("%Y年%m月%d日")))
    return f"{when}是{d.year}年{d.month}月{d.day}日，星期{weekday}。"


async def _amap_key() -> str:
    """读 system_config.amap_api_key — 跟 POI 共用同一把 key。"""
    try:
        db = await get_db()
        try:
            rows = await db.execute_fetchall(
                "SELECT value FROM system_config WHERE key='amap_api_key'"
            )
            return (rows[0]["value"] or "").strip() if rows else ""
        finally:
            await db.close()
    except Exception:
        return ""


async def _amap_weather(city: str, want_forecast: bool = False) -> dict | None:
    """高德天气 — extensions=base 拿实况, extensions=all 拿预报4天 (含今天)。

    返回结构化 dict: {"city","weather","temperature","wind","humidity","reporttime"}
    或 forecast 列表 [{"date","week","day_weather","night_weather","day_temp","night_temp","wind"}]
    失败返回 None, 让 caller 走 wttr 兜底。

    5 分钟内同 (city, want_forecast) 命中内存缓存直接返回, 跳过 HTTP。
    """
    cache_key = (city or _DEFAULT_CITY, bool(want_forecast))
    cached = _WEATHER_CACHE.get(cache_key)
    if cached:
        ts, data = cached
        if _time_module.time() - ts < _WEATHER_TTL_SEC:
            logger.info("[Realtime] amap weather cache HIT key=%s age=%.0fs",
                        cache_key, _time_module.time() - ts)
            return data
        else:
            _WEATHER_CACHE.pop(cache_key, None)

    key = await _amap_key()
    if not key:
        return None
    params = {
        "key": key,
        "city": city or _DEFAULT_CITY,
        "extensions": "all" if want_forecast else "base",
        "output": "JSON",
    }
    try:
        # 复用 poi_retriever 的全局 amap session — 跨调用 TLS 复用省 200-500ms
        from app.engine.retrievers.poi_retriever import _get_amap_session
        session = await _get_amap_session()
        async with session.get(_AMAP_WEATHER_URL, params=params, timeout=_AMAP_TIMEOUT) as resp:
            data = await resp.json(content_type=None)
        if str(data.get("status")) != "1":
            logger.warning("[Realtime] amap weather 失败 status=%s info=%s city=%r",
                           data.get("status"), data.get("info"), city)
            return None
        if want_forecast:
            forecasts = data.get("forecasts") or []
            if not forecasts:
                return None
            f0 = forecasts[0]
            casts = f0.get("casts") or []
            result = {
                "city": f0.get("city") or city,
                "casts": [
                    {
                        "date": c.get("date", ""),
                        "week": c.get("week", ""),
                        "day_weather": c.get("dayweather", ""),
                        "night_weather": c.get("nightweather", ""),
                        "day_temp": c.get("daytemp", ""),
                        "night_temp": c.get("nighttemp", ""),
                        "day_wind": c.get("daywind", ""),
                        "day_power": c.get("daypower", ""),
                    } for c in casts
                ],
            }
            _WEATHER_CACHE[cache_key] = (_time_module.time(), result)
            return result
        else:
            lives = data.get("lives") or []
            if not lives:
                return None
            l0 = lives[0]
            result = {
                "city": l0.get("city") or city,
                "weather": l0.get("weather", ""),
                "temperature": l0.get("temperature", ""),
                "wind": l0.get("winddirection", ""),
                "windpower": l0.get("windpower", ""),
                "humidity": l0.get("humidity", ""),
                "reporttime": l0.get("reporttime", ""),
            }
            _WEATHER_CACHE[cache_key] = (_time_module.time(), result)
            return result
    except Exception as e:
        logger.warning("[Realtime] amap weather 异常 city=%r: %s", city, e)
        return None


def _format_amap_live(d: dict) -> str:
    """高德实况 → 中文 TTS 串。"""
    city = d.get("city") or ""
    weather = d.get("weather") or "未知"
    temp = d.get("temperature") or ""
    wind = d.get("wind") or ""
    power = d.get("windpower") or ""
    humid = d.get("humidity") or ""
    seg = [f"{city}现在{weather}"]
    if temp:
        seg.append(f"气温{temp}度")
    if humid:
        seg.append(f"湿度{humid}%")
    if wind and power:
        seg.append(f"{wind}风{power}级")
    elif wind:
        seg.append(f"{wind}风")
    return "，".join(seg) + "。"


def _format_amap_forecast(d: dict, day_offset: int) -> str:
    """高德 forecast 列表中取对应天的预报文案。day_offset: 0=今天 1=明天 2=后天 3=大后天。"""
    casts = d.get("casts") or []
    if day_offset < 0 or day_offset >= len(casts):
        return ""
    c = casts[day_offset]
    city = d.get("city") or ""
    label = ["今天", "明天", "后天", "大后天"][day_offset]
    dw = c.get("day_weather") or "未知"
    nw = c.get("night_weather") or ""
    dt = c.get("day_temp") or ""
    nt = c.get("night_temp") or ""
    wind = c.get("day_wind") or ""
    power = c.get("day_power") or ""
    seg = [f"{city}{label}白天{dw}"]
    if nw and nw != dw:
        seg.append(f"夜间{nw}")
    if dt and nt:
        seg.append(f"气温{nt}到{dt}度")
    elif dt:
        seg.append(f"最高{dt}度")
    if wind and power and power != "≤3":
        seg.append(f"{wind}风{power}级")
    elif wind:
        seg.append(f"{wind}风")
    return "，".join(seg) + "。"


async def _fetch_weather(city: str) -> str | None:
    """调用 wttr.in 获取真实天气文本"""
    city = city.strip() or _DEFAULT_CITY
    encoded_city = urllib.parse.quote(city)
    # 仅 ASCII 的 format, 后续在本地拼"湿度/风"前缀, 避免 Request 对中文 URL 报 ascii 错
    fmt = urllib.parse.quote("%C %t %h %w")
    url = f"https://wttr.in/{encoded_city}?format={fmt}&lang=zh"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "curl/7.88"},
        )

        def _do():
            with urllib.request.urlopen(req, timeout=4) as resp:
                return resp.read().decode("utf-8", errors="ignore").strip()

        text = await asyncio.get_event_loop().run_in_executor(None, _do)
        # wttr 错误时返回 "Unknown location" 之类
        if not text or "Unknown" in text or "ERROR" in text or len(text) > 200:
            return None
        return text
    except Exception as e:
        logger.warning("[Realtime] wttr fail city=%s: %s", city, e)
        return None


# wttr.in 在 lang=zh 失败时会回英文 condition (Sunny / Partly cloudy / ...)
# 这些直接进 TTS 体验很差, 维护一份英→中映射, 命中即替换;
# 最后兜底: 若 condition 仍含 ASCII 字母, 用 "天气情况未知" 替代, 不让英文进 TTS。
_WEATHER_EN2ZH = {
    "sunny": "晴", "clear": "晴",
    "partly cloudy": "局部多云", "partly sunny": "局部多云",
    "cloudy": "多云", "overcast": "阴",
    "mist": "薄雾", "fog": "雾", "haze": "霾", "smoke": "烟雾",
    "patchy rain possible": "可能有阵雨", "patchy rain nearby": "附近有阵雨",
    "patchy light rain": "局部小雨", "light rain": "小雨", "light rain shower": "小阵雨",
    "moderate rain": "中雨", "heavy rain": "大雨", "heavy rain at times": "时有大雨",
    "torrential rain shower": "暴雨", "rain": "下雨", "showers": "阵雨",
    "thundery outbreaks possible": "可能有雷阵雨", "thunderstorm": "雷阵雨",
    "patchy snow possible": "可能有阵雪", "light snow": "小雪", "moderate snow": "中雪",
    "heavy snow": "大雪", "snow": "下雪", "blizzard": "暴风雪",
    "patchy sleet possible": "可能有雨夹雪", "sleet": "雨夹雪",
    "freezing fog": "冻雾", "ice pellets": "冰粒",
    "blowing snow": "吹雪", "windy": "有风",
    "hot": "炎热", "cold": "寒冷",
}


def _zh_condition(cond_text: str) -> str:
    """把 wttr 的 condition 段尽量翻成中文; 仍含英文则返回 '天气情况未知'."""
    if not cond_text:
        return "天气情况未知"
    raw = cond_text.strip()
    lower = raw.lower()
    # 长 → 短优先匹配 (避免 "partly cloudy" 被 "cloudy" 抢先)
    for en in sorted(_WEATHER_EN2ZH.keys(), key=len, reverse=True):
        if en in lower:
            lower = lower.replace(en, _WEATHER_EN2ZH[en])
            raw = lower
            break
    # 替换后仍出现 ASCII 字母 → 视为未知, 不放英文进 TTS
    if re.search(r"[a-zA-Z]", raw):
        return "天气情况未知"
    return raw


def _format_weather(city: str, raw: str) -> str:
    """raw 形如 '局部多云 +21°C 65% →12km/h' 或 'Sunny +21°C 65% →12km/h'
    → 自然中文句, 绝不放英文 condition 进 TTS。
    """
    body = raw.strip()
    body = body.replace("km/h", "公里每小时")
    body = re.sub(r"[↑↓←→↖↗↘↙]", "风", body)
    # condition 可能含空格 (Partly Cloudy), 按"温度 token" 反向定位边界
    m = re.search(r"\s([+\-]?\d+\.?\d*°[CF])\s", " " + body + " ")
    if m:
        temp = m.group(1)
        idx = body.find(temp)
        cond_part = body[:idx].strip()
        rest = body[idx + len(temp):].strip()
        rest_parts = rest.split(None, 1)
        humidity = rest_parts[0] if rest_parts else ""
        wind = rest_parts[1] if len(rest_parts) > 1 else ""
        cond = _zh_condition(cond_part)
        seg = [f"{city}现在{cond}", f"气温{temp}"]
        if humidity:
            seg.append(f"湿度{humidity}")
        if wind:
            seg.append(wind)
        return "，".join(seg) + "。"
    # 兜底切分 (老逻辑)
    parts = body.split()
    if len(parts) >= 4:
        cond, temp, humidity, wind = parts[0], parts[1], parts[2], " ".join(parts[3:])
        cond = _zh_condition(cond)
        return f"{city}现在{cond}，气温{temp}，湿度{humidity}，{wind}。"
    # 实在解析不出来, 整段查英文 → 不可用
    if re.search(r"[a-zA-Z]", body):
        return f"{city}天气数据格式异常，建议您查一下手机天气。"
    return f"{city}现在{body}。"


async def retrieve(text: str, slots: dict, world_state=None, robot_id: str = "") -> dict:
    sub = (slots or {}).get("sub", "")

    if sub == "time":
        return {
            "answer": _local_time_phrase(),
            "source": "local_clock",
            "confidence": 1.0,
        }

    if sub == "date":
        offset = 0
        if "明天" in text:
            offset = 1
        elif "昨天" in text:
            offset = -1
        return {
            "answer": _local_date_phrase(offset),
            "source": "local_clock",
            "confidence": 1.0,
        }

    # 默认按天气处理
    city = (slots or {}).get("city", "") or _DEFAULT_CITY

    # 解析"哪一天" — 0 今天 / 1 明天 / 2 后天 / 3 大后天
    day_offset = 0
    if "大后天" in text:
        day_offset = 3
    elif "后天" in text:
        day_offset = 2
    elif "明天" in text:
        day_offset = 1
    elif "昨天" in text:
        day_offset = -1  # 高德不给历史, 后面会用 wttr 文案兜底

    # 1) 高德为主源 — 实况 base; 问"明天/后天/大后天/这几天"走 forecast
    want_forecast = day_offset > 0 or any(
        kw in text for kw in ("明天", "后天", "大后天", "这两天", "这几天", "未来", "周末", "星期", "周")
    )
    if day_offset >= 0:
        amap = await _amap_weather(city, want_forecast=want_forecast)
        if amap:
            if want_forecast and "casts" in amap:
                ans = _format_amap_forecast(amap, day_offset)
                # 用户问"这几天/这两天" → 拼前 3 天
                if not ans and any(kw in text for kw in ("这两天", "这几天", "未来", "几天")):
                    parts = []
                    for i in range(min(3, len(amap.get("casts", [])))):
                        s = _format_amap_forecast(amap, i)
                        if s: parts.append(s)
                    ans = "".join(parts)
            elif want_forecast:
                # forecast 缺数据降级回实况
                live = await _amap_weather(city, want_forecast=False)
                ans = _format_amap_live(live) if live else ""
            else:
                ans = _format_amap_live(amap)
            if ans:
                return {
                    "answer": ans,
                    "source": "weather_api",
                    "confidence": 0.96,
                    "city": city,
                }
        else:
            logger.info("[Realtime] amap 没回数据, 降级到 wttr.in city=%s", city)

    # 2) wttr.in 兜底 (高德失败或没配 key)
    raw = await _fetch_weather(city)
    if raw:
        ans = _format_weather(city, raw)
        if day_offset > 0:
            ans = f"我这边只能查到当前天气：{ans}建议您看一下手机天气预报获取后续数据。"
        elif day_offset < 0:
            ans = f"我查不到昨天的历史天气, 帮您看下当前: {ans}"
        return {
            "answer": ans,
            "source": "weather_api",
            "confidence": 0.85,
            "city": city,
        }
    # 真实接口都失败: 不允许 LLM 猜
    return {
        "answer": f"抱歉，{city}的天气服务暂时不可用，您可以查一下手机天气。",
        "source": "weather_unavailable",
        "confidence": 0.3,
        "city": city,
    }
