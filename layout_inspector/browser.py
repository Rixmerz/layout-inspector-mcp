"""Browser lifecycle: one reusable Chromium, bounded concurrency, real errors."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal, cast

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)

log = logging.getLogger("layout_inspector")

DEFAULT_NAV_TIMEOUT_MS = 15_000
DEFAULT_WAIT_TIMEOUT_MS = 5_000
DEFAULT_IDLE_SHUTDOWN_S = 300.0
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


class BrowserUnavailable(RuntimeError):
    """Chromium could not be launched. Carries the operator's next step."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def chromium_executable() -> str | None:
    """Explicit Chromium path, if the operator pinned one.

    Playwright refuses to launch a browser build that does not match the wheel
    it ships with. Pinning a path is the escape hatch when the environment
    already has a working Chromium.
    """
    for var in ("LAYOUT_INSPECTOR_CHROMIUM", "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"):
        value = os.environ.get(var)
        if value:
            return value
    return None


def _launch_args() -> list[str]:
    args = ["--disable-gpu", "--disable-dev-shm-usage", "--hide-scrollbars=false"]
    # --no-sandbox is a real weakening of the browser sandbox. Only used where
    # the sandbox cannot work anyway (running as root, or in a container that
    # forbids user namespaces), or when explicitly asked for.
    if _env_flag("LAYOUT_INSPECTOR_NO_SANDBOX") or (hasattr(os, "geteuid") and os.geteuid() == 0):
        args.append("--no-sandbox")
    return args


@dataclass
class PageDiagnostics:
    """What the browser reported while loading — not just the geometry."""

    url: str
    status: int | None = None
    ok: bool = True
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out: dict = {"url": self.url}
        if self.status is not None:
            out["http_status"] = self.status
        if not self.ok:
            out["http_ok"] = False
        for key, value in (
            ("console_errors", self.console_errors),
            ("page_errors", self.page_errors),
            ("failed_requests", self.failed_requests),
            ("notes", self.notes),
        ):
            if value:
                out[key] = value[:10]
        return out


class BrowserPool:
    """Keeps one Chromium alive across calls and caps concurrent pages."""

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max(1, _env_int("LAYOUT_INSPECTOR_MAX_CONCURRENCY", 4)))
        self._idle_after = float(
            os.environ.get("LAYOUT_INSPECTOR_IDLE_SHUTDOWN", DEFAULT_IDLE_SHUTDOWN_S)
            or DEFAULT_IDLE_SHUTDOWN_S
        )
        self._idle_task: asyncio.Task | None = None
        self._in_flight = 0

    # ── lifecycle ────────────────────────────────────────────────
    async def get_browser(self) -> Browser:
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            if self._pw is None:
                self._pw = await async_playwright().start()
            kwargs: dict = {"headless": True, "args": _launch_args()}
            executable = chromium_executable()
            if executable:
                kwargs["executable_path"] = executable
            try:
                self._browser = await self._pw.chromium.launch(**kwargs)
            except PlaywrightError as exc:
                raise BrowserUnavailable(_launch_help(exc)) from exc
            log.info("chromium launched (executable=%s)", executable or "bundled")
            return self._browser

    async def shutdown(self) -> None:
        async with self._lock:
            if self._idle_task:
                self._idle_task.cancel()
                self._idle_task = None
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    log.debug("browser close failed", exc_info=True)
            if self._pw:
                try:
                    await self._pw.stop()
                except Exception:
                    log.debug("playwright stop failed", exc_info=True)
            self._browser = None
            self._pw = None

    def _schedule_idle_shutdown(self) -> None:
        if self._idle_after <= 0:
            return
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()

        async def _close_when_idle() -> None:
            try:
                await asyncio.sleep(self._idle_after)
            except asyncio.CancelledError:
                return
            if self._in_flight == 0:
                log.info("closing idle chromium after %.0fs", self._idle_after)
                await self.shutdown()

        self._idle_task = asyncio.ensure_future(_close_when_idle())

    # ── contexts and pages ───────────────────────────────────────
    async def _new_context(
        self, width: int, height: int, touch: bool, mobile: bool
    ) -> BrowserContext:
        browser = await self.get_browser()
        options: dict = {
            "viewport": {"width": width, "height": height},
            "device_scale_factor": 2 if mobile else 1,
            # is_mobile switches Chromium to a phone's *layout* viewport, which
            # on a page with no <meta name="viewport"> is ~980px wide however
            # narrow the window is. That is faithful phone behaviour but it
            # breaks the contract "measure at this width", so it stays opt-in.
            "is_mobile": mobile,
            # has_touch alone is what makes pointer:coarse and hover:none
            # media queries apply, without touching layout.
            "has_touch": touch or mobile,
            # Entrance animations otherwise get measured mid-flight.
            "reduced_motion": "reduce",
        }
        if mobile:
            options["user_agent"] = MOBILE_UA
        headers = os.environ.get("LAYOUT_INSPECTOR_HTTP_HEADERS")
        if headers:
            try:
                options["extra_http_headers"] = json.loads(headers)
            except json.JSONDecodeError:
                log.warning("LAYOUT_INSPECTOR_HTTP_HEADERS is not valid JSON; ignored")
        basic = os.environ.get("LAYOUT_INSPECTOR_BASIC_AUTH")
        if basic and ":" in basic:
            user, _, password = basic.partition(":")
            options["http_credentials"] = {"username": user, "password": password}
        state = os.environ.get("LAYOUT_INSPECTOR_STORAGE_STATE")
        if state and os.path.exists(state):
            options["storage_state"] = state
        return await browser.new_context(**options)

    @asynccontextmanager
    async def page(
        self,
        url: str,
        *,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        wait_for: str | None = None,
        wait_until: str = "networkidle",
        timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
        wait_for_timeout_ms: int = DEFAULT_WAIT_TIMEOUT_MS,
        mobile: bool | None = None,
        full_page: bool = False,
        scroll_y: int = 0,
    ) -> AsyncIterator[tuple[Page, PageDiagnostics]]:
        """Open `url`, settle it, and hand back the page plus what went wrong."""
        # Narrow viewports get touch input semantics by default; full phone
        # emulation, which also rewrites the layout viewport, must be asked for.
        touch = viewport_width <= 480 if mobile is None else bool(mobile)
        mobile = bool(mobile)
        diagnostics = PageDiagnostics(url=url)

        async with self._semaphore:
            self._in_flight += 1
            if self._idle_task and not self._idle_task.done():
                self._idle_task.cancel()
            context = await self._new_context(viewport_width, viewport_height, touch, mobile)
            page = await context.new_page()
            page.on(
                "console",
                lambda msg: (
                    diagnostics.console_errors.append(f"{msg.type}: {msg.text}")
                    if msg.type == "error"
                    else None
                ),
            )
            page.on("pageerror", lambda exc: diagnostics.page_errors.append(str(exc)))
            page.on(
                "requestfailed",
                lambda req: diagnostics.failed_requests.append(
                    f"{req.method} {req.url} — {(req.failure or '')}"
                ),
            )
            try:
                try:
                    response = await page.goto(
                        url,
                        wait_until=cast(
                            Literal["commit", "domcontentloaded", "load", "networkidle"],
                            wait_until,
                        ),
                        timeout=timeout_ms,
                    )
                except PlaywrightError as exc:
                    if "Timeout" in str(exc) and wait_until == "networkidle":
                        # A page with a websocket, poller or analytics beacon
                        # never goes idle. Measuring the loaded DOM beats
                        # failing outright, as long as we say so.
                        diagnostics.notes.append(
                            f"networkidle never reached within {timeout_ms}ms; "
                            "measured after 'load' instead. Pass wait_until='load' "
                            "to skip this wait."
                        )
                        response = await page.goto(url, wait_until="load", timeout=timeout_ms)
                    else:
                        raise
                if response is not None:
                    diagnostics.status = response.status
                    diagnostics.ok = response.ok
                    if not response.ok:
                        diagnostics.notes.append(
                            f"Server returned HTTP {response.status}; the measured "
                            "page may be an error page."
                        )

                if wait_for:
                    try:
                        await page.wait_for_selector(wait_for, timeout=wait_for_timeout_ms)
                    except PlaywrightError:
                        diagnostics.notes.append(
                            f"wait_for selector {wait_for!r} never appeared within "
                            f"{wait_for_timeout_ms}ms; measured without it."
                        )

                if full_page:
                    await _scroll_through(page)
                if scroll_y:
                    await page.evaluate("(y) => window.scrollTo(0, y)", scroll_y)
                    await page.wait_for_timeout(150)

                # Web fonts change every text metric. Measuring before they land
                # produces rects that do not match what anyone sees.
                try:
                    await page.evaluate(
                        "() => document.fonts ? document.fonts.ready.then(() => true) : true"
                    )
                except PlaywrightError:
                    diagnostics.notes.append("document.fonts.ready was not awaited")

                yield page, diagnostics
            finally:
                self._in_flight -= 1
                try:
                    await context.close()
                except Exception:
                    log.debug("context close failed", exc_info=True)
                self._schedule_idle_shutdown()


async def _scroll_through(page: Page) -> None:
    """Walk the page top to bottom to trigger lazy loading, then return to 0."""
    await page.evaluate(
        """async () => {
            const step = Math.max(200, window.innerHeight * 0.8);
            const limit = document.documentElement.scrollHeight;
            for (let y = 0; y < limit; y += step) {
                window.scrollTo(0, y);
                await new Promise(r => setTimeout(r, 60));
            }
            window.scrollTo(0, 0);
            await new Promise(r => setTimeout(r, 120));
        }"""
    )


def _launch_help(exc: Exception) -> str:
    message = str(exc)
    if "Executable doesn't exist" in message or "playwright install" in message:
        return (
            "Chromium is not installed for this Playwright build. Install it into "
            "the same environment that runs the server:\n"
            "    uv run --project <plugin-or-repo-root> playwright install chromium\n"
            "A `playwright` binary from a different environment installs a build "
            "this one will reject. To reuse a Chromium you already have, set "
            "LAYOUT_INSPECTOR_CHROMIUM to its executable path.\n\n"
            f"Playwright said: {message}"
        )
    return f"Chromium could not be launched: {message}"


pool = BrowserPool()
