"""用户档案状态、内容转义以及两种截图后端的完整页面契约。"""

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "plugins/commspt_bot/templates"


@pytest.fixture
def user_data():
    return {
        "generated_at": "2026-10-12 20:00:00",
        "uid": 100001,
        "permission": 0,
        "score": 2048,
        "nickname": "LittleSkin_Demo",
        "network": "中国 · 上海 · 示例网络",
        "qq": 123456789,
        "qq_nickname": "演示用户",
        "register_at": "2024-02-09 14:23:11",
        "last_sign_at": "2026-10-12 19:47:26",
        "email": "123456789@qq.com",
        "verified": True,
        "email_help": "已验证",
        "ban_reason": None,
        "is_dark_mode": False,
    }


@pytest.fixture
def template():
    return Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True).get_template("user-info.html.jinja")


@pytest.mark.parametrize("permission", [0, 1, 2])
def test_active_accounts_show_status_instead_of_permission(template, user_data, permission):
    user_data["permission"] = permission
    html = template.render(user_data)
    assert "账户状态" in html
    assert 'account-badge">正常<' in html
    assert 'class="ban-notice"' not in html


@pytest.mark.parametrize("reason", [None, "", "   \n\t"])
def test_banned_accounts_have_notice_even_without_reason(template, user_data, reason):
    user_data.update(permission=-1, ban_reason=reason)
    html = template.render(user_data)
    assert 'class="record is-banned"' in html
    assert 'account-badge">已封禁<' in html
    assert 'class="ban-notice"' in html
    assert "未提供封禁原因" in html


def test_long_reason_and_user_text_are_preserved_and_escaped(template, user_data):
    reason = "<script>alert(1)</script>\n" + "一段很长的封禁原因。" * 200
    user_data.update(permission=-1, ban_reason=reason, nickname='<img src=x onerror="alert(1)">')
    html = template.render(user_data)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;\n" in html
    assert "一段很长的封禁原因。" * 200 in html
    assert "&lt;img src=x" in html
    assert "<script>" not in html
    assert "<img " not in html


def test_unverified_email_does_not_change_account_status(template, user_data):
    user_data.update(verified=False, email="Demo@example.com", email_help="未验证 / 含有大写字母")
    html = template.render(user_data)
    assert 'account-badge">正常<' in html
    assert 'class="email is-unverified"' in html
    assert "未验证 / 含有大写字母" in html


def test_optional_qq_and_never_signed_in(template, user_data):
    user_data.update(qq=None, qq_nickname="", last_sign_at="2000-01-01 00:00:00")
    html = template.render(user_data)
    assert 'class="qq-grid"' not in html
    assert "从未签到过" in html
    assert "注册 IP 属地" in html


def test_dark_theme_uses_existing_user_preference(template, user_data):
    assert 'data-theme="light"' in template.render(user_data)
    user_data["is_dark_mode"] = True
    assert 'data-theme="dark"' in template.render(user_data)


def load_isolated_module(monkeypatch, name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def renderer(monkeypatch):
    # 不启动机器人、不加载整套插件，也不读取部署配置或访问真实服务。
    nonebot = ModuleType("nonebot")
    nonebot.get_driver = lambda: SimpleNamespace(on_shutdown=lambda callback: callback)
    nonebot.logger = SimpleNamespace(info=lambda *args: None)
    monkeypatch.setitem(sys.modules, "nonebot", nonebot)
    config = ModuleType("plugins.commspt_bot.config")
    config.S_ = SimpleNamespace(
        browserless_mode="REMOTE", api_browserless=SimpleNamespace(endpoint="https://example.com")
    )
    config.TEMPLATE_DIR = TEMPLATES
    config.VERIFY_CONTENT = True
    monkeypatch.setitem(sys.modules, "plugins.commspt_bot.config", config)
    return load_isolated_module(monkeypatch, "card_test_browserless", "plugins/commspt_bot/utils/browserless.py")


@pytest.mark.parametrize("full_page", [False, True])
async def test_local_backend_forwards_full_page_and_closes_context(renderer, monkeypatch, full_page):
    page = SimpleNamespace(set_content=AsyncMock(), screenshot=AsyncMock(return_value=b"png"))
    context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
    browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
    monkeypatch.setattr(renderer, "_ensure_browser", AsyncMock(return_value=browser))
    assert await renderer._screenshot_local("<html></html>", 530, 1, True, 2.5, full_page) == b"png"
    page.screenshot.assert_awaited_once_with(type="png", full_page=full_page)
    context.close.assert_awaited_once()


@pytest.mark.parametrize("full_page", [False, True])
async def test_remote_backend_forwards_full_page(renderer, monkeypatch, full_page):
    class Client:
        def __init__(self, **kwargs):
            self.post = AsyncMock(return_value=SimpleNamespace(content=b"png", raise_for_status=lambda: None))
            clients.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    clients = []
    monkeypatch.setattr(renderer.httpx, "AsyncClient", Client)
    assert await renderer._screenshot_remote("<html></html>", 530, 1, True, 2.5, full_page) == b"png"
    payload = clients[0].post.await_args.kwargs["json"]
    assert payload["options"] == {"type": "png", "fullPage": full_page}
    assert payload["viewport"]["height"] == 1


@pytest.mark.parametrize("mode", ["LOCAL", "REMOTE"])
async def test_public_screenshot_keeps_default_and_opt_in(renderer, monkeypatch, user_data, mode):
    local, remote = AsyncMock(return_value=b"png"), AsyncMock(return_value=b"png")
    monkeypatch.setattr(renderer, "_screenshot_local", local)
    monkeypatch.setattr(renderer, "_screenshot_remote", remote)
    renderer.S_.browserless_mode = mode
    backend = local if mode == "LOCAL" else remote
    await renderer.screenshot("user-info.html.jinja", **user_data)
    assert backend.await_args.args[2] == 800
    assert backend.await_args.args[-1] is False
    await renderer.screenshot("user-info.html.jinja", height=1, full_page=True, **user_data)
    assert backend.await_args.args[2] == 1
    assert backend.await_args.args[-1] is True
    assert "LittleSkin_Demo" in backend.await_args.args[0]


async def test_user_card_requests_full_content_and_keeps_dark_mode(renderer, monkeypatch, user_data):
    monkeypatch.setitem(sys.modules, "plugins.commspt_bot.utils.browserless", renderer)
    ipip = ModuleType("plugins.commspt_bot.models.bingling_ipip")
    ipip.BingLingIPIP = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                country_name="中国",
                region_name="上海",
                city_name="",
                isp_domain="示例",
                owner_domain="网络",
            )
        )
    )
    monkeypatch.setitem(sys.modules, "plugins.commspt_bot.models.bingling_ipip", ipip)
    model = load_isolated_module(monkeypatch, "card_test_model", "plugins/commspt_bot/models/render_user_info.py")
    screenshot = AsyncMock(return_value=b"png")
    monkeypatch.setattr(model, "screenshot", screenshot)
    data = dict(user_data, ip=["127.0.0.1"], is_dark_mode=True)
    for field in ("generated_at", "register_at", "last_sign_at"):
        data[field] = datetime.fromisoformat(data[field])
    assert await model.RenderUserInfo(**data).get_image() == b"png"
    assert screenshot.await_args.kwargs["height"] == 1
    assert screenshot.await_args.kwargs["full_page"] is True
    assert screenshot.await_args.kwargs["is_dark_mode"] is True
