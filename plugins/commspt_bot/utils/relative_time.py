"""相对时间格式化。

纯函数，不依赖 nonebot / 配置，便于测试与离线脚本（如 ``tests/ui-compare``）复用，
避免「模型算一套、出图脚本另写一套」导致两边文案不一致。
"""

from __future__ import annotations

from datetime import datetime

import arrow


def ensure_aware(moment: datetime) -> datetime:
    """给朴素时间补上服务器本地时区，使其可与带时区的时间相减。

    ``generated_at`` 默认取自 ``datetime.now()``（朴素），而 ``register_at`` 由
    ``StdTime`` 规范化为 Asia/Shanghai（带时区）；两者直接相减会抛 TypeError。
    朴素值按服务器本地时区解释，再统一换算到北京时间比较。
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return arrow.get(moment).to("Asia/Shanghai").datetime


def months_since(start: datetime, end: datetime) -> int:
    """start → end 之间走过的完整自然月数（日号溢出时按 arrow 收敛处理）。"""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    # 按候选月数平移后若已越过 end，说明最后一个自然月还没走满
    if arrow.get(start).shift(months=months).datetime > end:
        months -= 1
    return max(months, 0)


def humanize_since(base: datetime, moment: datetime) -> str:
    """把「moment 距 base 多久」写成中文相对时间（如「4 年 2 个月前」「2 天前」）。

    只报到两个单位：年月组合最多到「N 年 M 个月前」，更近则依次退到月 / 天 / 时 / 分。
    ``moment`` 晚于 ``base`` 时返回「刚刚」。
    """
    base, moment = ensure_aware(base), ensure_aware(moment)
    if moment >= base:
        return "刚刚"

    months = months_since(moment, base)
    years, rest_months = divmod(months, 12)
    if years:
        return f"{years} 年 {rest_months} 个月前" if rest_months else f"{years} 年前"
    if months:
        return f"{months} 个月前"

    delta = base - moment
    if delta.days:
        return f"{delta.days} 天前"
    hours, remainder = divmod(delta.seconds, 3600)
    if hours:
        return f"{hours} 小时前"
    minutes = remainder // 60
    return f"{minutes} 分钟前" if minutes else "刚刚"
