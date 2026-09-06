"""Turns extracted geometry into layout findings.

Every check here is a statement about measured pixels. Where a heuristic has to
guess at intent — a deliberate overlay versus an accidental collision — it says
so in the severity rather than by hiding the finding.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .geometry import Rect

# Overlaps below this area, and elements thinner than MIN_SIDE, are hairline
# artefacts of borders and sub-pixel rounding rather than layout defects.
MIN_OVERLAP_AREA = 100.0
MIN_SIDE = 5.0

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class LayoutIssue:
    type: str
    severity: str  # "error" | "warning" | "info"
    element: str
    description: str
    details: dict = field(default_factory=dict)


def _rect(el: dict, key: str = "rect") -> Rect:
    return Rect.from_dict(el.get(key) or el["rect"])


def _visible_rect(el: dict) -> Rect:
    """The part of the element that survives its clipping ancestors."""
    return Rect.from_dict(el.get("visibleRect") or el["rect"])


def _effective_z(el: dict):
    """z-index of the element, or of the nearest ancestor that set one.

    A child of a `z-index: 50` modal is stacked at 50 even though its own
    computed z-index is `auto`. Comparing own z-indexes alone reports the
    modal's paragraphs as accidental collisions.
    """
    z = el.get("effectiveZIndex")
    if z is None:
        z = el.get("zIndex", "auto")
    return z


def _is_deliberately_stacked(el: dict) -> bool:
    if _effective_z(el) != "auto":
        return True
    return el.get("computedPosition") in ("fixed", "sticky")


def _magnitude(issue: LayoutIssue) -> float:
    d = issue.details
    for key in ("overflow_px", "overlap_area_px", "occluded_ratio", "truncated_px"):
        if key in d:
            try:
                return float(d[key])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _build_nesting_test(elements: list[dict]):
    by_index = {e["index"]: e for e in elements if "index" in e}

    def ancestors(el: dict) -> set:
        seen: set = set()
        parent = el.get("parent", -1)
        while parent is not None and parent >= 0 and parent not in seen:
            seen.add(parent)
            parent = by_index.get(parent, {}).get("parent", -1)
        return seen

    cache: dict = {}

    def nested(a: dict, b: dict) -> bool:
        """True when either element contains the other.

        A child always intersects its container. That is nesting, not a
        collision, and reporting it buries the real findings.
        """
        for outer, inner in ((a, b), (b, a)):
            key = inner.get("index")
            if key not in cache:
                cache[key] = ancestors(inner)
            if outer.get("index") in cache[key]:
                return True
        return False

    return nested, by_index


def detect_layout_issues(
    elements: list[dict],
    viewport: dict,
    *,
    page_scrolls_horizontally: bool | None = None,
    overflow_culprits: list[dict] | None = None,
    has_viewport_meta: bool | None = None,
    max_issues: int = 250,
) -> list[dict]:
    """Analyse extracted elements and return findings, worst first."""
    issues: list[LayoutIssue] = []
    vp_w = float(viewport.get("w", 1280) or 1280)
    scroll_w = float(viewport.get("scrollW", vp_w) or vp_w)
    culprits = overflow_culprits or []
    culprit_selectors = {c.get("selector") for c in culprits}

    if page_scrolls_horizontally is None:
        page_scrolls_horizontally = scroll_w > vp_w + 1

    # A phone lays a page out at ~980px unless the document opts in. Measuring
    # at 375px hides that entirely, so it is reported rather than emulated.
    if has_viewport_meta is False and vp_w <= 480:
        issues.append(
            LayoutIssue(
                type="missing_viewport_meta",
                severity="error",
                element="head",
                description=(
                    'The page has no <meta name="viewport">, so a phone lays it out '
                    "at roughly 980px and scales it down. Every measurement at this "
                    "width understates the real mobile layout."
                ),
                details={
                    "fix": '<meta name="viewport" content="width=device-width, initial-scale=1">'
                },
            )
        )

    # ── Page-level: the horizontal scrollbar itself ──────────────
    # Reported from the document's own scroll width, so a culprit past the
    # element cap still surfaces the symptom.
    if page_scrolls_horizontally:
        overflow_px = round(scroll_w - vp_w, 2)
        top = culprits[0]["selector"] if culprits else None
        issues.append(
            LayoutIssue(
                type="horizontal_scroll",
                severity="error",
                element=top or "document",
                description=(
                    f"The page scrolls horizontally: content is {overflow_px:.0f}px "
                    f"wider than the {vp_w:.0f}px viewport."
                    + (f" Widest culprit: {top}." if top else "")
                ),
                details={
                    "overflow_px": overflow_px,
                    "scroll_width": scroll_w,
                    "viewport_width": vp_w,
                    "culprits": culprits[:10],
                },
            )
        )

    visible = [e for e in elements if e.get("visible", True) and not e.get("visuallyHidden")]
    nested, by_index = _build_nesting_test(elements)

    # ── Per-element checks ───────────────────────────────────────
    for el in visible:
        sel = el["selector"]
        raw = _rect(el)
        vis = _visible_rect(el)
        clipped_by_ancestor = bool(el.get("clippedByAncestor"))

        if vis.is_empty and not raw.is_empty:
            # Entirely inside a clipping ancestor's hidden overflow.
            if el.get("hasText") or el.get("interactive"):
                clipper = by_index.get(el.get("clippedBy", -1), {}).get("selector")
                issues.append(
                    LayoutIssue(
                        type="clipped_by_ancestor",
                        severity="warning" if el.get("interactive") else "info",
                        element=sel,
                        description=(
                            "Element is fully hidden by an ancestor's overflow"
                            + (f" ({clipper})" if clipper else "")
                            + "."
                        ),
                        details={"rect": raw.to_dict(), "clipped_by": clipper},
                    )
                )
            continue

        # Horizontal placement, measured on the part that is actually painted.
        if vis.right < 0 or vis.x > vp_w:
            issues.append(
                LayoutIssue(
                    type="offscreen_horizontal",
                    severity="error",
                    element=sel,
                    description="Element is completely off-screen horizontally.",
                    details={"rect": raw.to_dict(), "viewport_w": vp_w},
                )
            )
        elif vis.x < 0:
            issues.append(
                LayoutIssue(
                    type="clipped_left",
                    severity="warning",
                    element=sel,
                    description=f"Element extends {abs(vis.x):.0f}px past the left edge.",
                    details={
                        "overflow_px": round(abs(vis.x), 2),
                        "clipped_area": round(abs(vis.x) * vis.h, 2),
                    },
                )
            )
        elif vis.right > vp_w + 0.5:
            overflow = vis.right - vp_w
            causes_scroll = sel in culprit_selectors or (
                page_scrolls_horizontally
                and not clipped_by_ancestor
                and el.get("computedPosition") != "fixed"
            )
            issues.append(
                LayoutIssue(
                    type="clipped_right",
                    severity="warning",
                    element=sel,
                    description=(
                        f"Element extends {overflow:.0f}px past the right edge"
                        + (
                            " and widens the document, causing horizontal scroll."
                            if causes_scroll
                            else " but does not widen the document (fixed or clipped)."
                        )
                    ),
                    details={
                        "overflow_px": round(overflow, 2),
                        "causes_page_scroll": causes_scroll,
                        "position": el.get("computedPosition", "static") or "static",
                    },
                )
            )

        if vis.bottom < 0:
            issues.append(
                LayoutIssue(
                    type="offscreen_top",
                    severity="warning",
                    element=sel,
                    description="Element is completely above the viewport.",
                    details={"rect": raw.to_dict()},
                )
            )
        elif vis.y < 0:
            issues.append(
                LayoutIssue(
                    type="clipped_top",
                    severity="info",
                    element=sel,
                    description=f"Element extends {abs(vis.y):.0f}px above the viewport.",
                    details={"overflow_px": round(abs(vis.y), 2)},
                )
            )

        # Truncation is only reported when the content really exceeds the box.
        if el.get("textTruncated"):
            px = el.get("truncatedPx", 0)
            issues.append(
                LayoutIssue(
                    type="text_truncated",
                    severity="info",
                    element=sel,
                    description=(
                        f"Text is cut off: content is {px:.0f}px wider than the box "
                        "(ellipsis or nowrap overflow)."
                    ),
                    details={"truncated_px": px},
                )
            )

        # What paints on top, measured with elementFromPoint rather than
        # inferred from pairwise z-index.
        ratio = el.get("occludedRatio")
        if ratio and ratio >= 0.6:
            other = by_index.get(el.get("occludedBy", -1))
            other_sel = other["selector"] if other else "another element"
            z_self = _effective_z(el)
            z_other = _effective_z(other) if other else "auto"
            trapped = isinstance(z_self, int) and isinstance(z_other, int) and z_self > z_other
            if trapped:
                reason = (other or {}).get("stackingContext") or el.get("stackingContext")
                issues.append(
                    LayoutIssue(
                        type="z_index_conflict",
                        severity="error",
                        element=sel,
                        description=(
                            f"z-index says this element ({z_self}) is above {other_sel} "
                            f"({z_other}), but {other_sel} paints on top. An ancestor "
                            "stacking context is trapping it."
                        ),
                        details={
                            "other": other_sel,
                            "z_self": z_self,
                            "z_other": z_other,
                            "occluded_ratio": ratio,
                            "stacking_context": reason,
                        },
                    )
                )
            elif el.get("hasText") or el.get("interactive"):
                deliberate = bool(other) and _is_deliberately_stacked(other)
                issues.append(
                    LayoutIssue(
                        type="occluded_content",
                        severity="info" if deliberate else "warning",
                        element=sel,
                        description=(
                            f"{ratio:.0%} of this element is painted over by {other_sel}"
                            + (
                                " (explicit stacking — likely a deliberate overlay)."
                                if deliberate
                                else " and neither is deliberately stacked."
                            )
                        ),
                        details={
                            "other": other_sel,
                            "occluded_ratio": ratio,
                            "z_self": z_self,
                            "z_other": z_other,
                            "deliberate_stacking": deliberate,
                        },
                    )
                )

    # ── Pairwise overlap ─────────────────────────────────────────
    candidates = [
        e
        for e in visible
        if not _visible_rect(e).is_empty
        and _visible_rect(e).w >= MIN_SIDE
        and _visible_rect(e).h >= MIN_SIDE
    ]

    for i, a in enumerate(candidates):
        ra = _visible_rect(a)
        za = _effective_z(a)
        for b in candidates[i + 1 :]:
            rb = _visible_rect(b)
            area = ra.intersection_area(rb)
            if area < MIN_OVERLAP_AREA:
                continue
            if nested(a, b):
                continue

            zb = _effective_z(b)
            # Explicit stacking, or fixed/sticky positioning, means the author
            # put these on top of each other on purpose. Still reported, since
            # the intent can be wrong, but as info so it does not bury the
            # collisions nobody asked for.
            deliberate = _is_deliberately_stacked(a) or _is_deliberately_stacked(b)
            issues.append(
                LayoutIssue(
                    type="overlap",
                    severity="info" if deliberate else "warning",
                    element=a["selector"],
                    description=(
                        f"Overlaps with {b['selector']} by {area:.0f}px²"
                        + (" (explicit stacking — likely intentional)" if deliberate else "")
                    ),
                    details={
                        "other": b["selector"],
                        "overlap_area_px": round(area, 2),
                        "z_a": za,
                        "z_b": zb,
                        "deliberate_stacking": deliberate,
                    },
                )
            )

    issues.sort(key=lambda i: (SEVERITY_ORDER.get(i.severity, 3), -_magnitude(i)))
    return [asdict(i) for i in issues[:max_issues]]


def diff_viewport_issues(results: list[dict]) -> dict:
    """Which findings are unique to a viewport, and which hold everywhere.

    A responsive regression is an issue present at one breakpoint and absent at
    another; per-viewport counts alone never show that.
    """
    seen: dict[tuple, dict] = {}
    for result in results:
        vp = result["viewport"]
        for issue in result["issues"]:
            key = (issue["type"], issue["element"], issue.get("details", {}).get("other"))
            entry = seen.setdefault(
                key,
                {
                    "type": issue["type"],
                    "element": issue["element"],
                    "other": issue.get("details", {}).get("other"),
                    "severity": issue["severity"],
                    "viewports": [],
                },
            )
            entry["viewports"].append(vp)
            if SEVERITY_ORDER.get(issue["severity"], 3) < SEVERITY_ORDER.get(entry["severity"], 3):
                entry["severity"] = issue["severity"]

    all_viewports = [r["viewport"] for r in results]
    everywhere, breakpoint_only = [], []
    for entry in seen.values():
        if len(entry["viewports"]) == len(all_viewports):
            everywhere.append(entry)
        else:
            breakpoint_only.append(entry)

    order = {"error": 0, "warning": 1, "info": 2}
    breakpoint_only.sort(key=lambda e: order.get(e["severity"], 3))
    everywhere.sort(key=lambda e: order.get(e["severity"], 3))
    return {
        "viewports_tested": all_viewports,
        "breakpoint_specific": breakpoint_only,
        "at_every_viewport": everywhere,
    }
