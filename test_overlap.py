"""The overlap heuristic, pinned.

The bug this guards against: detection keyed on equal DOM `depth` both missed
real collisions across depths (a card escaping its container onto a sidebar)
and flagged deliberate same-depth stacking (a badge on its thumbnail). It was
wrong in both directions at once.
"""

from server import detect_layout_issues

VIEWPORT = {"w": 1280, "h": 720}


def el(index, parent, selector, x, y, w, h, depth, z="auto"):
    return {
        "index": index,
        "parent": parent,
        "selector": selector,
        "rect": {"x": x, "y": y, "w": w, "h": h},
        "zIndex": z,
        "visible": True,
        "depth": depth,
        "textTruncated": False,
    }


def overlaps(issues):
    return {
        (i["element"], i["details"]["other"]): i
        for i in issues
        if i["type"] == "overlap"
    }


def test_real_collision_across_depths_is_caught():
    """.card sits one level deeper than .sidebar and covers it by 24000px²."""
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620, 0),
        el(1, 0, "div.wrap", 0, 0, 1200, 620, 1),
        el(2, 1, "div.sidebar", 0, 0, 200, 400, 2),
        el(3, 1, "div.content", 200, 0, 900, 400, 2),
        el(4, 3, "div.card", 80, 50, 300, 200, 3),
    ]
    found = overlaps(detect_layout_issues(elements, VIEWPORT))
    hit = found.get(("div.sidebar", "div.card"))
    assert hit, f"missed the real collision; got {list(found)}"
    assert hit["severity"] == "warning"
    assert hit["details"]["overlap_area_px"] == 24000


def test_explicit_z_index_is_info_not_warning():
    """A badge on its thumbnail is deliberate — reported, but not as a warning."""
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620, 0),
        el(1, 0, "div.gallery", 0, 430, 300, 140, 1),
        el(2, 1, "div.thumb", 0, 430, 160, 120, 2),
        el(3, 1, "div.badge", 110, 436, 46, 24, 2, z=2),
    ]
    hit = overlaps(detect_layout_issues(elements, VIEWPORT))[("div.thumb", "div.badge")]
    assert hit["severity"] == "info"
    assert hit["details"]["deliberate_stacking"] is True


def test_nesting_is_not_a_collision():
    """A child inside its container always intersects it. That is not a finding."""
    elements = [
        el(0, -1, "body", 0, 0, 1280, 620, 0),
        el(1, 0, "div.card", 0, 0, 400, 300, 1),
        el(2, 1, "p.text", 10, 10, 380, 100, 2),   # child
        el(3, 2, "span.inner", 20, 20, 100, 40, 3),  # grandchild
    ]
    assert not overlaps(detect_layout_issues(elements, VIEWPORT))


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
