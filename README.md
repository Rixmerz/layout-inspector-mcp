# layout-inspector-mcp

Spatial inspection of rendered web pages for AI agents. Drives a headless browser (Playwright), extracts the **computed** layout — bounding rects, z-index, overflow — and reports layout problems as measured geometry.

## Why

An agent that changes CSS has no way to know whether the result overlaps, clips, or truncates. Screenshots are the obvious answer and the wrong one: judging overlap by looking at pixels is probabilistic — subtle collisions get missed, and legitimate overlays get reported as bugs.

This measures instead. `getBoundingClientRect()` on every element, cross-referenced for intersections, with the overlap area in px². Deterministic, reproducible, and it tells you *which* two elements collide rather than "something looks off". Use a screenshot afterwards to confirm a finding looks wrong to a human — not to find it.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) — used by the plugin launch path
- **Playwright browser binaries** — the Python package alone is not enough:

```bash
playwright install chromium
```

Without this every tool call fails. Playwright's own error says so explicitly and names the command.

## Install as plugin

```bash
claude plugin install layout-inspector --marketplace Rixmerz/claude-plugins
```

Then run `playwright install chromium` once (see above).

**First-launch timeout risk:** a cold `uv` cache downloads the Playwright wheel (~45MB) plus its deps before the server reports ready, which can exceed Claude Code's default 30s MCP startup timeout. Warm it once:

```bash
uv sync --project ~/.claude/plugins/cache/rixmerz/layout-inspector/0.2.0
```

The cache path uses the **plugin** name (`layout-inspector`), not the repo name.

## Manual install

```bash
git clone https://github.com/Rixmerz/layout-inspector-mcp.git
cd layout-inspector-mcp
uv sync
playwright install chromium
claude mcp add layout-inspector -- uv run --project "$PWD" layout-inspector-mcp
```

## Tools

Every tool takes a `url` — an `http(s)://` address or a `file://` path.

| Tool | Purpose |
| --- | --- |
| `detect_issues(url, viewport_width?, viewport_height?, root_selector?, wait_for?)` | The main one. Overlaps, off-screen elements, clipping, text truncation, z-index conflicts — each with severity and quantified detail (px overflow, overlap area). |
| `inspect_layout(url, viewport_width?, viewport_height?, root_selector?, wait_for?)` | Full computed layout tree: bounding rects, z-index, overflow per element. |
| `element_context(url, selector, viewport_width?, viewport_height?)` | Deep dive on one element — computed styles and what surrounds it. |
| `compare_viewports(url, viewports?, root_selector?, wait_for?)` | Same page across breakpoints (default `375x667,768x1024,1280x720,1920x1080`) to catch responsive breakage. |
| `accessibility_spatial(url, viewport_width?, viewport_height?)` | Touch targets under the WCAG 44px minimum, and interactive elements covered by something else. |

`root_selector` scopes the scan to a subtree; `wait_for` blocks until a selector appears, for content that renders late.

## What it detects

- `overlap` — two elements intersect and neither contains the other. Severity splits the signal from the noise:
  - **`warning`** — both sit at `z-index: auto`. Nobody asked for this stacking, so it is probably a bug: an element escaping its container onto a neighbour.
  - **`info`** — at least one has an explicit z-index, meaning the author stacked them deliberately (badge, tooltip, modal, sticky header). Still reported, because the intent can be wrong, but it will not bury the real findings.

  Nesting is never reported — a child always intersects its container. Intersections under 100px² are ignored so borders and hairline offsets do not drown the signal.
- `clipped_right` / off-screen — an element extends past the viewport, causing horizontal scroll.
- `text_truncated` — ellipsis or `nowrap` overflow is cutting text.
- Touch targets below the WCAG minimum, and interactive elements obscured by another element.

## Limitations

- Reports geometry, not intent. An overlap can be a correct modal, tooltip, or sticky header — the tool quantifies, you judge.
- Renders in Chromium only; Firefox/WebKit differences are not covered.
- Needs the page to be reachable from this machine (local dev server, static file, or public URL).

## License

MIT
