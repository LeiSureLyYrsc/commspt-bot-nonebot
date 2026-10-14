"""Browserless 网页截图工具

通过 Jinja2 模板渲染 HTML，并按 ``BROWSERLESS_MODE`` 选择渲染后端：
- ``REMOTE``（默认）: 调用 Browserless HTTP 服务（POST /screenshot）
- ``LOCAL``: 使用本地 Playwright + Chromium 渲染（需安装可选依赖 render-local）
            安装方式：``uv sync --extra render-local && uv run playwright install chromium``

主要接口：
- screenshot: 异步渲染 Jinja2 模板并截图返回 PNG 图片字节流
"""

from __future__ import annotations

import asyncio
import json

import httpx
from jinja2 import Environment, FileSystemLoader
from nonebot import get_driver, logger
from pydantic import BaseModel

from plugins.commspt_bot.config import ASSETS_DIR, S_, TEMPLATE_DIR, VERIFY_CONTENT

# 模板资源基址：REMOTE 走内网 CDN；LOCAL 走本地 assets/browserless（file:// 便于 set_content 解析，
# 因 set_content 无 baseURL，相对路径无法解析）。LOCAL 所需的三个文件由人工放入该目录（已 gitignore）。
_REMOTE_ASSETS_ORIGIN = "https://assets-z1-113a.serinanya.fwnet"
_REMOTE_FA_JS_URL = f"{_REMOTE_ASSETS_ORIGIN}/fontawesome@6.5.2/js/all.min.js"
_REMOTE_FA_CSS_URL = f"{_REMOTE_ASSETS_ORIGIN}/fontawesome@6.5.2/css/fontawesome.min.css"
_REMOTE_FONT_URL = f"{_REMOTE_ASSETS_ORIGIN}/PingFang%20Bold.ttf"
# LOCAL 资源目录内必须存在的文件（与用户放入的文件名保持约定）
_LOCAL_FONT_FILE = "PingFangSC-Bold.ttf"


def _resolve_assets() -> dict[str, str]:
    """按渲染模式返回模板资源变量（assets_origin / fa_js_url / fa_css_url / font_url）。

    LOCAL 模式下要求 assets/browserless 内三个资源齐全，缺失即抛错终止（不静默回退远程，
    否则会拍出缺字体/缺图标的卡片）。
    """
    if S_.browserless_mode != "LOCAL":
        return {
            "assets_origin": _REMOTE_ASSETS_ORIGIN,
            "fa_js_url": _REMOTE_FA_JS_URL,
            "fa_css_url": _REMOTE_FA_CSS_URL,
            "font_url": _REMOTE_FONT_URL,
        }

    local_dir = ASSETS_DIR / "browserless"
    required = ("all.min.js", "fontawesome.min.css", _LOCAL_FONT_FILE)
    if any(not (local_dir / name).is_file() for name in required):
        raise RuntimeError("处理你的请求时发生了一些问题：LOCAL 模式下缺失部分资源文件")
    base = local_dir.resolve().as_uri()
    return {
        "assets_origin": "",  # 本地无需 preconnect
        "fa_js_url": f"{base}/all.min.js",
        "fa_css_url": f"{base}/fontawesome.min.css",
        "font_url": f"{base}/{_LOCAL_FONT_FILE}",
    }


# ---------------------------------------------------------------------------
# LOCAL 渲染后端：常驻 Chromium（懒初始化）
# ---------------------------------------------------------------------------
_LAUNCH_LOCK = asyncio.Lock()
_playwright = None
_browser = None
_FONT_TIMEOUT_SECONDS = 10
# 截图前的就绪判定：字体加载完成 → 再等一帧布局绘制（避免中文/数字用临时回退字体渲染），
# 最后等 FontAwesome 把 <i class="fa-…"> 换成 inline SVG（模板图标依赖它的 JS）。
# 合成单次 evaluate，LOCAL 与 REMOTE 两条后端共用同一套判定。
_SELECTORS_READY = "i.fa-solid, i.fa-brands"
_READY = (
    "async () => { await document.fonts.ready; "
    "await new Promise(resolve => requestAnimationFrame(resolve)); "
    f"if (!document.querySelector('{_SELECTORS_READY}')) return true; "
    "const deadline = Date.now() + 5000; "
    "while (Date.now() < deadline) { "
    f"  if (!document.querySelector('{_SELECTORS_READY}')) return true; "
    "  await new Promise(r => setTimeout(r, 50)); "
    "} return true; }"
)


async def _ensure_browser():
    """懒初始化并返回常驻 Chromium 实例（首次调用时启动 Playwright）。"""
    global _playwright, _browser
    if _browser is not None:
        return _browser

    async with _LAUNCH_LOCK:
        if _browser is not None:
            return _browser
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            # LOCAL 模式缺少可选依赖时给出明确指引
            raise RuntimeError(
                "BROWSERLESS_MODE=LOCAL 需要安装 playwright："
                "uv sync --extra render-local && uv run playwright install chromium",
            ) from exc
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=["--ignore-certificate-errors"],
        )
        logger.info("本地 Playwright Chromium 已启动（BROWSERLESS_MODE=LOCAL）")
        return _browser


async def _screenshot_local(
    html: str,
    width: int,
    height: int,
    is_mobile: bool,
    device_scale_factor: float,
    full_page: bool = False,
    transparent: bool = False,
) -> bytes:
    """使用本地 Playwright 渲染 HTML 并截图为 PNG 字节流。"""
    browser = await _ensure_browser()
    # 视口配置与远程 Browserless 请求保持一致：分辨率、移动端模拟、设备像素比
    context = await browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=device_scale_factor,
        is_mobile=is_mobile,
        ignore_https_errors=True,
        locale="zh-CN",
        extra_http_headers={"Accept-Language": "zh-CN,en;q=0.9"},
    )
    try:
        page = await context.new_page()
        # 等待网络空闲（networkidle）确保静态资源加载完成，再按视口截图
        await page.set_content(html, wait_until="networkidle")
        # 字体/图标就绪后再截图（见 _READY）
        await asyncio.wait_for(page.evaluate(_READY), timeout=_FONT_TIMEOUT_SECONDS + 5)
        return await page.screenshot(type="png", full_page=full_page, omit_background=transparent)
    finally:
        await context.close()


async def _screenshot_remote(
    html: str,
    width: int,
    height: int,
    is_mobile: bool,
    device_scale_factor: float,
    full_page: bool = False,
    transparent: bool = False,
) -> bytes:
    """调用远程 Browserless 服务渲染网页为 PNG 图片字节流。"""
    async with httpx.AsyncClient(
        base_url=S_.api_browserless.endpoint,
        verify=VERIFY_CONTENT,
        http2=True,
        timeout=20,
    ) as client:
        resp = await client.post(
            "/screenshot",
            params={
                # Chromium 启动参数：忽略证书错误以兼容自签名环境，并开启无头模式
                "launch": json.dumps(
                    {
                        "args": ["--ignore-certificate-errors"],
                        "ignoreHTTPSErrors": True,
                        "headless": True,
                    },
                ),
            },
            json={
                "html": html,
                "options": {"type": "png", "fullPage": full_page, "omitBackground": transparent},
                # 视口配置：指定截图分辨率、移动端视口模拟及设备像素比（高 DPI 保证渲染清晰度）
                "viewport": {
                    "width": width,
                    "height": height,
                    "isMobile": is_mobile,
                    "deviceScaleFactor": device_scale_factor,
                },
                # 请求头配置与页面加载策略：设置中文语言偏好，并等待网络连接空闲（networkidle0）确保静态资源加载完成
                "setExtraHTTPHeaders": {"Accept-Language": "zh-CN,en;q=0.9"},
                "gotoOptions": {"waitUntil": ["networkidle0"]},
                "waitForFunction": {"fn": _READY, "timeout": (_FONT_TIMEOUT_SECONDS + 5) * 1000},
            },
        )
        # raise exception if status is bad
        resp.raise_for_status()
        return resp.content


async def screenshot(
    template_name: str,
    width: int = 530,
    height: int = 800,
    is_mobile: bool = True,
    device_scale_factor: float = 2.5,
    full_page: bool = False,
    transparent: bool = False,
    **params,
) -> bytes:
    """渲染模板并返回 PNG；``full_page=True`` 捕获视口之外的完整内容。

    ``transparent=True`` 时页面背景透明（PNG 保留 alpha），截图内容即卡片本体。
    """
    # Jinja2 render: 从项目模板目录 TEMPLATE_DIR 加载指定模板文件
    jinja_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    template = jinja_env.get_template(name=template_name)
    # 资源基址按渲染后端注入（REMOTE→内网 CDN；LOCAL→本地 assets/browserless，缺失即报错）
    assets = _resolve_assets()
    base_ctx = params.model_dump() if isinstance(params, BaseModel) else dict(params)
    html = template.render({**base_ctx, "transparent": transparent, **assets})

    if S_.browserless_mode == "LOCAL":
        return await _screenshot_local(
            html, width, height, is_mobile, device_scale_factor, full_page, transparent
        )
    return await _screenshot_remote(html, width, height, is_mobile, device_scale_factor, full_page, transparent)


@get_driver().on_shutdown
async def _close_browser() -> None:
    """应用关闭时释放常驻 Chromium 与 Playwright。"""
    global _playwright, _browser
    if _browser is not None:
        await _browser.close()
        _browser = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None
