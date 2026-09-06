"""Argument parsing and serialisation, without touching a browser."""

import json

import pytest
from fastmcp.exceptions import ToolError

from layout_inspector import scripts
from layout_inspector.server import _dump, _lean, _parse_viewports, _validate_url


def test_viewports_accept_a_csv_string():
    assert _parse_viewports("375x667,1280x720") == [(375, 667), (1280, 720)]


def test_viewports_accept_a_list():
    assert _parse_viewports(["375x667", " 1280X720 "]) == [(375, 667), (1280, 720)]


def test_duplicate_viewports_collapse():
    assert _parse_viewports("800x600,800x600") == [(800, 600)]


@pytest.mark.parametrize("bad", ["375", "375xabc", "0x600", "-10x20", "375x0"])
def test_invalid_viewports_name_themselves(bad):
    with pytest.raises(ToolError, match="Invalid viewport"):
        _parse_viewports(bad)


def test_empty_viewport_list_is_an_error():
    with pytest.raises(ToolError, match="No viewports"):
        _parse_viewports(" , ")


def test_too_many_viewports_is_an_error():
    with pytest.raises(ToolError, match="cap is"):
        _parse_viewports(",".join(f"{100 + i}x600" for i in range(12)))


@pytest.mark.parametrize("url", ["http://x.test", "https://x.test/a", "file:///tmp/a.html"])
def test_supported_url_schemes_pass(url):
    assert _validate_url(url) == url


@pytest.mark.parametrize("url", ["", "not-a-url", "/tmp/a.html", "ftp://x.test"])
def test_unsupported_urls_are_rejected_before_the_browser(url):
    with pytest.raises(ToolError):
        _validate_url(url)


def test_small_payloads_stay_readable():
    assert "\n" in _dump({"a": 1})


def test_large_payloads_are_compacted():
    payload = {"elements": [{"selector": f"div.d{i}", "rect": {"x": i}} for i in range(2000)]}
    out = _dump(payload)
    assert ", " not in out and '": ' not in out
    assert len(out) < len(json.dumps(payload, indent=2))


def test_lean_mode_drops_the_bulky_fields():
    element = {
        "selector": "div",
        "rect": {},
        "depth": 3,
        "classes": "a b",
        "id": "x",
        "overflow": "visible",
        "childrenCount": 2,
    }
    assert set(_lean([element], verbose=False)[0]) == {"selector", "rect"}
    assert _lean([element], verbose=True)[0] is element


@pytest.mark.parametrize(
    "name", [scripts.EXTRACT_LAYOUT, scripts.ELEMENT_CONTEXT, scripts.ACCESSIBILITY]
)
def test_every_script_gets_the_shared_helpers(name):
    source = scripts.load(name)
    assert "@inject helpers" not in source
    assert "function liBuildSelector" in source
