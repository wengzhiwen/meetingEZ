"""
辅助工具函数
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional


def format_duration(seconds: float) -> str:
    """
    格式化时长

    Args:
        seconds: 秒数

    Returns:
        格式化的时长字符串，如 "1:23:45" 或 "23:45"
    """
    if seconds < 0:
        return "0:00"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def format_date(d: date, fmt: str = "YYYY-MM-DD") -> str:
    """
    格式化日期

    Args:
        d: 日期对象
        fmt: 格式模板

    Returns:
        格式化的日期字符串
    """
    format_map = {
        "YYYY-MM-DD": "%Y-%m-%d",
        "YYYY/MM/DD": "%Y/%m/%d",
        "MM-DD": "%m-%d",
        "中文": "%Y年%m月%d日",
    }

    py_fmt = format_map.get(fmt, fmt)
    return d.strftime(py_fmt)


def sanitize_filename(name: str) -> str:
    """
    清理文件名，移除非法字符

    Args:
        name: 原始文件名

    Returns:
        清理后的文件名
    """
    # 移除或替换非法字符
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    # 移除首尾空格和点
    name = name.strip(". ")
    # 限制长度
    if len(name) > 200:
        name = name[:200]
    return name


def parse_meeting_dir_name(dir_name: str) -> tuple[Optional[date], str]:
    """
    解析会议目录名称

    Args:
        dir_name: 目录名，如 "2025-03-18_产品评审会"

    Returns:
        (日期, 标题) 元组
    """
    # 尝试匹配 YYYY-MM-DD_格式
    match = re.match(r"^(\d{4}-\d{2}-\d{2})[_\s](.+)$", dir_name)
    if match:
        try:
            meeting_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            return meeting_date, match.group(2)
        except ValueError:
            pass

    return None, dir_name


def get_week_bounds(d: date) -> tuple[date, date]:
    """
    获取某日期所在周的周一和周日

    Args:
        d: 日期

    Returns:
        (周一, 周日) 元组
    """
    weekday = d.weekday()
    monday = d - timedelta(days=weekday)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def is_same_week(d1: date, d2: date) -> bool:
    """判断两个日期是否在同一周"""
    w1 = get_week_bounds(d1)
    w2 = get_week_bounds(d2)
    return w1 == w2


def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    截断文本

    Args:
        text: 原始文本
        max_length: 最大长度
        suffix: 截断后缀

    Returns:
        截断后的文本
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix
