# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [0.4.0]

A correctness release. Every detector produced false positives or false
negatives that a real page would hit; the fixture pages in `tests/pages/` now
pin each one.

### Fixed — detection

- **Text truncation fired on text that fits.** `overflow: hidden` plus
  `text-overflow: ellipsis` was enough to report a defect. Truncation now
  requires the content to actually exceed the box, and reports how many pixels
  are lost. A `white-space: nowrap` carousel of element children is no longer
  read as cut-off text.
- **Screen-reader-only elements were reported as `offscreen_horizontal` errors.**
  The `.sr-only` family (1×1 box, `clip: rect(0,0,0,0)`, parked off-screen) is
  now recognised and skipped.
- **Content clipped by an ancestor was reported as escaping the viewport.** All
  edge checks now run on the element's *visible* rect after every clipping
  ancestor, tracked separately for static, absolute and fixed positioning.
- **A fixed header wider than the viewport was blamed for horizontal scroll.**
  `clipped_right` now carries `causes_page_scroll`, and fixed elements — which
  do not contribute to document scroll width — are excluded from the culprits.
- **Horizontal overflow past element 500 was invisible.** `horizontal_scroll` is
  now a page-level finding derived from the document's own scroll width, with
  culprits scanned across the whole page rather than only the returned elements.
- **Overlap severity ignored inherited stacking.** A paragraph inside a
  `z-index: 50` modal was reported as an accidental collision. Elements now
  carry an effective z-index from the nearest ancestor that set one, and
  `fixed`/`sticky` positioning counts as deliberate stacking on its own.
- **z-index conflicts were never detected**, despite being documented. A new
  `z_index_conflict` error fires when an element with the higher z-index is
  painted over anyway, because an ancestor's `transform`, `filter`, `opacity`,
  `will-change`, `contain` or `isolation` trapped it in a stacking context.
- **Occlusion was not measured at all.** `occluded_content` reports content
  painted over by another element, probed with `elementFromPoint` at nine points
  rather than inferred from pairwise z-index.
- **`root_selector` matching nothing returned an empty, clean-looking scan.** It
  is now an error that says so.
- **Selectors containing Tailwind or BEM class names threw when reused.** Every
  class is escaped with `CSS.escape`, and each selector is verified against
  `querySelectorAll` before being returned, so a reported selector resolves to
  exactly one element.
- **`opacity: 0` on an ancestor did not hide its children**, and a zero-size
  wrapper pruned its entire subtree — an absolutely positioned child of a
  zero-height wrapper was never measured. Both are fixed.
- **Touch-target checks used the wrong criterion.** They called 44px the "WCAG
  minimum"; SC 2.5.8 (level AA) is 24px, with inline-in-text and 24px-spacing
  exceptions, and 44px is SC 2.5.5 (AAA). Both are now reported at their real
  levels, and disabled, `inert`, `pointer-events: none` and screen-reader-only
  controls are excluded.
- **Missing `<meta name="viewport">` is now reported** at phone widths, where it
  invalidates every other measurement.

### Added

- `check_environment` tool: reports the Playwright version, whether a usable
  Chromium is installed, and the exact command to install it.
- `compare_viewports` now diffs findings across viewports
  (`breakpoint_specific` vs `at_every_viewport`) instead of returning bare
  counts, and measures viewports in parallel — about 2x faster on four sizes.
- `element_context` reports every clipping ancestor, what paints over the
  element's centre, `scrollWidth`/`clientWidth`, and names the CSS property that
  makes each ancestor a stacking context. It now covers `isolation`,
  `will-change`, `backdrop-filter`, `mix-blend-mode`, `contain`, `perspective`
  and `clip-path`, and returns the sizing, flex, grid and wrapping styles the
  diagnostic loop actually needs.
- `accessibility_spatial` accepts `root_selector` and `wait_for`.
- Per-call `wait_until`, `timeout_ms`, `max_elements`, `max_issues`, `verbose`,
  `full_page` (scroll once to trigger lazy loading), `scroll_y` (measure at an
  offset) and `mobile` (full phone emulation).
- Page diagnostics on every result: HTTP status, console errors, page errors,
  failed requests, and notes such as a `networkidle` fallback — a 404 no longer
  measures as a clean page.
- Environment configuration: `LAYOUT_INSPECTOR_CHROMIUM`,
  `MAX_CONCURRENCY`, `IDLE_SHUTDOWN`, `NO_SANDBOX`, `STORAGE_STATE`,
  `BASIC_AUTH`, `HTTP_HEADERS`, `LOG_LEVEL`. Credentials come from the
  environment, never from tool arguments.
- 75 tests, up from 3: unit tests for geometry, detection and argument parsing,
  plus browser-backed integration tests over two fixture pages.
- CI running ruff, ruff format, mypy, the unit tests on Python 3.11–3.13, the
  integration tests, a clean wheel install, and a check that the three declared
  versions agree.
- `LICENSE` — the MIT licence the README and plugin manifest already claimed.

### Changed

- The single `server.py` module is now the `layout_inspector` package, with the
  browser-side JavaScript in `layout_inspector/js/*.js` rather than embedded in
  doubly-escaped Python strings. Installing no longer places a module named
  `server` on the import path.
- `inspect_layout` output is about 64% smaller on a 500-element page (roughly
  54k tokens to 19.5k): compact JSON, omitted default values, and a `verbose`
  flag for the fields most callers never read.
- Errors are `ToolError`s that name the remedy — a missing browser, a refused
  connection, an invalid selector, a timeout — instead of raw Playwright
  tracebacks or, for `compare_viewports` alone, an `{"error": ...}` object.
- Web fonts are awaited and `prefers-reduced-motion` is emulated before
  measuring, so text metrics and entrance animations no longer vary between
  runs.
- Narrow viewports get touch input semantics so `pointer: coarse` applies;
  full phone emulation, which rewrites the layout viewport, is opt-in via
  `mobile=true`.
- Chromium is launched with `--no-sandbox` only when running as root or when
  asked, and closes after 5 minutes idle.
- `test_page.html` moved to `tests/pages/`.
- `fastmcp` is pinned `>=2.0,<4`.

## [0.3.0]

- Shipped the `layout-inspection` skill and the `layout-inspector` subagent.
- Moved the MCP manifest out of the repository root.

## [0.2.1]

- Fixed overlap detection, which keyed on equal DOM depth and was wrong in both
  directions: it missed real collisions across depths and flagged deliberate
  same-depth stacking.
- Made the package installable and shipped it as a Claude Code plugin.
