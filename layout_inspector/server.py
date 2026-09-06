"""Layout Inspector MCP — spatial inspection of rendered web pages.

Drives headless Chromium, extracts computed layout geometry, and reports
measured layout defects: overlaps, clipping, horizontal scroll, truncation,
occlusion, trapped z-indexes and target-size failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from playwright.async_api import Error as PlaywrightError

from . import __version__, scripts
from .browser import (
    DEFAULT_NAV_TIMEOUT_MS,
    BrowserUnavailable,
    chromium_executable,
    pool,
)
from .issues import detect_layout_issues, diff_viewport_issues

log = logging.getLogger("layout_inspector")

DEFAULT_VIEWPORTS = ["375x667", "768x1024", "1280x720", "1920x1080"]
DEFAULT_VIEWPORTS_CSV = ",".join(DEFAULT_VIEWPORTS)
WAIT_UNTIL_VALUES = ("load", "domcontentloaded", "networkidle", "commit")
MAX_VIEWPORTS = 8
# Above this, pretty-printing costs more tokens than the readability buys.
COMPACT_THRESHOLD = 20_000
# Fields the caller rarely needs; dropped unless verbose.
VERBOSE_ONLY_FIELDS = ("depth", "classes", "id", "overflow", "childrenCount")
# Fields are omitted when they hold these values, to keep large trees cheap.
ELEMENT_DEFAULTS = {
    "zIndex": "auto",
    "effectiveZIndex": "same as zIndex",
    "computedPosition": "static",
    "visible": True,
    "hasText": False,
    "visibleRect": "same as rect (nothing clips it)",
}


# ── Serialisation ────────────────────────────────────────────────


def _dump(payload: Any) -> str:
    """JSON, pretty when small and compact when the payload is large."""
    pretty = json.dumps(payload, indent=2, ensure_ascii=False)
    if len(pretty) <= COMPACT_THRESHOLD:
        return pretty
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _fail(message: str) -> ToolError:
    return ToolError(message)


def _check_script_result(result: Any, *, what: str) -> dict:
    """Turn a script-side error marker into an actionable tool error."""
    if not isinstance(result, dict):
        raise _fail(f"{what} returned an unexpected result: {result!r}")
    error = result.get("error")
    if error == "invalid_selector":
        raise _fail(
            f"{result.get('selector')!r} is not a valid CSS selector. "
            "Class names containing ':' or '/' (Tailwind) must be escaped, e.g. "
            r"'.md\:flex'. The browser said: " + str(result.get("message", ""))
        )
    if error == "root_not_found":
        raise _fail(
            f"No element matches root_selector {result.get('selector')!r} on this "
            "page. Nothing was measured. Check the selector, or drop it to scan "
            "the whole body. If the content renders late, pass wait_for."
        )
    if error == "not_found":
        raise _fail(
            f"No element matches {result.get('selector')!r} on this page. "
            "Reported selectors are built from ids and classes; run "
            "inspect_layout and copy the selector field verbatim."
        )
    if error == "no_body":
        raise _fail("The page has no <body> to measure.")
    if error:
        raise _fail(f"{what} failed: {error}")
    return result


def _lean(elements: list[dict], verbose: bool) -> list[dict]:
    if verbose:
        return elements
    return [{k: v for k, v in el.items() if k not in VERBOSE_ONLY_FIELDS} for el in elements]


def _parse_viewports(viewports: str | Sequence[str]) -> list[tuple[int, int]]:
    raw = viewports.split(",") if isinstance(viewports, str) else list(viewports)

    pairs: list[tuple[int, int]] = []
    bad: list[str] = []
    for item in raw:
        item = str(item).strip().lower()
        if not item:
            continue
        width, sep, height = item.partition("x")
        if not sep:
            bad.append(item)
            continue
        try:
            w, h = int(width), int(height)
        except ValueError:
            bad.append(item)
            continue
        if not (0 < w <= 10000 and 0 < h <= 10000):
            bad.append(item)
            continue
        if (w, h) not in pairs:
            pairs.append((w, h))

    if bad:
        raise _fail(
            f"Invalid viewport(s): {', '.join(bad)}. Expected WxH pairs of positive "
            "integers up to 10000, e.g. '375x667,1280x720'."
        )
    if not pairs:
        raise _fail("No viewports given. Pass at least one WxH pair, e.g. '375x667'.")
    if len(pairs) > MAX_VIEWPORTS:
        raise _fail(
            f"{len(pairs)} viewports requested; the cap is {MAX_VIEWPORTS} per call. "
            "Split the list across calls."
        )
    return pairs


def _validate_wait_until(value: str) -> str:
    if value not in WAIT_UNTIL_VALUES:
        raise _fail(
            f"wait_until={value!r} is not supported. Use one of: "
            + ", ".join(WAIT_UNTIL_VALUES)
            + ". 'networkidle' is the default; 'load' is the escape hatch for a "
            "page with a websocket or poller that never goes idle."
        )
    return value


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise _fail("url is required.")
    lowered = url.lower()
    if not lowered.startswith(("http://", "https://", "file://", "about:blank")):
        raise _fail(
            f"{url!r} is not a URL this server can open. Use an http(s):// address "
            "or a file:// path (absolute, e.g. file:///home/me/site/index.html)."
        )
    return url


@asynccontextmanager
async def _measure(url: str, **kwargs):
    """Open a page, translating browser failures into actionable tool errors."""
    url = _validate_url(url)
    if "wait_until" in kwargs:
        kwargs["wait_until"] = _validate_wait_until(kwargs["wait_until"])
    try:
        async with pool.page(url, **kwargs) as (page, diagnostics):
            yield page, diagnostics
    except BrowserUnavailable as exc:
        raise _fail(str(exc)) from exc
    except PlaywrightError as exc:
        message = str(exc)
        if "net::ERR_CONNECTION_REFUSED" in message:
            raise _fail(
                f"Nothing is listening at {url}. Start the dev server first, or "
                "point at a static file:// build."
            ) from exc
        if "net::ERR_FILE_NOT_FOUND" in message:
            raise _fail(f"No such file: {url}") from exc
        if "Timeout" in message:
            raise _fail(
                f"{url} did not finish loading in time. Raise timeout_ms, or pass "
                "wait_until='load' or 'domcontentloaded' for a page that never "
                "goes network-idle."
            ) from exc
        raise _fail(f"Could not load {url}: {message}") from exc


# ── Server ───────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await pool.shutdown()


mcp = FastMCP(
    "Layout Inspector",
    version=__version__,
    instructions=(
        "Measures rendered web page layouts in headless Chromium and reports "
        "geometry, not impressions. 'detect_issues' is the default entry point: "
        "overlaps, horizontal scroll, clipping, truncated text, occluded content "
        "and trapped z-indexes, each with severity and pixel detail. "
        "'inspect_layout' returns the raw computed layout tree, 'element_context' "
        "root-causes one element (stacking and clipping chains), "
        "'compare_viewports' diffs findings across breakpoints, "
        "'accessibility_spatial' checks WCAG target sizes and covered controls, "
        "and 'check_environment' verifies the browser is installed. "
        "Coordinates are viewport-relative CSS pixels at the requested scroll "
        "position. Chromium only."
    ),
    lifespan=lifespan,
)


@mcp.tool()
async def check_environment() -> str:
    """Verifies that the headless browser this server needs is actually installed.

    Call this first when any other tool fails to launch a browser. Reports the
    Playwright version, which Chromium build it expects, whether that build is
    present, and the exact command to install it.
    """
    import playwright  # local import: only needed for the version string

    info: dict[str, Any] = {
        "server_version": __version__,
        "playwright_version": getattr(playwright, "__version__", "unknown"),
        "chromium_executable_override": chromium_executable(),
        "playwright_browsers_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
        "playwright_cli_on_path": shutil.which("playwright"),
    }
    try:
        browser = await pool.get_browser()
        info["browser_ready"] = True
        info["browser_version"] = browser.version
    except BrowserUnavailable as exc:
        info["browser_ready"] = False
        info["remedy"] = str(exc)
    return _dump(info)


@mcp.tool()
async def inspect_layout(
    url: str,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    root_selector: str | None = None,
    wait_for: str | None = None,
    max_elements: int = 500,
    verbose: bool = False,
    wait_until: str = "networkidle",
    timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    mobile: bool | None = None,
    full_page: bool = False,
    scroll_y: int = 0,
) -> str:
    """Extracts the computed layout tree of a web page.

    Returns every rendered element with its bounding rect (x, y, w, h), the part
    of it that survives clipping ancestors, own and inherited z-index, position,
    overflow, visibility and truncation. Also reports whether the document
    scrolls horizontally and which elements make it do so, scanned across the
    whole page rather than only the returned elements.

    Args:
        url: Page URL or file:// path to inspect.
        viewport_width: Viewport width in CSS px.
        viewport_height: Viewport height in CSS px.
        root_selector: CSS selector to scope the scan to a subtree. Errors if it matches nothing.
        wait_for: CSS selector to wait for before measuring.
        max_elements: Cap on returned elements, depth-first (default 500).
        verbose: Include depth, classes, id, overflow and child counts per element.
        wait_until: Navigation wait: 'networkidle', 'load' or 'domcontentloaded'.
        timeout_ms: Navigation timeout in milliseconds.
        mobile: Full phone emulation (device pixel ratio, mobile UA, and the phone layout viewport). Off by default; widths <= 480 already get touch input semantics.
        full_page: Scroll the page once to trigger lazy loading, then measure at the top.
        scroll_y: Measure after scrolling to this vertical offset, for sticky behaviour.
    """
    async with _measure(
        url,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        wait_for=wait_for,
        wait_until=wait_until,
        timeout_ms=timeout_ms,
        mobile=mobile,
        full_page=full_page,
        scroll_y=scroll_y,
    ) as (page, diagnostics):
        raw = await page.evaluate(
            scripts.load(scripts.EXTRACT_LAYOUT),
            {
                "selector": root_selector,
                "maxElements": max(1, min(int(max_elements), 5000)),
                "occlusion": True,
            },
        )
        data = _check_script_result(raw, what="inspect_layout")

    elements = _lean(data["elements"], verbose)
    payload = {
        "page": diagnostics.to_dict(),
        "viewport": data["viewport"],
        "total_elements": len(elements),
        "element_cap_reached": data.get("truncated", False),
        "page_scrolls_horizontally": data.get("page_scrolls_horizontally", False),
        "has_viewport_meta": data.get("has_viewport_meta"),
        "overflow_culprits": data.get("overflow_culprits", []),
        "omitted_field_defaults": ELEMENT_DEFAULTS,
        "elements": elements,
    }
    if data.get("truncated"):
        payload["note"] = (
            f"The scan stopped at {data.get('max_elements')} elements, so the tail of "
            "the page was not measured. Raise max_elements or scope with root_selector."
        )
    return _dump(payload)


@mcp.tool()
async def detect_issues(
    url: str,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    root_selector: str | None = None,
    wait_for: str | None = None,
    max_elements: int = 500,
    max_issues: int = 250,
    wait_until: str = "networkidle",
    timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    mobile: bool | None = None,
    full_page: bool = False,
    scroll_y: int = 0,
) -> str:
    """Scans a page for layout defects and returns them worst-first.

    Detects: horizontal page scroll (with the widest culprits), elements past
    the viewport edges, content hidden by an ancestor's overflow, text truncated
    by ellipsis or nowrap, content painted over by another element, z-indexes
    trapped by an ancestor stacking context, and pairwise overlaps.

    Severity encodes intent: `error` is unambiguous, `warning` is stacking or
    overflow nobody asked for, `info` is deliberate stacking that may still be
    wrong. Nesting is never reported.

    Args:
        url: Page URL or file:// path to inspect.
        viewport_width: Viewport width in CSS px.
        viewport_height: Viewport height in CSS px.
        root_selector: CSS selector to scope the scan to a subtree.
        wait_for: CSS selector to wait for before measuring.
        max_elements: Cap on scanned elements, depth-first (default 500).
        max_issues: Cap on returned issues (default 250).
        wait_until: Navigation wait: 'networkidle', 'load' or 'domcontentloaded'.
        timeout_ms: Navigation timeout in milliseconds.
        mobile: Full phone emulation (device pixel ratio, mobile UA, and the phone layout viewport). Off by default; widths <= 480 already get touch input semantics.
        full_page: Scroll the page once to trigger lazy loading, then measure at the top.
        scroll_y: Measure after scrolling to this vertical offset.
    """
    async with _measure(
        url,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        wait_for=wait_for,
        wait_until=wait_until,
        timeout_ms=timeout_ms,
        mobile=mobile,
        full_page=full_page,
        scroll_y=scroll_y,
    ) as (page, diagnostics):
        raw = await page.evaluate(
            scripts.load(scripts.EXTRACT_LAYOUT),
            {
                "selector": root_selector,
                "maxElements": max(1, min(int(max_elements), 5000)),
                "occlusion": True,
            },
        )
        data = _check_script_result(raw, what="detect_issues")

    issues = detect_layout_issues(
        data["elements"],
        data["viewport"],
        page_scrolls_horizontally=data.get("page_scrolls_horizontally"),
        overflow_culprits=data.get("overflow_culprits"),
        has_viewport_meta=data.get("has_viewport_meta"),
        max_issues=max_issues,
    )
    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        counts[issue["severity"]] = counts.get(issue["severity"], 0) + 1

    payload = {
        "page": diagnostics.to_dict(),
        "viewport": data["viewport"],
        "total_elements": len(data["elements"]),
        "element_cap_reached": data.get("truncated", False),
        "issues_count": len(issues),
        "by_severity": counts,
        "issues": issues,
    }
    if data.get("truncated"):
        payload["note"] = (
            f"The scan stopped at {data.get('max_elements')} elements. Findings past "
            "that point are missing; raise max_elements or scope with root_selector "
            "before calling the page clean."
        )
    return _dump(payload)


@mcp.tool()
async def element_context(
    url: str,
    selector: str,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    wait_for: str | None = None,
    wait_until: str = "networkidle",
    timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    mobile: bool | None = None,
    scroll_y: int = 0,
) -> str:
    """Root-causes one element: why it sits where it sits.

    Returns its computed box and the styles that decide layout (sizing, flex,
    grid, wrapping, transforms), its parent's layout styles, sibling rects, the
    full chain of ancestors that create a stacking context and the property
    responsible for each, every ancestor that clips it, and what actually paints
    over its centre.

    Args:
        url: Page URL or file:// path.
        selector: CSS selector of the target element. The first match is used.
        viewport_width: Viewport width in CSS px.
        viewport_height: Viewport height in CSS px.
        wait_for: CSS selector to wait for before measuring.
        wait_until: Navigation wait: 'networkidle', 'load' or 'domcontentloaded'.
        timeout_ms: Navigation timeout in milliseconds.
        mobile: Full phone emulation (device pixel ratio, mobile UA, and the phone layout viewport). Off by default; widths <= 480 already get touch input semantics.
        scroll_y: Measure after scrolling to this vertical offset.
    """
    if not selector or not selector.strip():
        raise _fail("selector is required.")
    async with _measure(
        url,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        wait_for=wait_for,
        wait_until=wait_until,
        timeout_ms=timeout_ms,
        mobile=mobile,
        scroll_y=scroll_y,
    ) as (page, diagnostics):
        raw = await page.evaluate(scripts.load(scripts.ELEMENT_CONTEXT), {"selector": selector})
        data = _check_script_result(raw, what="element_context")

    data["page"] = diagnostics.to_dict()
    if data.get("matched", 1) > 1:
        data["note"] = (
            f"{data['matched']} elements match {selector!r}; the first is reported. "
            "Use the element.selector field for an unambiguous one."
        )
    return _dump(data)


@mcp.tool()
async def compare_viewports(
    url: str,
    viewports: str | list[str] = DEFAULT_VIEWPORTS_CSV,
    root_selector: str | None = None,
    wait_for: str | None = None,
    max_elements: int = 500,
    wait_until: str = "networkidle",
    timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    include_details: bool = False,
) -> str:
    """Compares layout across viewport sizes and names the responsive regressions.

    Measures every viewport in parallel and diffs the findings: which issues
    appear at only some breakpoints (the responsive bug) and which hold at every
    size (a general defect). Widths of 480px or less get touch/mobile emulation,
    so `pointer: coarse` media queries apply.

    Args:
        url: Page URL or file:// path.
        viewports: WxH sizes, comma-separated or as a list (default: 375x667, 768x1024, 1280x720, 1920x1080).
        root_selector: CSS selector to scope the scan to a subtree.
        wait_for: CSS selector to wait for before measuring.
        max_elements: Cap on scanned elements per viewport.
        wait_until: Navigation wait: 'networkidle', 'load' or 'domcontentloaded'.
        timeout_ms: Navigation timeout in milliseconds.
        include_details: Include the full per-viewport issue lists, not just the diff.
    """
    pairs = _parse_viewports(viewports)
    script = scripts.load(scripts.EXTRACT_LAYOUT)

    async def measure(width: int, height: int) -> dict:
        async with _measure(
            url,
            viewport_width=width,
            viewport_height=height,
            wait_for=wait_for,
            wait_until=wait_until,
            timeout_ms=timeout_ms,
        ) as (page, diagnostics):
            raw = await page.evaluate(
                script,
                {
                    "selector": root_selector,
                    "maxElements": max(1, min(int(max_elements), 5000)),
                    "occlusion": True,
                },
            )
            data = _check_script_result(raw, what="compare_viewports")
        issues = detect_layout_issues(
            data["elements"],
            data["viewport"],
            page_scrolls_horizontally=data.get("page_scrolls_horizontally"),
            overflow_culprits=data.get("overflow_culprits"),
            has_viewport_meta=data.get("has_viewport_meta"),
        )
        counts = {"error": 0, "warning": 0, "info": 0}
        for issue in issues:
            counts[issue["severity"]] = counts.get(issue["severity"], 0) + 1
        return {
            "viewport": f"{width}x{height}",
            "page": diagnostics.to_dict(),
            "total_elements": len(data["elements"]),
            "element_cap_reached": data.get("truncated", False),
            "issues_count": len(issues),
            "by_severity": counts,
            "issues": issues,
        }

    results = await asyncio.gather(*(measure(w, h) for w, h in pairs))
    results = list(results)

    payload: dict[str, Any] = {
        "url": url,
        "summary": {
            r["viewport"]: {
                "total_issues": r["issues_count"],
                "by_severity": r["by_severity"],
            }
            for r in results
        },
        "diff": diff_viewport_issues(results),
    }
    if include_details:
        payload["details"] = results
    else:
        payload["note"] = (
            "Per-viewport issue lists omitted. Pass include_details=true for them, "
            "or call detect_issues at the viewport you care about."
        )
    return _dump(payload)


@mcp.tool()
async def accessibility_spatial(
    url: str,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    root_selector: str | None = None,
    wait_for: str | None = None,
    wait_until: str = "networkidle",
    timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    mobile: bool | None = None,
) -> str:
    """Checks spatial accessibility: target sizes and covered controls.

    Applies WCAG 2.2 SC 2.5.8 Target Size (Minimum, level AA, 24x24 CSS px) with
    its inline-in-text and 24px-spacing exceptions, and reports the stricter SC
    2.5.5 (Enhanced, AAA, 44x44) separately as info. Disabled, inert,
    pointer-events:none and screen-reader-only controls are excluded. Also
    reports interactive elements painted over by something else.

    Args:
        url: Page URL or file:// path.
        viewport_width: Viewport width in CSS px.
        viewport_height: Viewport height in CSS px.
        root_selector: CSS selector to scope the audit to a subtree.
        wait_for: CSS selector to wait for before measuring.
        wait_until: Navigation wait: 'networkidle', 'load' or 'domcontentloaded'.
        timeout_ms: Navigation timeout in milliseconds.
        mobile: Full phone emulation (device pixel ratio, mobile UA, and the phone layout viewport). Off by default; widths <= 480 already get touch input semantics.
    """
    async with _measure(
        url,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        wait_for=wait_for,
        wait_until=wait_until,
        timeout_ms=timeout_ms,
        mobile=mobile,
    ) as (page, diagnostics):
        raw = await page.evaluate(scripts.load(scripts.ACCESSIBILITY), {"selector": root_selector})
        data = _check_script_result(raw, what="accessibility_spatial")

    data["page"] = diagnostics.to_dict()
    data["viewport"] = {"w": viewport_width, "h": viewport_height}
    return _dump(data)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LAYOUT_INSPECTOR_LOG_LEVEL", "WARNING").upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )
    mcp.run()


if __name__ == "__main__":
    main()
