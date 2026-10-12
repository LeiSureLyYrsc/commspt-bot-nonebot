"""统一回复文案常量

集中管理面向用户的固定回复文本，避免各子插件重复书写。

主要常量：
- SERVICE_FAILURE_MESSAGE: 外部网络服务/出图失败（含重试耗尽）时的统一失败提示
- USER_NOT_FOUND_MESSAGE: 玩家名类命令（&ygg / &pro / &view / &view.ygg / %view.pro）查无此人时的统一提示
"""

SERVICE_FAILURE_MESSAGE = "处理你的请求时发生了一些问题"
"""外部服务请求失败（重试耗尽或渲染类失败）时向用户回复的统一文案。"""

USER_NOT_FOUND_MESSAGE = "未查询到该用户的信息"
"""玩家名类命令查无此人（含非法角色名）时向用户回复的统一文案。"""
