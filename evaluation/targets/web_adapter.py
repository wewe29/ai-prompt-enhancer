"""网页版目标模型适配器：Playwright 持久 profile / CDP 双模式。

核心流程：health_check（登录态检测）→ new_chat（防串扰）→ send（慢速输入）
→ 等待生成完成（stop 按钮消失 或 输出稳定）→ 提取最终回答。

选择器全部来自 config.yaml 的 target.selectors，站点改版时只改配置。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .base import (
    GenerationTimeout,
    LoginRequired,
    NetworkError,
    RateLimited,
    SelectorError,
    TargetAdapter,
    TargetError,
)

RATE_LIMIT_KEYWORDS = ("稍后再试", "频率过快", "rate limit", "Rate limit", "请求过于频繁", "请稍后重试")


class BrowserManager:
    """管理浏览器生命周期：playwright 持久 profile 或 cdp 连接（两种模式共用）。"""

    def __init__(self, browser_cfg: dict[str, Any], profile_dir: Path):
        self.browser_cfg = browser_cfg
        self.profile_dir = profile_dir
        self._pw = None
        self._browser = None
        self._context = None
        self._pages: dict[str, Any] = {}

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise NetworkError("未安装 playwright，请先执行：pip install -r requirements.txt && python -m playwright install chromium") from exc
        self._pw = sync_playwright().start()
        mode = self.browser_cfg.get("mode", "playwright")
        try:
            if mode == "cdp":
                cdp_url = self.browser_cfg.get("cdp_url", "http://localhost:9222")
                self._browser = self._pw.chromium.connect_over_cdp(cdp_url)
                self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
                print(f"[browser] 已连接 Chrome CDP：{cdp_url}")
            else:
                # channel: msedge=系统 Edge | chrome=系统 Chrome | chromium=Playwright 自带浏览器
                # 用系统浏览器可避免下载的 Chromium 被安全软件拦截，也无需额外安装浏览器
                channel = self.browser_cfg.get("channel", "msedge")
                launch_kwargs: dict[str, Any] = {
                    "user_data_dir": str(self.profile_dir),
                    "headless": bool(self.browser_cfg.get("headless", False)),
                    "viewport": {"width": 1280, "height": 900},
                    "locale": "zh-CN",
                    # 帮助系统浏览器（Edge/Chrome）首启时快速进入页面，跳过首次运行引导
                    "args": ["--no-first-run", "--no-default-browser-check",
                             "--disable-features=msEdgeFirstRunExperience"],
                }
                if channel and channel != "chromium":
                    launch_kwargs["channel"] = channel
                self._context = self._pw.chromium.launch_persistent_context(**launch_kwargs)
                self._browser = self._context.browser
                print(f"[browser] 已启动 {channel or 'chromium'} 持久浏览器，profile：{self.profile_dir}")
        except Exception as exc:
            self.stop()
            hint = ""
            if "msedge" in str(exc) or "channel" in str(exc):
                hint = ("\n提示：找不到 Edge 时请确认系统已安装 Microsoft Edge，"
                        "或将 config.yaml 的 browser.channel 改为 chrome（系统 Chrome）或 chromium（Playwright 自带）")
            raise NetworkError(f"浏览器启动失败：{exc}{hint}") from exc

    def page_for(self, target_id: str, url: str):
        """每个 target 复用独立页面。"""
        if target_id not in self._pages or self._pages[target_id].is_closed():
            page = self._context.new_page()
            self._pages[target_id] = page
        return self._pages[target_id]

    def stop(self) -> None:
        is_cdp = self.browser_cfg.get("mode") == "cdp"
        for page in self._pages.values():
            try:
                page.close()
            except Exception:
                pass
        self._pages.clear()
        if not is_cdp:
            # 仅关闭自己启动的 context；CDP 模式连接的是用户浏览器，不能关闭其 context
            try:
                if self._context:
                    self._context.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = None
        self._browser = None
        self._context = None


class WebTargetAdapter(TargetAdapter):
    """网页版 target 适配器。"""

    def __init__(self, name: str, target_cfg: dict[str, Any], global_cfg: dict[str, Any], browser_manager: BrowserManager | None = None):
        super().__init__(name, target_cfg, global_cfg)
        self.url = target_cfg.get("url", "")
        self.selectors = target_cfg.get("selectors", {}) or {}
        self.timeout = float(target_cfg.get("timeout", 30))  # 生成超时等待（秒），默认 30
        self.stabilize_seconds = float(target_cfg.get("stabilize_seconds", 8))
        self.wait_strategy = target_cfg.get("wait_strategy", "stop_button")
        self.browser_cfg = global_cfg.get("browser", {})
        self.profile_dir = Path(global_cfg.get("_eval_root", ".")) / ".browser_profile"
        self._browser_manager = browser_manager
        # 仅自己创建的浏览器才负责关闭；外部传入的共享浏览器由 run_eval 统一关闭
        self._owns_browser = browser_manager is None

    # ---- 浏览器生命周期 ----
    def _get_browser_manager(self) -> BrowserManager:
        """惰性创建或返回共享的 BrowserManager 实例。"""
        if self._browser_manager is None:
            self._browser_manager = BrowserManager(self.browser_cfg, self.profile_dir)
            self._browser_manager.start()
        return self._browser_manager

    def close(self) -> None:
        if self._owns_browser and self._browser_manager is not None:
            self._browser_manager.stop()
            self._browser_manager = None

    # ---- 核心流程 ----
    def health_check(self) -> None:
        bm = self._get_browser_manager()
        page = bm.page_for(self.name, self.url)
        print(f"[{self.name}] 正在打开 {self.url} ...")
        try:
            page.goto(self.url, wait_until="domcontentloaded", timeout=45_000)
            # 不强制等待 networkidle：图片/脚本加载慢会导致卡顿，domcontentloaded 已可操作输入框
        except Exception as exc:
            print(f"[{self.name}] 页面加载提示：{exc}（可能较慢，继续尝试定位输入框）")
        if not self._locate(self.selectors.get("input"), timeout=20_000):
            raise LoginRequired(
                f"[{self.name}] 未找到输入框，可能是未登录或页面改版。\n"
                f"请先运行 python run_eval.py --login 在浏览器中登录 {self.url}，再重新执行。"
            )
        print(f"[{self.name}] 健康检查通过（已定位输入框）")

    def infer(self, text: str, temperature: float | None = None) -> str:
        # 网页版无温度控制，参数仅用于保持统一接口
        self.health_check()
        self.new_chat()
        self._send_prompt(text)
        return self._wait_and_extract()

    def new_chat(self) -> None:
        bm = self._get_browser_manager()
        page = bm.page_for(self.name, self.url)
        click_selectors = self.selectors.get("new_chat") or []
        if isinstance(click_selectors, str):
            click_selectors = [click_selectors]
        for selector in click_selectors:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=3_000):
                    locator.click(timeout=3_000, no_wait_after=True)
                    time.sleep(1.0)
                    return
            except Exception:
                continue
        # 兜底：重新加载页面
        try:
            page.goto(self.url, wait_until="domcontentloaded", timeout=20_000)
        except Exception as exc:
            print(f"[{self.name}] 新会话兜底刷新页面失败：{exc}")

    # ---- 发送提示词 ----
    def _send_prompt(self, text: str) -> None:
        bm = self._get_browser_manager()
        page = bm.page_for(self.name, self.url)
        input_selector = self.selectors.get("input")
        locator = self._locate(input_selector)
        if locator is None:
            raise LoginRequired(f"[{self.name}] 发送前输入框不可用（可能已登出）")
        # no_wait_after：豆包等站点点击输入框会触发 SPA 跳转，Playwright 默认等导航完成易超时，
        # 我们只需点击行为本身，跳转由后续逻辑处理
        try:
            locator.click(timeout=5_000, no_wait_after=True)
        except Exception:
            pass
        try:
            locator.fill(text, timeout=5_000)
        except Exception:
            # contenteditable 等场景 fill 失败，改用慢速键盘输入
            delay = float(self.browser_cfg.get("input_delay_ms", 30)) / 1000.0
            for chunk in _chunks(text, int(self.browser_cfg.get("slow_input_chars", 200))):
                page.keyboard.type(chunk, delay=delay)
        time.sleep(0.3)
        send_selector = self.selectors.get("send")
        if send_selector:
            send = self._locate(send_selector)
            if send is None:
                raise SelectorError(f"[{self.name}] 发送按钮定位失败：{send_selector}")
            send.click(timeout=5_000, no_wait_after=True)
        else:
            page.keyboard.press("Enter")
        # 发送确认：千问等站点回车可能不触发发送（contenteditable 回车只是换行），
        # 若输入框未清空，则用相对定位自动点击输入框旁的发送图标兜底。
        time.sleep(1.2)
        if not self._is_input_cleared():
            print(f"[{self.name}] 回车未触发发送，尝试点击发送图标...")
            if self._click_send_fallback():
                time.sleep(0.5)
                if not self._is_input_cleared():
                    print(f"[{self.name}] 发送图标点击后输入框仍未清空，继续等待（可能已发送但保留输入）")
            else:
                print(f"[{self.name}] 未找到可用的发送图标，按原流程继续")
        # 确认已经进入生成状态：等待 stop 按钮出现或输入框被清空
        self._wait_generation_started()

    def _is_input_cleared(self) -> bool:
        """输入框是否已清空（多数站点发送成功后会清空输入框）。"""
        bm = self._get_browser_manager()
        page = bm.page_for(self.name, self.url)
        locator = self._locate(self.selectors.get("input"), timeout=1_500)
        if locator is None:
            return True  # 输入框消失通常意味着已发送（部分站点生成时隐藏输入框）
        try:
            return locator.input_value().strip() == ""
        except Exception:
            pass
        try:
            return locator.inner_text().strip() == ""
        except Exception:
            return False

    def _click_send_fallback(self) -> bool:
        """相对定位兜底：从输入框向上找最近的工具栏，点击其中最后一个无文本的图标按钮。

        适用于千问等使用 Tailwind 原子化类名、发送按钮无语义 class/aria 的站点。
        """
        bm = self._get_browser_manager()
        page = bm.page_for(self.name, self.url)
        try:
            return bool(page.evaluate("""() => {
                const ce = document.querySelector('[contenteditable="true"]') || document.querySelector('textarea');
                if (!ce) return false;
                let el = ce;
                for (let i = 0; i < 6 && el; i++) {
                    el = el.parentElement;
                    if (!el) break;
                    const cands = Array.from(el.querySelectorAll('button, [role="button"], [class*="cursor-pointer"]'))
                        .filter(b => {
                            const r = b.getBoundingClientRect();
                            return r.width > 0 && r.height > 0;
                        });
                    if (cands.length) {
                        const last = cands[cands.length - 1];
                        const t = (last.innerText || '').trim();
                        if (t && t.length > 1) continue;  // 跳过有文字的按钮（如"新对话"）
                        last.click();
                        return true;
                    }
                }
                return false;
            }"""))
        except Exception as exc:
            print(f"[{self.name}] 发送图标兜底点击失败：{exc}")
            return False

    def _wait_generation_started(self) -> None:
        bm = self._get_browser_manager()
        page = bm.page_for(self.name, self.url)
        stop_selector = self.selectors.get("stop")
        deadline = time.time() + 15
        while time.time() < deadline:
            if stop_selector and self._locate(stop_selector, timeout=1_000) is not None:
                return
            input_selector = self.selectors.get("input")
            if input_selector:
                locator = self._locate(input_selector, timeout=1_000)
                if locator is None:
                    return  # 输入框消失（部分站点生成时隐藏）
                try:
                    if not locator.input_value():
                        return  # 输入框已清空说明已发送
                except Exception:
                    pass
            time.sleep(0.5)

    # ---- 等待生成完成 ----
    def _wait_and_extract(self) -> str:
        """等待生成完成并提取回答。

        统一截止时间（self.timeout，默认 30 秒）内：
        - 停止按钮出现后消失 → 完成
        - 输出文本连续 stabilize_seconds 秒不变 → 完成
        - 超时 → 兜底返回已生成的部分内容（若可提取），否则抛 GenerationTimeout
        """
        deadline = time.time() + self.timeout
        done, how = self._wait_done(deadline)
        if not done:
            partial = self._safe_extract()
            if partial:
                print(f"[{self.name}] 生成超时（{self.timeout:.0f}s），返回已生成的部分内容（{len(partial)} 字符）")
                return partial
            raise GenerationTimeout(f"[{self.name}] 生成超时（>{self.timeout:.0f}s），且未提取到任何内容")
        text = self._extract_output()
        if any(keyword in text for keyword in RATE_LIMIT_KEYWORDS):
            raise RateLimited(f"[{self.name}] 检测到限流提示")
        if not text.strip():
            raise SelectorError(f"[{self.name}] 未提取到回答文本，请检查 output_selector 配置")
        return text

    def _wait_done(self, deadline: float) -> tuple[bool, str]:
        """统一截止时间内的完成判定。

        豆包等站点的"停止按钮"选择器往往匹配不到，此时自动落到
        "输出文本稳定"判定，互不干扰；两者都不满足则超时返回 False。
        """
        stop_selector = self.selectors.get("stop")
        stop_appeared = False
        last_text = ""
        last_change = 0.0
        while time.time() < deadline:
            if stop_selector:
                loc = self._locate(stop_selector, timeout=1_500)
                stop_visible = loc is not None and loc.is_visible()
                if stop_visible:
                    if not stop_appeared:
                        stop_appeared = True
                        last_change = time.time()
                elif stop_appeared:
                    return True, "stop_gone"
            text = self._safe_extract()
            if text:
                if text != last_text:
                    last_text = text
                    last_change = time.time()
                elif time.time() - last_change >= self.stabilize_seconds:
                    return True, "stable"
            time.sleep(0.8)
        return False, "timeout"

    # ---- 提取输出 ----
    def _extract_output(self) -> str:
        bm = self._get_browser_manager()
        page = bm.page_for(self.name, self.url)
        candidates = self.selectors.get("output_selector") or self.selectors.get("output_container") or []
        if isinstance(candidates, str):
            candidates = [candidates]
        best = ""
        for selector in candidates:
            try:
                locator = page.locator(selector).last
                if not locator.is_visible(timeout=2_000):
                    continue
                text = locator.inner_text(timeout=3_000)
                if len(text) > len(best):
                    best = text
            except Exception:
                continue
        if best and self.selectors.get("include_code", False):
            best = self._merge_code_blocks(best)
        return _clean_text(best)

    def _merge_code_blocks(self, text: str) -> str:
        return text

    def _safe_extract(self) -> str:
        """容错提取：任何异常都返回空串，不打断等待循环。"""
        try:
            return self._extract_output()
        except Exception:
            return ""

    # ---- 工具 ----
    def _locate(self, selectors: str | list[str] | None, timeout: float = 5_000):
        """按候选选择器列表逐一尝试定位第一个可见元素；返回 None 表示全部失败。"""
        if isinstance(selectors, str):
            selectors = [selectors]
        if not selectors:
            return None
        bm = self._get_browser_manager()
        page = bm.page_for(self.name, self.url)
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=timeout):
                    return locator
            except Exception:
                continue
        return None

    def _wait_until(self, predicate, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if predicate():
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False


def _chunks(text: str, size: int):
    for i in range(0, len(text), size):
        yield text[i : i + size]


_CITATION_LINE = re.compile(r"^-?\d+$")


def _clean_text(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    out: list[str] = []
    blank = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        # 过滤 inner_text 混入的独立引用索引行（如 "-1"、"-3"、"11"）
        if _CITATION_LINE.match(stripped):
            continue
        out.append(line)
    return "\n".join(out).strip()
