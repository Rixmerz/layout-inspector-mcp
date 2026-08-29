"""
Layout Inspector MCP — Inspección espacial de páginas web para agentes de IA.

Usa un headless browser (Playwright) para extraer el layout computado
de una página y devolver datos geométricos estructurados: bounding rects,
z-index, overlaps, overflow, y problemas de layout detectados.
"""

import asyncio
import json
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, asdict
from typing import Optional

from fastmcp import FastMCP, Context
from playwright.async_api import async_playwright, Browser, Page, Playwright

# ── Types ────────────────────────────────────────────────────

@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )

    def intersection_area(self, other: "Rect") -> float:
        if not self.intersects(other):
            return 0.0
        ix = max(self.x, other.x)
        iy = max(self.y, other.y)
        iw = min(self.right, other.right) - ix
        ih = min(self.bottom, other.bottom) - iy
        return iw * ih


@dataclass
class ElementInfo:
    selector: str
    tag: str
    rect: dict
    z_index: int | str
    overflow: str
    visible: bool
    text_truncated: bool
    computed_position: str
    children_count: int
    classes: str
    id: str


@dataclass
class LayoutIssue:
    type: str
    severity: str  # "error", "warning", "info"
    element: str
    description: str
    details: dict = field(default_factory=dict)


# ── Browser pool ─────────────────────────────────────────────

class BrowserPool:
    """Mantiene un browser Playwright reutilizable."""

    def __init__(self):
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()

    async def get_browser(self) -> Browser:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                if self._pw is None:
                    self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                    ],
                )
            return self._browser

    async def new_page(
        self, viewport_w: int = 1280, viewport_h: int = 720
    ) -> Page:
        browser = await self.get_browser()
        ctx = await browser.new_context(
            viewport={"width": viewport_w, "height": viewport_h},
            device_scale_factor=1,
        )
        return await ctx.new_page()

    async def shutdown(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._browser = None
        self._pw = None


pool = BrowserPool()

# ── JS helpers (inyectados en la página) ─────────────────────

JS_EXTRACT_LAYOUT = """
(selector) => {
    const root = selector
        ? document.querySelector(selector)
        : document.body;
    if (!root) return { elements: [], viewport: {} };

    const vp = {
        w: window.innerWidth,
        h: window.innerHeight,
        scrollW: document.documentElement.scrollWidth,
        scrollH: document.documentElement.scrollHeight,
    };

    function getSelector(el) {
        if (el.id) return '#' + CSS.escape(el.id);
        let path = el.tagName.toLowerCase();
        if (el.className && typeof el.className === 'string') {
            const cls = el.className.trim().split(/\\s+/).slice(0, 2).join('.');
            if (cls) path += '.' + cls;
        }
        // disambiguate among siblings
        const parent = el.parentElement;
        if (parent) {
            const siblings = Array.from(parent.children).filter(
                c => c.tagName === el.tagName
            );
            if (siblings.length > 1) {
                const idx = siblings.indexOf(el) + 1;
                path += ':nth-of-type(' + idx + ')';
            }
        }
        return path;
    }

    function isTextTruncated(el, style) {
        if (style.overflow === 'hidden' && style.textOverflow === 'ellipsis')
            return true;
        if (style.whiteSpace === 'nowrap' && el.scrollWidth > el.clientWidth)
            return true;
        return false;
    }

    const elements = [];
    const MAX_ELEMENTS = 500;

    function walk(el, depth, parentIdx) {
        if (elements.length >= MAX_ELEMENTS) return;
        if (el.nodeType !== 1) return;
        const tag = el.tagName.toLowerCase();
        // skip invisible meta elements
        if (['script','style','link','meta','head','noscript'].includes(tag)) return;

        const rect = el.getBoundingClientRect();
        // skip zero-size elements unless they have positioned children
        if (rect.width === 0 && rect.height === 0) return;

        const style = window.getComputedStyle(el);

        // Index of this element and of its parent, so overlap detection can tell
        // nesting (a child inside its container) from a real collision.
        const myIdx = elements.length;

        elements.push({
            index: myIdx,
            parent: parentIdx,
            selector: getSelector(el),
            tag: tag,
            rect: {
                x: Math.round(rect.x * 100) / 100,
                y: Math.round(rect.y * 100) / 100,
                w: Math.round(rect.width * 100) / 100,
                h: Math.round(rect.height * 100) / 100,
            },
            zIndex: style.zIndex === 'auto' ? 'auto' : parseInt(style.zIndex) || 0,
            overflow: style.overflow,
            visible: style.display !== 'none'
                && style.visibility !== 'hidden'
                && parseFloat(style.opacity) > 0,
            textTruncated: isTextTruncated(el, style),
            computedPosition: style.position,
            childrenCount: el.children.length,
            classes: el.className && typeof el.className === 'string'
                ? el.className.trim() : '',
            id: el.id || '',
            depth: depth,
        });

        for (const child of el.children) {
            walk(child, depth + 1, myIdx);
        }
    }

    walk(root, 0, -1);
    return { elements, viewport: vp };
}
"""

JS_ELEMENT_CONTEXT = """
(selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;

    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    const parent = el.parentElement;

    // gather siblings
    const siblings = parent
        ? Array.from(parent.children)
            .filter(c => c !== el && c.nodeType === 1)
            .slice(0, 10)
            .map(c => {
                const r = c.getBoundingClientRect();
                return {
                    selector: c.id ? '#' + CSS.escape(c.id) : c.tagName.toLowerCase(),
                    rect: {
                        x: Math.round(r.x * 100) / 100,
                        y: Math.round(r.y * 100) / 100,
                        w: Math.round(r.width * 100) / 100,
                        h: Math.round(r.height * 100) / 100,
                    },
                };
            })
        : [];

    // stacking context
    const stackingContext = [];
    let ancestor = el;
    while (ancestor && ancestor !== document.documentElement) {
        const s = window.getComputedStyle(ancestor);
        if (
            s.zIndex !== 'auto' ||
            s.position === 'fixed' ||
            s.position === 'sticky' ||
            parseFloat(s.opacity) < 1 ||
            s.transform !== 'none' ||
            s.filter !== 'none'
        ) {
            stackingContext.push({
                tag: ancestor.tagName.toLowerCase(),
                id: ancestor.id || null,
                zIndex: s.zIndex,
                position: s.position,
            });
        }
        ancestor = ancestor.parentElement;
    }

    return {
        element: {
            tag: el.tagName.toLowerCase(),
            id: el.id || '',
            classes: typeof el.className === 'string' ? el.className.trim() : '',
            rect: {
                x: Math.round(rect.x * 100) / 100,
                y: Math.round(rect.y * 100) / 100,
                w: Math.round(rect.width * 100) / 100,
                h: Math.round(rect.height * 100) / 100,
            },
            computedStyle: {
                position: style.position,
                display: style.display,
                zIndex: style.zIndex,
                overflow: style.overflow,
                margin: style.margin,
                padding: style.padding,
                boxSizing: style.boxSizing,
                width: style.width,
                height: style.height,
                maxWidth: style.maxWidth,
                maxHeight: style.maxHeight,
                flexGrow: style.flexGrow,
                gridArea: style.gridArea,
            },
        },
        parent: parent ? {
            tag: parent.tagName.toLowerCase(),
            rect: (() => {
                const pr = parent.getBoundingClientRect();
                return {
                    x: Math.round(pr.x * 100) / 100,
                    y: Math.round(pr.y * 100) / 100,
                    w: Math.round(pr.width * 100) / 100,
                    h: Math.round(pr.height * 100) / 100,
                };
            })(),
            display: window.getComputedStyle(parent).display,
            overflow: window.getComputedStyle(parent).overflow,
        } : null,
        siblings,
        stackingContext,
        viewport: {
            w: window.innerWidth,
            h: window.innerHeight,
        },
    };
}
"""

JS_ACCESSIBILITY_SPATIAL = """
() => {
    const MIN_TOUCH = 44; // px - WCAG minimum
    const interactiveSelectors = 'a, button, input, select, textarea, [role="button"], [tabindex]';
    const elements = document.querySelectorAll(interactiveSelectors);
    const issues = [];

    for (const el of elements) {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        if (rect.width === 0 && rect.height === 0) continue;

        const tag = el.tagName.toLowerCase();
        const selector = el.id ? '#' + CSS.escape(el.id) : tag;

        if (rect.width < MIN_TOUCH || rect.height < MIN_TOUCH) {
            issues.push({
                type: 'small_touch_target',
                element: selector,
                size: { w: Math.round(rect.width), h: Math.round(rect.height) },
                minimum: MIN_TOUCH,
            });
        }

        // check if covered by another element
        const cx = rect.x + rect.width / 2;
        const cy = rect.y + rect.height / 2;
        const topEl = document.elementFromPoint(cx, cy);
        if (topEl && topEl !== el && !el.contains(topEl) && !topEl.contains(el)) {
            issues.push({
                type: 'covered_interactive',
                element: selector,
                coveredBy: topEl.id ? '#' + CSS.escape(topEl.id) : topEl.tagName.toLowerCase(),
            });
        }
    }

    return { totalInteractive: elements.length, issues };
}
"""

# ── Issue detection ──────────────────────────────────────────

def detect_layout_issues(
    elements: list[dict], viewport: dict
) -> list[dict]:
    """Analiza elementos y detecta problemas de layout."""
    issues: list[LayoutIssue] = []
    vp_w = viewport.get("w", 1280)
    vp_h = viewport.get("h", 720)

    visible_elements = [e for e in elements if e.get("visible", True)]

    for el in visible_elements:
        r = el["rect"]
        sel = el["selector"]
        rect = Rect(r["x"], r["y"], r["w"], r["h"])

        # Off-screen detection
        if rect.right < 0 or rect.x > vp_w:
            issues.append(LayoutIssue(
                type="offscreen_horizontal",
                severity="error",
                element=sel,
                description=f"Element is completely off-screen horizontally",
                details={"rect": r, "viewport_w": vp_w},
            ))
        elif rect.x < 0:
            clipped = abs(rect.x) * rect.h
            issues.append(LayoutIssue(
                type="clipped_left",
                severity="warning",
                element=sel,
                description=f"Element extends {abs(rect.x):.0f}px past left edge",
                details={"overflow_px": abs(rect.x), "clipped_area": clipped},
            ))
        elif rect.right > vp_w:
            overflow = rect.right - vp_w
            issues.append(LayoutIssue(
                type="clipped_right",
                severity="warning",
                element=sel,
                description=f"Element extends {overflow:.0f}px past right edge (causes horizontal scroll)",
                details={"overflow_px": overflow},
            ))

        if rect.bottom < 0:
            issues.append(LayoutIssue(
                type="offscreen_top",
                severity="warning",
                element=sel,
                description="Element is completely above viewport",
                details={"rect": r},
            ))
        elif rect.y < 0:
            issues.append(LayoutIssue(
                type="clipped_top",
                severity="info",
                element=sel,
                description=f"Element extends {abs(rect.y):.0f}px above viewport",
                details={"overflow_px": abs(rect.y)},
            ))

        # Text truncation
        if el.get("textTruncated"):
            issues.append(LayoutIssue(
                type="text_truncated",
                severity="info",
                element=sel,
                description="Text is being truncated (ellipsis or nowrap overflow)",
            ))

    # Overlap detection (O(n²) pero limitado a MAX_ELEMENTS=500)
    by_index = {e["index"]: e for e in elements if "index" in e}

    def nested(a: dict, b: dict) -> bool:
        """True if either element contains the other.

        A child always intersects its container — that is normal nesting, not a
        collision, and reporting it buries the real findings.
        """
        for outer, inner in ((a, b), (b, a)):
            target = outer.get("index")
            p = inner.get("parent", -1)
            while p is not None and p >= 0:
                if p == target:
                    return True
                p = by_index.get(p, {}).get("parent", -1)
        return False

    for i, a in enumerate(visible_elements):
        ra = Rect(a["rect"]["x"], a["rect"]["y"], a["rect"]["w"], a["rect"]["h"])
        # skip tiny elements
        if ra.w < 5 or ra.h < 5:
            continue
        za = a.get("zIndex", "auto")

        for b in visible_elements[i + 1:]:
            rb = Rect(b["rect"]["x"], b["rect"]["y"], b["rect"]["w"], b["rect"]["h"])
            if rb.w < 5 or rb.h < 5:
                continue

            area = ra.intersection_area(rb)
            if area < 100:  # ignore tiny overlaps (<100px²)
                continue

            if nested(a, b):
                continue

            zb = b.get("zIndex", "auto")
            # An explicit z-index means the author stacked these on purpose — a
            # badge, tooltip, modal or sticky header. Still reported, because the
            # intent can be wrong, but as info so it does not drown out the
            # collisions nobody asked for: two elements at z-index auto that
            # happen to land on top of each other.
            deliberate = za != "auto" or zb != "auto"
            issues.append(LayoutIssue(
                type="overlap",
                severity="info" if deliberate else "warning",
                element=a["selector"],
                description=(
                    f"Overlaps with {b['selector']} by {area:.0f}px²"
                    + (" (explicit z-index — likely intentional)" if deliberate else "")
                ),
                details={
                    "other": b["selector"],
                    "overlap_area_px": area,
                    "z_a": za,
                    "z_b": zb,
                    "deliberate_stacking": deliberate,
                },
            ))

    return [asdict(i) for i in issues]


# ── MCP Server ───────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    """Startup/shutdown del browser pool."""
    yield
    await pool.shutdown()


mcp = FastMCP(
    "Layout Inspector",
    instructions=(
        "Inspects rendered web page layouts via headless browser. "
        "Returns spatial/geometric data: bounding rects, z-index, overlaps, "
        "overflow, and detected layout issues. Use 'inspect_layout' for a full "
        "page scan, 'detect_issues' for problem-focused analysis, "
        "'element_context' for deep-diving a specific element, "
        "'compare_viewports' for responsive testing, and "
        "'accessibility_spatial' for touch target and coverage issues."
    ),
    lifespan=lifespan,
)


@mcp.tool()
async def inspect_layout(
    url: str,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    root_selector: Optional[str] = None,
    wait_for: Optional[str] = None,
) -> str:
    """Extracts the full computed layout tree of a web page.

    Returns every visible element with its bounding rect (x, y, w, h),
    z-index, overflow, visibility, position type, and child count.
    Limited to 500 elements. Use root_selector to scope to a subtree.

    Args:
        url: Page URL or file:// path to inspect
        viewport_width: Browser viewport width in px (default 1280)
        viewport_height: Browser viewport height in px (default 720)
        root_selector: CSS selector to scope inspection (default: body)
        wait_for: CSS selector to wait for before extracting (optional)
    """
    page = await pool.new_page(viewport_width, viewport_height)
    try:
        await page.goto(url, wait_until="networkidle", timeout=15000)
        if wait_for:
            await page.wait_for_selector(wait_for, timeout=5000)

        result = await page.evaluate(JS_EXTRACT_LAYOUT, root_selector)
        return json.dumps(result, indent=2)
    finally:
        await page.context.close()


@mcp.tool()
async def detect_issues(
    url: str,
    viewport_width: int = 1280,
    viewport_height: int = 720,
    root_selector: Optional[str] = None,
    wait_for: Optional[str] = None,
) -> str:
    """Scans a page for layout problems: overlaps, off-screen elements,
    clipping, text truncation, and z-index conflicts.

    Returns a list of issues with type, severity (error/warning/info),
    affected element, description, and quantified details (px overflow, area).

    Args:
        url: Page URL or file:// path to inspect
        viewport_width: Browser viewport width in px (default 1280)
        viewport_height: Browser viewport height in px (default 720)
        root_selector: CSS selector to scope inspection (default: body)
        wait_for: CSS selector to wait for before extracting (optional)
    """
    page = await pool.new_page(viewport_width, viewport_height)
    try:
        await page.goto(url, wait_until="networkidle", timeout=15000)
        if wait_for:
            await page.wait_for_selector(wait_for, timeout=5000)

        data = await page.evaluate(JS_EXTRACT_LAYOUT, root_selector)
        issues = detect_layout_issues(data["elements"], data["viewport"])

        return json.dumps({
            "url": url,
            "viewport": data["viewport"],
            "total_elements": len(data["elements"]),
            "issues_count": len(issues),
            "issues": issues,
        }, indent=2)
    finally:
        await page.context.close()


@mcp.tool()
async def element_context(
    url: str,
    selector: str,
    viewport_width: int = 1280,
    viewport_height: int = 720,
) -> str:
    """Deep inspection of a specific element: its computed styles,
    parent layout, sibling positions, and full stacking context chain.

    Use this when you need to understand WHY an element is positioned
    where it is — shows parent display/overflow, sibling rects, and
    every ancestor that creates a stacking context.

    Args:
        url: Page URL or file:// path
        selector: CSS selector of the target element
        viewport_width: Browser viewport width in px (default 1280)
        viewport_height: Browser viewport height in px (default 720)
    """
    page = await pool.new_page(viewport_width, viewport_height)
    try:
        await page.goto(url, wait_until="networkidle", timeout=15000)
        result = await page.evaluate(JS_ELEMENT_CONTEXT, selector)
        if result is None:
            return json.dumps({"error": f"Element not found: {selector}"})
        return json.dumps(result, indent=2)
    finally:
        await page.context.close()


@mcp.tool()
async def compare_viewports(
    url: str,
    viewports: str = "375x667,768x1024,1280x720,1920x1080",
    root_selector: Optional[str] = None,
    wait_for: Optional[str] = None,
) -> str:
    """Compares layout across multiple viewport sizes for responsive testing.

    Runs detect_issues at each viewport and reports which problems appear
    or disappear at different sizes. Great for finding mobile breakpoint bugs.

    Args:
        url: Page URL or file:// path
        viewports: Comma-separated WxH pairs (default: common breakpoints)
        root_selector: CSS selector to scope inspection (default: body)
        wait_for: CSS selector to wait for before extracting (optional)
    """
    pairs = []
    bad = []
    for vp in viewports.split(","):
        vp = vp.strip()
        if not vp:
            continue
        w, _, h = vp.partition("x")
        try:
            w, h = int(w), int(h)
        except ValueError:
            bad.append(vp)
            continue
        if w <= 0 or h <= 0:
            bad.append(vp)
            continue
        pairs.append((w, h))

    if bad:
        return json.dumps({
            "error": f"Invalid viewport(s): {', '.join(bad)}. "
                     "Expected comma-separated WxH pairs with positive integers, "
                     "e.g. '375x667,1280x720'."
        }, indent=2)
    if not pairs:
        return json.dumps({"error": "No viewports given."}, indent=2)

    results = []
    for w, h in pairs:
        page = await pool.new_page(w, h)
        try:
            await page.goto(url, wait_until="networkidle", timeout=15000)
            if wait_for:
                await page.wait_for_selector(wait_for, timeout=5000)

            data = await page.evaluate(JS_EXTRACT_LAYOUT, root_selector)
            issues = detect_layout_issues(data["elements"], data["viewport"])

            results.append({
                "viewport": f"{w}x{h}",
                "total_elements": len(data["elements"]),
                "issues_count": len(issues),
                "issues": issues,
            })
        finally:
            await page.context.close()

    # Summary: issues unique to each viewport
    all_viewports = [r["viewport"] for r in results]
    summary = {}
    for r in results:
        unique = [
            i for i in r["issues"]
            if i["severity"] in ("error", "warning")
        ]
        summary[r["viewport"]] = {
            "total_issues": r["issues_count"],
            "errors_and_warnings": len(unique),
        }

    return json.dumps({
        "url": url,
        "summary": summary,
        "details": results,
    }, indent=2)


@mcp.tool()
async def accessibility_spatial(
    url: str,
    viewport_width: int = 1280,
    viewport_height: int = 720,
) -> str:
    """Checks spatial accessibility: touch target sizes (WCAG 44px minimum)
    and interactive elements covered/hidden by other elements.

    Args:
        url: Page URL or file:// path
        viewport_width: Browser viewport width in px (default 1280)
        viewport_height: Browser viewport height in px (default 720)
    """
    page = await pool.new_page(viewport_width, viewport_height)
    try:
        await page.goto(url, wait_until="networkidle", timeout=15000)
        result = await page.evaluate(JS_ACCESSIBILITY_SPATIAL)
        return json.dumps(result, indent=2)
    finally:
        await page.context.close()


# ── Entrypoint ───────────────────────────────────────────────

def main():
    mcp.run()


if __name__ == "__main__":
    main()
