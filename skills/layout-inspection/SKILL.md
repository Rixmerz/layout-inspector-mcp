---
name: layout-inspection
description: Diagnose and fix rendered web layout defects by measuring geometry with the layout-inspector MCP server (detect_issues, inspect_layout, element_context, compare_viewports, accessibility_spatial, check_environment). Use whenever someone reports that a page looks broken, overlapping, cut off, misaligned, squashed on mobile, horizontally scrolling, or clipped — and whenever a CSS/layout change needs verification that it did not break another breakpoint. Also use for touch-target and covered-control accessibility checks, and for auditing a page before shipping. Trigger even when the user only says "se ve mal", "se superpone", "se corta el texto", "no se ve en mobile", "revisa el layout", or pastes a screenshot of a broken UI — the point of this skill is to replace eyeballing pixels with measured geometry.
---

# Layout Inspection

Measure layout, don't look at it. `getBoundingClientRect()` on every element,
cross-referenced for intersections, is deterministic; judging a screenshot is
probabilistic. A screenshot confirms a finding at the end — it never finds one.

## The tools

All take `url` (an `http(s)://` address or a `file://` path).

| Tool | Use it for |
|---|---|
| `detect_issues` | Default entry point. Issues with type, severity, element and quantified detail, ordered worst-first. |
| `inspect_layout` | Raw layout tree when you need coordinates `detect_issues` did not flag (below-the-fold positions, sibling alignment, actual widths). |
| `element_context` | Root cause for one element: computed styles, parent box, siblings, the stacking-context chain and every clipping ancestor. |
| `compare_viewports` | Responsive regressions, already diffed. Default `375x667,768x1024,1280x720,1920x1080`. |
| `accessibility_spatial` | WCAG 2.2 target sizes and covered controls. |
| `check_environment` | Call this when a tool fails to launch a browser, before concluding anything else. |

Shared arguments: `viewport_width`/`viewport_height` (default `1280x720`),
`root_selector` to scope the scan, `wait_for` for late-rendering content,
`wait_until` (`networkidle` default, `load` for pages that never go idle),
`timeout_ms`, `full_page` to trigger lazy loading, `scroll_y` to measure at an
offset, and `mobile` for full phone emulation.

## The loop

1. **Baseline.** `detect_issues` at the viewport where the user saw the problem.
   If they did not say, run `compare_viewports` — its `diff` names the
   breakpoint for you.
2. **Triage by severity, not by count.** A page with 40 `info` overlaps and one
   `warning` has exactly one finding. Issues already come back worst-first.
3. **Root cause with `element_context`** on the flagged selector before touching
   any CSS. The stacking chain, the clipping ancestors and the parent's
   `display`/`overflow` explain the geometry; guessing from the issue
   description alone produces wrong fixes.
4. **Fix the cause**, minimally.
5. **Re-measure** the same tool call. The issue is gone or it is not — never
   report a fix you did not re-measure.
6. **Re-run `compare_viewports`.** Layout fixes routinely trade a desktop bug
   for a mobile one.

## Reading severity

Severity encodes authorial intent, which is the whole point of the signal:

- `horizontal_scroll` — **`error`**. The document is wider than the viewport.
  `details.culprits` names the widest offenders, found across the whole page
  rather than only the returned elements. **Start here for any mobile
  complaint.**
- `missing_viewport_meta` — **`error`** at phone widths. Without the meta tag a
  phone lays the page out at ~980px, so every other measurement at 375px
  understates the real problem. Fix this before chasing anything else.
- `z_index_conflict` — **`error`**. An element with the higher z-index is
  painted over anyway: an ancestor's `transform`, `filter`, `opacity` or
  `will-change` trapped it in a stacking context. `element_context` names the
  property. This is a real bug essentially every time.
- `overlap` + **`warning`** — neither element is deliberately stacked. Almost
  always the real bug: something escaping its container onto a neighbour.
- `overlap` + **`info`** — one has an explicit z-index or is `fixed`/`sticky`.
  Deliberate: badge, tooltip, modal, sticky header. Reported because the intent
  can still be wrong, but never lead with these.
- `occluded_content` — content painted over, measured with `elementFromPoint`.
  `warning` when nothing is deliberately stacked, `info` under a deliberate
  overlay.
- `clipped_right` / `clipped_left` — **`warning`**, with `overflow_px`. Read
  `details.causes_page_scroll`: `true` means this element widens the document,
  `false` means it is `fixed` or clipped by an ancestor and is a visual cut-off
  rather than the scrollbar's cause.
- `clipped_by_ancestor` — content entirely inside an ancestor's hidden overflow.
  `warning` when interactive, `info` otherwise.
- `offscreen_horizontal` — **`error`**; `offscreen_top` — `warning` (often a
  deliberately hidden drawer); `clipped_top` — `info`.
- `text_truncated` — **`info`**, with `truncated_px`. Only fires when content
  genuinely exceeds the box, so it is a real cut-off; whether that is wanted is
  a design question.

Nesting is never reported. Intersections under **100px²** and elements under
**5px** are skipped. Screen-reader-only elements are recognised and never
reported as off-screen defects. A child inherits its stacking ancestor's
z-index, so elements inside a stacked modal count as deliberate.

## What the numbers actually mean

- **Coordinates are viewport-relative at the requested scroll position**, which
  is 0 unless you pass `scroll_y`. An element below the fold legitimately has
  `y > viewport_height` and is *not* flagged — there is no "off-screen bottom"
  issue type, by design.
- **`element_cap_reached: true` means the tail of the page was never measured.**
  Raise `max_elements` or scope with `root_selector` before concluding the page
  is clean. Horizontal-overflow culprits are still found past the cap; nothing
  else is.
- **Default values are omitted from `inspect_layout` elements** to keep large
  trees affordable. An absent field means: `zIndex` is `auto`, position is
  `static`, the element is visible, it has no direct text, its effective
  z-index equals its own, and nothing clips it. The payload's
  `omitted_field_defaults` says so too. Pass `verbose=true` for depth, classes,
  id, overflow and child counts.
- **Reported selectors are verified unique.** Each one is checked against
  `querySelectorAll` before being returned, and Tailwind-style class names are
  escaped, so you can paste a selector straight into `element_context`. If it
  still fails, the DOM changed between calls.
- **`compare_viewports` returns a `diff`, not just counts.**
  `diff.breakpoint_specific` is the responsive regression; `diff.at_every_viewport`
  is a general defect. Pass `include_details=true` only when you need the full
  per-viewport lists.
- **Overlap pairs are ordered by DOM position**: `element` appears before
  `details.other` in the tree. It is not a claim about which one is at fault.
- **The `page` block reports what the browser saw**: HTTP status, console
  errors, failed requests, and notes such as "networkidle never reached". A 404
  or a crashed hydration otherwise measures as a clean, empty page — check it
  before reporting no issues.

## Getting the page to render

- `goto` waits for `networkidle` by default with a 15s timeout, then falls back
  to `load` and says so in `page.notes`. For a page with a websocket, poller or
  analytics beacon, pass `wait_until="load"` up front.
- `wait_for` has its own 5s timeout and runs after load — use it for content
  behind a fetch or a framework mount. If it never appears, the tools measure
  anyway and note it rather than failing.
- Animations are measured wherever they happen to be, though `reduced-motion` is
  emulated and web fonts are awaited before measuring.
- `full_page=true` scrolls the page once to trigger lazy loading, then measures
  at the top. `scroll_y` measures at an offset, which is how you check sticky
  behaviour.
- Narrow viewports get touch input semantics, so `pointer: coarse` applies, but
  the layout viewport stays at the width you asked for. `mobile=true` adds full
  phone emulation, including the ~980px layout viewport a page without a
  viewport meta tag really gets.
- Chromium only. Firefox/WebKit differences are out of scope, and saying so is
  better than implying coverage the tool does not have.
- If a call fails to launch a browser, run `check_environment` and report its
  `remedy` verbatim. Do not fall back to reading CSS and guessing.

## Common defects and where to look

| Symptom | First measurement | Usual cause |
|---|---|---|
| Horizontal scroll on mobile | `detect_issues` at `375x667`, read `horizontal_scroll.details.culprits` | Fixed `width`/`min-width`, a wide image, `100vw` with a scrollbar, unbroken long string, missing viewport meta |
| Text spilling over a neighbour | `warning` overlaps | Absolute positioning without a positioned ancestor; a collapsed float or grid row |
| Element vanished / behind another | `z_index_conflict`, then `element_context` → `stackingContext` | An ancestor with `transform`, `filter`, `will-change` or `opacity < 1` creates a stacking context and traps the child's z-index |
| Squashed or stretched flex child | `element_context` → parent `display`/`flexDirection` + child `flexGrow`/`flexShrink`/`minWidth` | Missing `min-width: 0`, or `flex-shrink` on the wrong child |
| Content clipped inside a card | `element_context` → `clippingAncestors` | `overflow: hidden` on an ancestor with a too-small computed height |
| Breaks only at one size | `compare_viewports` → `diff.breakpoint_specific` | A media-query boundary; a value that works at only one width |
| Button that cannot be clicked | `accessibility_spatial` → `covered_interactive` | A transparent overlay, or a modal backdrop left mounted |

## Reporting

Lead with the measurement, then the cause, then the fix:

> `.card__title` extends 42px past the right edge at 375x667 (`clipped_right`,
> warning, `causes_page_scroll: true`) — the parent `.card` is a flex row and
> the title has no `min-width: 0`, so it refuses to shrink. Adding it drops the
> overflow to 0px; re-measured at all four breakpoints, clean.

Do not report a raw issue dump as a diagnosis, do not call `info` findings bugs
without arguing intent, and do not claim a fix works without the re-measurement
that proves it. When geometry is correct but the design intent is unclear, say
what the numbers show and ask — the tool quantifies, the human judges.
