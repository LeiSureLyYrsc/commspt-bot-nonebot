"""跨群消息发送工具

受 avilla 版 `ctx.scene.into(f"::group({g})").send_message(...)` 启发，
NoneBot 下使用 `UniMessage.send(Target.group(...))` 实现向指定群发送消息。

主要接口：
- send_to_group: 向指定群号主动推送文本或 UniMessage 消息
- send_to_group_with_at: 向指定群号发送带 @ 目标用户的消息
- send_to_group_with_id: 向指定群号发送消息并返回其 message_id（供审批消息贴表情等场景使用）
"""

from nonebot import logger
from nonebot_plugin_alconna.uniseg import At, Target, UniMessage
from nonebot_plugin_alconna.uniseg.constraint import SupportScope


def _to_unimessage(message: str | UniMessage) -> UniMessage:
    """统一为 UniMessage。"""
    return UniMessage(message) if isinstance(message, str) else message


def _group_target(group_id: int) -> Target:
    """构造群聊目标：用 scope=SupportScope.qq_client 限定在 QQ 客户端适配器范围内寻址发送。"""
    return Target.group(str(group_id), scope=SupportScope.qq_client)


async def send_to_group(group_id: int, message: str | UniMessage):
    """向指定群发送消息"""
    if not group_id:
        logger.warning("目标群号未配置，跳过发送")
        return
    await _to_unimessage(message).send(_group_target(group_id))


def _extract_message_id(receipt) -> str | None:
    """从 UniMessage.send 返回的 Receipt 中提取 message_id（取不到返回 None）。

    Alconna 的 OneBot11 导出器会把 msg_ids 元素规整为 dict（如 {"message_id": -123}），
    部分实现也可能直接给出标量 ID，故两种形态都兼容。
    """
    msg_ids = getattr(receipt, "msg_ids", None) or []
    if msg_ids:
        first = msg_ids[0]
        mid = first.get("message_id") if isinstance(first, dict) else first
        if mid is not None and mid != "":
            return str(mid)
    # 退化路径：部分实现可能在回执对象上直接暴露 message_id
    message_id = getattr(receipt, "message_id", None)
    return str(message_id) if message_id else None


async def send_to_group_with_id(group_id: int, message: str | UniMessage) -> str | None:
    """向指定群发送消息并返回其 message_id（取不到时返回 None）

    UniMessage.send 返回 Receipt，其 msg_ids 为适配器返回的消息 ID 列表；
    协议端（SnowLuma）的 send_group_msg 会在其中给出本机消息 ID。
    """
    if not group_id:
        logger.warning("目标群号未配置，跳过发送")
        return None
    receipt = await _to_unimessage(message).send(_group_target(group_id))
    return _extract_message_id(receipt)


async def send_to_group_with_at(group_id: int, user_id: int | str, message: str | UniMessage):
    """向指定群发送带 @ 的消息"""
    await send_to_group(group_id, UniMessage(At("user", str(user_id))) + " " + _to_unimessage(message))
