"""
工作日判断 — 支持中国调休/补班

数据源: timor.tech 免费节假日 API
本地缓存: 按月缓存，减少请求
"""

import asyncio
import json
import logging
import urllib.request
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger("hub.utils.workday")

# 月缓存: "2026-04" -> {1: "workday", 2: "workday", ..., 30: "holiday"}
_month_cache: dict = {}


async def is_workday(d: Optional[date] = None) -> bool:
    """判断指定日期是否为工作日（含调休补班）"""
    d = d or date.today()
    day_type = await _get_day_type(d)
    return day_type in ("workday", "wage_day")  # 工作日 或 补班日


async def is_holiday(d: Optional[date] = None) -> bool:
    """判断指定日期是否为法定节假日"""
    d = d or date.today()
    day_type = await _get_day_type(d)
    return day_type == "holiday"


async def is_weekend(d: Optional[date] = None) -> bool:
    """判断指定日期是否为周末（不含补班周六日）"""
    d = d or date.today()
    day_type = await _get_day_type(d)
    return day_type == "weekend"


async def check_weekday_filter(filter_type: str, d: Optional[date] = None) -> bool:
    """
    检查日期是否符合 weekday_filter

    filter_type:
      - "all": 每天都执行
      - "workday": 仅工作日（周一到周五 + 调休补班的周末）
      - "weekend": 仅周末（不含补班的周六日）
      - "holiday": 仅法定节假日
    """
    if filter_type == "all" or not filter_type:
        return True
    elif filter_type == "workday":
        return await is_workday(d)
    elif filter_type == "weekend":
        return await is_weekend(d)
    elif filter_type == "holiday":
        return await is_holiday(d)
    return True


async def _get_day_type(d: date) -> str:
    """
    获取日期类型

    返回值:
      - "workday": 普通工作日
      - "weekend": 普通周末
      - "holiday": 法定节假日
      - "wage_day": 补班日（调休上班）
    """
    month_key = d.strftime("%Y-%m")

    if month_key not in _month_cache:
        await _fetch_month(d.year, d.month)

    month_data = _month_cache.get(month_key, {})
    return month_data.get(d.day, "workday" if d.weekday() < 5 else "weekend")


async def _fetch_month(year: int, month: int):
    """从 timor.tech 拉取整月节假日数据"""
    month_key = f"{year}-{month:02d}"
    url = f"https://timor.tech/api/holiday/year/{year}"

    try:
        resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: urllib.request.urlopen(url, timeout=10),
        )
        data = json.loads(resp.read().decode("utf-8"))

        if data.get("code") != 0:
            logger.warning("节假日 API 返回异常: %s", data)
            _month_cache[month_key] = {}
            return

        holiday_data = data.get("holiday", {})
        month_result = {}

        # 遍历当月每一天
        import calendar
        _, days_in_month = calendar.monthrange(year, month)

        for day in range(1, days_in_month + 1):
            d = date(year, month, day)
            date_str = d.strftime("%m-%d")  # "04-01"

            if date_str in holiday_data:
                entry = holiday_data[date_str]
                is_off = entry.get("holiday", True)
                if is_off:
                    month_result[day] = "holiday"
                else:
                    # 补班日
                    month_result[day] = "wage_day"
            else:
                # 普通日
                month_result[day] = "workday" if d.weekday() < 5 else "weekend"

        _month_cache[month_key] = month_result
        logger.info("节假日数据已缓存: %s (%d天)", month_key, len(month_result))

    except Exception as e:
        logger.warning("获取节假日数据失败 (%s), 使用默认周末判断: %s", month_key, e)
        # 降级: 只按周几判断
        import calendar
        _, days_in_month = calendar.monthrange(year, month)
        month_result = {}
        for day in range(1, days_in_month + 1):
            d = date(year, month, day)
            month_result[day] = "workday" if d.weekday() < 5 else "weekend"
        _month_cache[month_key] = month_result
