"""End-to-end checks against real Chromium and the fixture pages.

These are the tests that would have caught the false positives: a screen-reader
link reported as off-screen, a carousel reported as truncated text, a Tailwind
class name producing a selector that throws.
"""

import json

import pytest

from layout_inspector import server

pytestmark = pytest.mark.integration


async def issues_for(url, **kwargs):
    return json.loads(await server.detect_issues(url, **kwargs))


def types_of(payload, element=None):
    return {
        i["type"] for i in payload["issues"] if element is None or i["element"].endswith(element)
    }


def find(payload, kind, needle):
    return [i for i in payload["issues"] if i["type"] == kind and needle in i["element"]]


async def test_environment_reports_a_ready_browser(browser_pool):
    info = json.loads(await server.check_environment())
    assert info["browser_ready"] is True
    assert info["browser_version"]


async def test_test_page_defects_are_all_found(browser_pool, test_page):
    payload = await issues_for(test_page)
    assert payload["page"]["url"] == test_page
    # The 1500px box widens the document; the fixed header does not.
    scroll = [i for i in payload["issues"] if i["type"] == "horizontal_scroll"]
    assert scroll and scroll[0]["severity"] == "error"
    assert "wide-box" in json.dumps(scroll[0]["details"]["culprits"])
    assert find(payload, "clipped_right", "wide-box")
    header = find(payload, "clipped_right", "header")
    assert header and header[0]["details"]["causes_page_scroll"] is False
    assert find(payload, "text_truncated", "truncated")


async def test_screen_reader_link_is_not_an_offscreen_defect(browser_pool, edge_page):
    payload = await issues_for(edge_page)
    assert not find(payload, "offscreen_horizontal", "sr-only")


async def test_ellipsis_on_a_box_the_text_fits_is_not_truncation(browser_pool, edge_page):
    payload = await issues_for(edge_page)
    assert not find(payload, "text_truncated", "ellipsis-fits")
    assert find(payload, "text_truncated", "ellipsis-cuts")


async def test_a_nowrap_carousel_is_not_truncated_text(browser_pool, edge_page):
    payload = await issues_for(edge_page)
    assert not find(payload, "text_truncated", "carousel")


async def test_a_slide_clipped_by_its_carousel_does_not_cause_page_scroll(browser_pool, edge_page):
    payload = await issues_for(edge_page)
    for issue in find(payload, "clipped_right", "slide"):
        assert issue["details"]["causes_page_scroll"] is False


async def test_the_real_escapee_collision_is_a_warning(browser_pool, edge_page):
    payload = await issues_for(edge_page)
    hits = [
        i
        for i in payload["issues"]
        if i["type"] == "overlap"
        and "escapee" in (i["element"] + i["details"].get("other", ""))
        and "neighbour" in (i["element"] + i["details"].get("other", ""))
    ]
    assert hits, "an absolute child landing on its neighbour is the real collision"
    assert hits[0]["severity"] == "warning"


async def test_a_trapped_z_index_is_reported_as_an_error(browser_pool, edge_page):
    payload = await issues_for(edge_page)
    hits = find(payload, "z_index_conflict", "under")
    assert hits, "z-index 999 painted over by z-index 1 is the classic stacking trap"
    assert hits[0]["details"]["z_self"] > hits[0]["details"]["z_other"]


async def test_selectors_with_tailwind_classes_are_usable(browser_pool, edge_page):
    layout = json.loads(await server.inspect_layout(edge_page))
    selectors = [e["selector"] for e in layout["elements"] if "md" in e["selector"]]
    assert selectors, "the Tailwind-style element should be in the tree"
    for selector in selectors:
        context = json.loads(await server.element_context(edge_page, selector))
        assert context["element"]["tag"] == "div"


async def test_reported_selectors_resolve_to_exactly_one_element(browser_pool, edge_page):
    layout = json.loads(await server.inspect_layout(edge_page))
    for element in layout["elements"][:40]:
        context = json.loads(await server.element_context(edge_page, element["selector"]))
        assert context["matched"] == 1, f"{element['selector']} is ambiguous"


async def test_a_missing_root_selector_is_an_error_not_an_empty_clean_scan(browser_pool, edge_page):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="No element matches root_selector"):
        await server.detect_issues(edge_page, root_selector="#does-not-exist")


async def test_an_invalid_selector_explains_itself(browser_pool, edge_page):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="not a valid CSS selector"):
        await server.element_context(edge_page, "div[")


async def test_a_missing_file_is_a_readable_error(browser_pool):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="No such file"):
        await server.detect_issues("file:///nope/missing-page.html")


async def test_overflow_culprits_are_found_past_the_element_cap(browser_pool, edge_page):
    payload = await issues_for(edge_page, max_elements=3)
    assert payload["element_cap_reached"] is True
    scroll = [i for i in payload["issues"] if i["type"] == "horizontal_scroll"]
    assert scroll, "the document still scrolls even when the scan is capped"
    assert "too-wide" in json.dumps(scroll[0]["details"]["culprits"])
    assert "note" in payload


async def test_element_context_names_the_property_that_traps_the_z_index(browser_pool, edge_page):
    context = json.loads(await server.element_context(edge_page, ".under"))
    reasons = [c["creates_stacking_context_because"] for c in context["stackingContext"]]
    assert any("transform" in r for r in reasons)
    assert context["occludedBy"]["selector"]


async def test_accessibility_applies_the_wcag_exceptions(browser_pool, edge_page):
    report = json.loads(await server.accessibility_spatial(edge_page))
    failing = {i["element"] for i in report["issues"] if i["type"] == "small_touch_target"}
    assert any("tiny" in s for s in failing), "an 18px button with a neighbour fails AA"
    assert not any("spaced" in s for s in failing), "the 24px spacing exception applies"
    assert not any("ok-target" in s for s in failing), "48px passes"
    assert report["skipped"]["disabled"] >= 1, "disabled controls are not targets"


async def test_accessibility_can_be_scoped_to_a_subtree(browser_pool, edge_page):
    whole = json.loads(await server.accessibility_spatial(edge_page))
    scoped = json.loads(await server.accessibility_spatial(edge_page, root_selector=".stack"))
    assert scoped["totalInteractive"] < whole["totalInteractive"]


async def test_compare_viewports_names_the_breakpoint_regression(browser_pool, test_page):
    payload = json.loads(await server.compare_viewports(test_page, viewports="375x667,1280x720"))
    assert set(payload["summary"]) == {"375x667", "1280x720"}
    diff = payload["diff"]
    assert diff["viewports_tested"] == ["375x667", "1280x720"]
    assert diff["breakpoint_specific"] or diff["at_every_viewport"]


async def test_compare_viewports_rejects_a_bad_size_before_launching(browser_pool, test_page):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="Invalid viewport"):
        await server.compare_viewports(test_page, viewports="375x667,nope")


async def test_lean_output_is_smaller_than_verbose(browser_pool, edge_page):
    lean = await server.inspect_layout(edge_page)
    verbose = await server.inspect_layout(edge_page, verbose=True)
    assert len(lean) < len(verbose)


async def test_a_narrow_viewport_measures_at_that_width(browser_pool, edge_page):
    """Touch semantics must not drag the layout viewport with them."""
    payload = json.loads(
        await server.inspect_layout(edge_page, viewport_width=375, viewport_height=667)
    )
    assert payload["viewport"]["w"] == 375


async def test_full_phone_emulation_is_opt_in_and_widens_the_layout_viewport(
    browser_pool, edge_page
):
    """edge_cases.html has no viewport meta, so a real phone lays it out wide."""
    payload = json.loads(
        await server.inspect_layout(edge_page, viewport_width=375, viewport_height=667, mobile=True)
    )
    assert payload["viewport"]["w"] > 375


async def test_a_missing_viewport_meta_is_reported_at_phone_widths(browser_pool, edge_page):
    payload = await issues_for(edge_page, viewport_width=375, viewport_height=667)
    hits = [i for i in payload["issues"] if i["type"] == "missing_viewport_meta"]
    assert hits and hits[0]["severity"] == "error"


async def test_a_page_with_a_viewport_meta_is_not_flagged(browser_pool, test_page):
    payload = await issues_for(test_page, viewport_width=375, viewport_height=667)
    assert not [i for i in payload["issues"] if i["type"] == "missing_viewport_meta"]
