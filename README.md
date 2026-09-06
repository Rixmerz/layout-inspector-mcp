# layout-inspector-mcp

Spatial inspection of rendered web pages for AI agents. Drives a headless browser (Playwright), extracts the **computed** layout — bounding rects, clipping, stacking — and reports layout problems as measured geometry.

## Why

An agent that changes CSS has no way to know whether the result overlaps, clips, or truncates. Screenshots are the obvious answer and the wrong one: judging overlap by looking at pixels is probabilistic — subtle collisions get missed, and legitimate overlays get reported as bugs.

This measures instead. `getBoundingClientRect()` on every element, cross-referenced for intersections, with the overlap area in px². Deterministic, reproducible, and it tells you *which* two elements collide rather than "something looks off". Use a screenshot afterwards to confirm a finding looks wrong to a human — not to find it.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) — used by the plugin launch path
- **Playwright's Chromium**, installed into the same environment that runs the server:

```bash
uv run --project <plugin-or-repo-root> playwright install chromium
```

A bare `playwright install chromium` from some other environment installs a build this one will refuse — Playwright pins an exact browser revision per wheel. If a tool call fails to launch a browser, call `check_environment`; it reports the expected build, whether it is present, and the command to fix it.

To reuse a Chromium you already have, point `LAYOUT_INSPECTOR_CHROMIUM` at its executable instead.

## Install as plugin

```bash
claude plugin install layout-inspector --marketplace Rixmerz/claude-plugins
uv run --project ~/.claude/plugins/cache/rixmerz/layout-inspector/0.4.0 playwright install chromium
```

**First-launch timeout risk:** a cold `uv` cache downloads the Playwright wheel (~45MB) plus its deps before the server reports ready, which can exceed Claude Code's default 30s MCP startup timeout. Warm it once:

```bash
uv sync --project ~/.claude/plugins/cache/rixmerz/layout-inspector/0.4.0
```

The cache path uses the **plugin** name (`layout-inspector`), not the repo name.

## Manual install

```bash
git clone https://github.com/Rixmerz/layout-inspector-mcp.git
cd layout-inspector-mcp
uv sync
uv run playwright install chromium
claude mcp add layout-inspector -- uv run --project "$PWD" layout-inspector-mcp
```

## Tools

Every tool takes a `url` — an `http(s)://` address or a `file://` path.

| Tool | Purpose |
| --- | --- |
| `detect_issues` | The main one. Horizontal scroll, overlaps, off-screen elements, clipping, truncated text, occluded content, trapped z-indexes — each with severity and quantified detail. |
| `inspect_layout` | Full computed layout tree: bounding rects, visible rects after clipping, z-index, position, overflow per element. |
| `element_context` | Deep dive on one element: computed styles, parent layout, siblings, the stacking-context chain and every clipping ancestor. |
| `compare_viewports` | Same page across breakpoints, with the findings **diffed** so responsive regressions are named rather than left for you to spot. |
| `accessibility_spatial` | WCAG 2.2 target sizes and interactive elements covered by something else. |
| `check_environment` | Whether the browser this server needs is installed, and how to install it. |

Shared arguments: `viewport_width`, `viewport_height`, `root_selector` (scope to a subtree — errors if it matches nothing), `wait_for` (block on a selector for late content), `wait_until` (`networkidle`, `load`, `domcontentloaded`, `commit`), `timeout_ms`, `full_page` (scroll once to trigger lazy loading), `scroll_y` (measure at an offset, for sticky behaviour), and `mobile` (full phone emulation, opt-in).

### Example

`detect_issues` at `375x667` against a page with a 1500px box and a fixed header:

```json
{
  "viewport": { "w": 375, "h": 667, "scrollW": 1520, "scrollH": 667 },
  "issues_count": 18,
  "by_severity": { "error": 1, "warning": 4, "info": 13 },
  "issues": [
    {
      "type": "horizontal_scroll",
      "severity": "error",
      "element": "div.wide-box",
      "description": "The page scrolls horizontally: content is 1145px wider than the 375px viewport. Widest culprit: div.wide-box.",
      "details": { "overflow_px": 1145.0, "culprits": [ { "selector": "div.wide-box", "overflow_px": 1145 } ] }
    },
    {
      "type": "clipped_right",
      "severity": "warning",
      "element": "div.header:nth-of-type(1)",
      "description": "Element extends 40px past the right edge but does not widen the document (fixed or clipped).",
      "details": { "overflow_px": 40.0, "causes_page_scroll": false, "position": "fixed" }
    }
  ]
}
```

Both elements stick past the right edge. Only one of them causes the scrollbar, and the report says which.

## What it detects

- **`horizontal_scroll`** — `error`. The document is wider than the viewport. Culprits are found across the *whole* page, not just the elements returned, so a wide footer past the element cap is still named.
- **`overlap`** — two elements intersect and neither contains the other. Severity splits signal from noise:
  - **`warning`** — neither is deliberately stacked. Probably a bug: an element escaping its container onto a neighbour.
  - **`info`** — one has an explicit z-index, or is `fixed`/`sticky`. The author stacked them on purpose. A child inherits its stacking ancestor's z-index, so a paragraph inside a `z-index: 50` modal counts as deliberate too.
- **`z_index_conflict`** — `error`. An element with the *higher* z-index is painted over anyway, because an ancestor with `transform`, `filter`, `opacity` or `will-change` trapped it in its own stacking context. `element_context` names the property responsible.
- **`occluded_content`** — content painted over by another element, measured with `elementFromPoint` rather than guessed from z-index.
- **`clipped_right` / `clipped_left` / `offscreen_*`** — an element past a viewport edge, with `causes_page_scroll` saying whether it actually widens the document.
- **`clipped_by_ancestor`** — content entirely inside an ancestor's hidden overflow.
- **`text_truncated`** — `info`, with how many pixels are lost. Only fires when the content genuinely exceeds the box, so `text-overflow: ellipsis` on text that fits is not reported.
- **`missing_viewport_meta`** — `error` at phone widths. Without it a phone lays the page out at ~980px, so every measurement understates the real mobile layout.
- Touch targets under WCAG 2.2 SC 2.5.8 (24px, level AA) with its inline-in-text and 24px-spacing exceptions, SC 2.5.5 (44px, AAA) as info, and interactive elements covered by something else.

Nesting is never reported — a child always intersects its container. Intersections under 100px² and elements under 5px are skipped so hairline offsets do not drown the signal. Screen-reader-only elements (`.sr-only` and friends) are recognised and never reported as off-screen defects.

## Configuration

All optional, read from the environment:

| Variable | Effect |
| --- | --- |
| `LAYOUT_INSPECTOR_CHROMIUM` | Path to a Chromium executable, instead of Playwright's bundled build. |
| `LAYOUT_INSPECTOR_MAX_CONCURRENCY` | Concurrent pages (default 4). |
| `LAYOUT_INSPECTOR_IDLE_SHUTDOWN` | Seconds of inactivity before the browser closes (default 300; `0` disables). |
| `LAYOUT_INSPECTOR_NO_SANDBOX` | Force `--no-sandbox`. Already implied when running as root. |
| `LAYOUT_INSPECTOR_STORAGE_STATE` | Playwright storage-state file, for pages behind a login. |
| `LAYOUT_INSPECTOR_BASIC_AUTH` | `user:password` for HTTP basic auth. |
| `LAYOUT_INSPECTOR_HTTP_HEADERS` | JSON object of extra request headers. |
| `LAYOUT_INSPECTOR_LOG_LEVEL` | Server log level (default `WARNING`). |

Credentials are read from the environment rather than passed as tool arguments, so an agent never has to handle them.

## Development

```bash
uv sync --all-groups
uv run pytest -m "not integration"     # unit tests, no browser needed
uv run playwright install chromium
uv run pytest                          # everything, including the browser tests
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Fixture pages live in `tests/pages/`. `edge_cases.html` exists to pin the false positives this server used to produce: a screen-reader link read as off-screen, a `nowrap` carousel read as truncated text, a Tailwind class name producing a selector that throws.

## Skill and subagent

The plugin ships two components beyond the MCP server, both loaded automatically when it is installed.

### Skill: `layout-inspection`

Triggers on layout symptoms — "se superpone", "se corta en mobile", "horizontal scroll", "revisa el layout", or any CSS change that needs verifying. It carries what the raw tool schemas cannot: what each severity means, the measurement gotchas, a symptom → measurement → usual-cause table, and the diagnose/fix/re-measure loop.

### Subagent: `layout-inspector`

Delegate a whole layout investigation to it and get back findings rather than a JSON dump. It baselines, triages by severity, root-causes every finding with `element_context` before touching CSS, applies the minimal fix, and re-measures at all four breakpoints. It reports in a fixed `FINDING / MEASURED / CAUSE / FIX / VERIFIED` form and will not claim a fix works without the re-measurement that proves it.

```
> ask the layout-inspector agent why the pricing cards overflow on mobile
```

**Model and effort: `sonnet` at `effort: high`.** The measurement is deterministic — the browser does it — so the hard part is not raw model capability but the diagnostic loop: hypothesise a cause from a stacking chain, verify it, fix, re-measure, check the other breakpoints. That is what a high effort budget buys, and it buys it more cheaply here than a larger model would.

## Limitations

- Reports geometry, not intent. An overlap can be a correct modal, tooltip, or sticky header — the tool quantifies, you judge.
- Chromium only. Firefox/WebKit differences are not covered.
- Needs the page to be reachable from this machine (local dev server, static file, or public URL).
- Occlusion is probed with `elementFromPoint`, which ignores elements with `pointer-events: none`. A decorative overlay that paints over content but does not receive clicks is not reported as covering it.
- Content inside a closed shadow root or a cross-origin iframe is not measured.

## License

MIT — see [LICENSE](LICENSE).
