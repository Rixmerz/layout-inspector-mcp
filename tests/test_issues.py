"""Unit tests for the issue detectors, on synthetic geometry.

The overlap heuristic is pinned here because it was wrong in both directions at
once: keyed on equal DOM `depth`, it missed real collisions across depths and
flagged deliberate same-depth stacking.
"""

import pytest

from layout_inspector.issues import detect_layout_issues, diff_viewport_issues

VIEWPORT = {"w": 1280, "h": 720, "scrollW": 1280, "scrollH": 720}


def el(index, parent, selector, x, y, w, h, **kwargs):
    element = {
        "index": index,
        "parent": parent,
        "selector": selector,
        "rect": {"x": x, "y": y, "w": w, "h": h},
        "zIndex": kwargs.pop("z", "auto"),
        "visible": kwargs.pop("visible", True),
        "hasText": kwargs.pop("has_text", False),
    }
    if "effective_z" in kwargs:
        element["effectiveZIndex"] = kwargs.pop("effective_z")
    if "position" in kwargs:
        element["computedPosition"] = kwargs.pop("position")
    if "visible_rect" in kwargs:
        vr = kwargs.pop("visible_rect")
        element["visibleRect"] = {"x": vr[0], "y": vr[1], "w": vr[2], "h": vr[3]}
        element["clippedByAncestor"] = True
    element.update(kwargs)
    return element


def by_type(issues, kind):
    return [i for i in issues if i["type"] == kind]


def overlaps(issues):
    return {(i["element"], i["details"]["other"]): i for i in by_type(issues, "overlap")}


# ── Overlap ──────────────────────────────────────────────────────


def test_real_collision_across_depths_is_caught():
    """.card sits one level deeper than .sidebar and covers it by 24000px²."""
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.wrap", 0, 0, 1200, 620),
        el(2, 1, "div.sidebar", 0, 0, 200, 400),
        el(3, 1, "div.content", 200, 0, 900, 400),
        el(4, 3, "div.card", 80, 50, 300, 200),
    ]
    hit = overlaps(detect_layout_issues(elements, VIEWPORT))[("div.sidebar", "div.card")]
    assert hit["severity"] == "warning"
    assert hit["details"]["overlap_area_px"] == 24000


def test_explicit_z_index_is_info_not_warning():
    """A badge on its thumbnail is deliberate — reported, but not as a warning."""
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.gallery", 0, 430, 300, 140),
        el(2, 1, "div.thumb", 0, 430, 160, 120),
        el(3, 1, "div.badge", 110, 436, 46, 24, z=2, effective_z=2),
    ]
    hit = overlaps(detect_layout_issues(elements, VIEWPORT))[("div.thumb", "div.badge")]
    assert hit["severity"] == "info"
    assert hit["details"]["deliberate_stacking"] is True


def test_nesting_is_not_a_collision():
    """A child inside its container always intersects it. That is not a finding."""
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.card", 0, 0, 400, 300),
        el(2, 1, "p.text", 10, 10, 380, 100),
        el(3, 2, "span.inner", 20, 20, 100, 40),
    ]
    assert not overlaps(detect_layout_issues(elements, VIEWPORT))


def test_inherited_z_index_makes_a_child_deliberate():
    """A paragraph inside a z-index:50 modal is stacked at 50, not at auto."""
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.main", 0, 60, 1280, 400),
        el(2, 0, "div.modal", 400, 20, 400, 300, z=50, effective_z=50, position="fixed"),
        el(3, 2, "p", 420, 40, 360, 40, effective_z=50),
    ]
    issues = detect_layout_issues(elements, VIEWPORT)
    hit = overlaps(issues)[("div.main", "p")]
    assert hit["severity"] == "info", "an element inside a stacked modal is not accidental"


def test_fixed_position_alone_counts_as_deliberate():
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.header", 0, 0, 1280, 60, position="fixed"),
        el(2, 0, "div.content", 0, 20, 800, 400),
    ]
    hit = overlaps(detect_layout_issues(elements, VIEWPORT))[("div.header", "div.content")]
    assert hit["severity"] == "info"


def test_fully_clipped_element_does_not_collide():
    """A carousel slide hidden by overflow:hidden is not on top of anything."""
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.carousel", 0, 0, 600, 120, overflow="hidden"),
        el(2, 1, "div.slide", 600, 0, 600, 100, visible_rect=(600, 0, 0, 0)),
        el(3, 0, "div.aside", 620, 0, 300, 100),
    ]
    assert not overlaps(detect_layout_issues(elements, VIEWPORT))


def test_tiny_overlaps_are_ignored():
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.a", 0, 0, 100, 100),
        el(2, 0, "div.b", 99, 99, 100, 100),
    ]
    assert not overlaps(detect_layout_issues(elements, VIEWPORT))


# ── Viewport edges ───────────────────────────────────────────────


def test_visually_hidden_elements_are_never_offscreen_defects():
    """.sr-only skip links live at left:-10000px on purpose."""
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "a.sr-only", -10000, 0, 1, 1, visuallyHidden=True),
    ]
    assert not by_type(detect_layout_issues(elements, VIEWPORT), "offscreen_horizontal")


def test_clipped_right_reports_whether_it_causes_page_scroll():
    viewport = {"w": 1280, "h": 720, "scrollW": 1500, "scrollH": 720}
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.wide", 0, 0, 1500, 60),
        el(2, 0, "div.header", 0, 0, 1320, 60, position="fixed"),
    ]
    issues = detect_layout_issues(
        elements,
        viewport,
        page_scrolls_horizontally=True,
        overflow_culprits=[{"selector": "div.wide", "overflow_px": 220}],
    )
    found = {i["element"]: i for i in by_type(issues, "clipped_right")}
    assert found["div.wide"]["details"]["causes_page_scroll"] is True
    assert found["div.header"]["details"]["causes_page_scroll"] is False


def test_horizontal_scroll_is_reported_with_its_culprit():
    viewport = {"w": 375, "h": 667, "scrollW": 900, "scrollH": 1200}
    issues = detect_layout_issues(
        [el(0, -1, "body", 0, 0, 375, 600)],
        viewport,
        page_scrolls_horizontally=True,
        overflow_culprits=[{"selector": "footer.wide", "overflow_px": 525}],
    )
    hit = by_type(issues, "horizontal_scroll")[0]
    assert hit["severity"] == "error"
    assert hit["element"] == "footer.wide"
    assert hit["details"]["overflow_px"] == 525


def test_fully_clipped_text_is_reported_once_not_as_overflow():
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "p.hidden", 0, 0, 200, 40, has_text=True, visible_rect=(0, 0, 0, 0)),
    ]
    issues = detect_layout_issues(elements, VIEWPORT)
    assert by_type(issues, "clipped_by_ancestor")
    assert not by_type(issues, "clipped_right")


# ── Occlusion and stacking ───────────────────────────────────────


def test_trapped_z_index_is_an_error():
    """Higher z-index, still painted over: an ancestor stacking context."""
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.over", 0, 0, 260, 100, z=1, effective_z=1),
        el(
            2,
            0,
            "div.under",
            0,
            0,
            260,
            100,
            z=999,
            effective_z=999,
            has_text=True,
            occludedRatio=1.0,
            occludedBy=1,
            stackingContext="transform",
        ),
    ]
    hit = by_type(detect_layout_issues(elements, VIEWPORT), "z_index_conflict")[0]
    assert hit["severity"] == "error"
    assert hit["details"]["z_self"] == 999
    assert hit["details"]["z_other"] == 1


def test_occlusion_by_a_deliberate_overlay_is_info():
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.modal", 0, 0, 400, 300, z=50, effective_z=50, position="fixed"),
        el(2, 0, "p.content", 10, 10, 300, 40, has_text=True, occludedRatio=1.0, occludedBy=1),
    ]
    hit = by_type(detect_layout_issues(elements, VIEWPORT), "occluded_content")[0]
    assert hit["severity"] == "info"


def test_occlusion_without_stacking_intent_is_a_warning():
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.blob", 0, 0, 400, 300),
        el(2, 0, "p.content", 10, 10, 300, 40, has_text=True, occludedRatio=1.0, occludedBy=1),
    ]
    hit = by_type(detect_layout_issues(elements, VIEWPORT), "occluded_content")[0]
    assert hit["severity"] == "warning"


def test_partial_occlusion_below_the_floor_is_not_reported():
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.blob", 0, 0, 400, 300),
        el(2, 0, "p.content", 10, 10, 300, 40, has_text=True, occludedRatio=0.2, occludedBy=1),
    ]
    assert not by_type(detect_layout_issues(elements, VIEWPORT), "occluded_content")


# ── Truncation, ordering and caps ────────────────────────────────


def test_truncation_reports_how_many_pixels_are_lost():
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "p.label", 0, 0, 120, 20, has_text=True, textTruncated=True, truncatedPx=180),
    ]
    hit = by_type(detect_layout_issues(elements, VIEWPORT), "text_truncated")[0]
    assert hit["details"]["truncated_px"] == 180


def test_issues_are_ordered_worst_first():
    viewport = {"w": 1280, "h": 720, "scrollW": 1600, "scrollH": 720}
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "p.trunc", 0, 0, 100, 20, has_text=True, textTruncated=True, truncatedPx=10),
        el(2, 0, "div.wide", 0, 100, 1600, 60),
    ]
    issues = detect_layout_issues(elements, viewport, page_scrolls_horizontally=True)
    severities = [i["severity"] for i in issues]
    assert severities == sorted(severities, key=lambda s: {"error": 0, "warning": 1, "info": 2}[s])


def test_max_issues_caps_the_payload():
    elements = [el(0, -1, "body", 0, 0, 4000, 4000)]
    for i in range(1, 30):
        elements.append(el(i, 0, f"div.d{i}", 0, 0, 500, 500))
    issues = detect_layout_issues(elements, VIEWPORT, max_issues=5)
    assert len(issues) == 5


def test_invisible_elements_are_skipped():
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620),
        el(1, 0, "div.a", 0, 0, 300, 300),
        el(2, 0, "div.ghost", 0, 0, 300, 300, visible=False),
    ]
    assert not overlaps(detect_layout_issues(elements, VIEWPORT))


# ── Viewport diffing ─────────────────────────────────────────────


def test_diff_separates_breakpoint_bugs_from_permanent_ones():
    results = [
        {
            "viewport": "375x667",
            "issues": [
                {"type": "clipped_right", "element": ".card", "severity": "warning", "details": {}},
                {
                    "type": "overlap",
                    "element": ".a",
                    "severity": "warning",
                    "details": {"other": ".b"},
                },
            ],
        },
        {
            "viewport": "1280x720",
            "issues": [
                {
                    "type": "overlap",
                    "element": ".a",
                    "severity": "warning",
                    "details": {"other": ".b"},
                },
            ],
        },
    ]
    diff = diff_viewport_issues(results)
    assert [e["element"] for e in diff["breakpoint_specific"]] == [".card"]
    assert diff["breakpoint_specific"][0]["viewports"] == ["375x667"]
    assert [e["element"] for e in diff["at_every_viewport"]] == [".a"]


@pytest.mark.parametrize("severity", ["error", "warning", "info"])
def test_diff_keeps_the_worst_severity_seen(severity):
    results = [
        {
            "viewport": "a",
            "issues": [{"type": "t", "element": "e", "severity": "info", "details": {}}],
        },
        {
            "viewport": "b",
            "issues": [{"type": "t", "element": "e", "severity": severity, "details": {}}],
        },
    ]
    diff = diff_viewport_issues(results)
    assert diff["at_every_viewport"][0]["severity"] == severity
