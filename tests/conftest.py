"""Shared fixtures. Integration tests skip cleanly when Chromium is missing."""

from __future__ import annotations

import pathlib

import pytest

PAGES = pathlib.Path(__file__).parent / "pages"


def page_url(name: str) -> str:
    path = PAGES / name
    if not path.exists():
        raise FileNotFoundError(path)
    return path.resolve().as_uri()


@pytest.fixture(scope="session")
def test_page() -> str:
    return page_url("test_page.html")


@pytest.fixture(scope="session")
def edge_page() -> str:
    return page_url("edge_cases.html")


@pytest.fixture
async def browser_pool():
    """A launched pool, or a skip when no usable Chromium is installed."""
    from layout_inspector.browser import BrowserUnavailable, pool

    try:
        await pool.get_browser()
    except BrowserUnavailable as exc:
        pytest.skip(f"Chromium unavailable: {str(exc).splitlines()[0]}")
    yield pool
    await pool.shutdown()
