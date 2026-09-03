---
name: layout-inspection
description: Diagnose and fix rendered web layout defects by measuring geometry with the layout-inspector MCP server (detect_issues, inspect_layout, element_context, compare_viewports, accessibility_spatial). Use whenever someone reports that a page looks broken, overlapping, cut off, misaligned, squashed on mobile, horizontally scrolling, or clipped — and whenever a CSS/layout change needs verification that it did not break another breakpoint. Also use for touch-target and covered-control accessibility checks, and for auditing a page before shipping. Trigger even when the user only says "se ve mal", "se superpone", "se corta el texto", "no se ve en mobile", "revisa el layout", or pastes a screenshot of a broken UI — the point of this skill is to replace eyeballing pixels with measured geometry.
---

# Layout Inspection

Measure layout, don't look at it. `getBoundingClientRect()` on every element,
cross-referenced for intersections, is deterministic; judging a screenshot is
probabilistic. A screenshot confirms a finding at the end — it never finds one.

## The five tools

All take `url` (an `http(s)://` address or a `file://` path).

| Tool | Use it for |
|---|---|
| `detect_issues(url, viewport_width?, viewport_height?, root_selector?, wait_for?)` | Default entry point. Returns issues with type, severity, element, and quantified detail. |
| `inspect_layout(url, ...same args)` | Raw layout tree when you need coordinates `detect_issues` did not flag (below-the-fold positions, sibling alignment, actual widths). |
| `element_context(url, selector, viewport_width?, viewport_height?)` | Root cause for one element: computed styles, parent box, sibling rects, the full stacking-context chain. |
| `compare_viewports(url, viewports?, root_selector?, wait_for?)` | Responsive regressions. Default `375x667,768x1024,1280x720,1920x1080`. |
| `accessibility_spatial(url, viewport_width?, viewport_height?)` | Touch targets under WCAG 44px, and interactive elements covered by something else. |

Viewport defaults everywhere else are `1280x720`. `root_selector` scopes the
scan to a subtree; `wait_for` blocks on a selector for late-rendering content.

## The loop

1. **Baseline.** `detect_issues` at the viewport where the user saw the problem.
   If they did not say, run `compare_viewports` — the breakpoint is usually the
   answer.
2. **Triage by severity, not by count.** See the table below. A page with 40
   `info` overlaps and one `warning` has exactly one finding.
3. **Root cause with `element_context`** on the flagged selector before touching
   any CSS. The stacking chain and the parent's `display`/`overflow` explain the
   geometry; guessing from the issue description alone produces wrong fixes.
4. **Fix the cause**, minimally.
5. **Re-measure** the same tool call. The issue is gone or it is not — never
   report a fix you did not re-measure.
6. **Re-run `compare_viewports`.** Layout fixes routinely trade a desktop bug
   for a mobile one.

## Reading severity

Severity encodes authorial intent, which is the whole point of the signal:

- `overlap` + **`warning`** — both elements sit at `z-index: auto`. Nobody asked
  for this stacking. Almost always the real bug: something escaping its
  container onto a neighbour. **Start here.**
- `overlap` + **`info`** — at least one has an explicit z-index, so the author
  stacked them deliberately: badge, tooltip, modal, sticky header. Reported
  because the intent can still be wrong, but never lead with these.
- `offscreen_horizontal` — **`error`**. The element is entirely outside the
  viewport horizontally. Real, always.
- `clipped_right` — **`warning`**, with `overflow_px`. This is what causes
  horizontal scroll on mobile. The classic mobile complaint.
- `clipped_left` — **`warning`**; `clipped_top` — `info`; `offscreen_top` —
  `warning` (often a deliberately hidden drawer or menu).
- `text_truncated` — **`info`**. Ellipsis or `nowrap` overflow. Frequently
  intentional (a one-line label); judge against the design.

Nesting is never reported: a child always intersects its container, and
reporting that buries the real findings. Intersections under **100px²** and
elements narrower or shorter than **5px** are skipped, so hairline borders and
sub-pixel offsets do not drown the signal.

## What the numbers actually mean

- **Coordinates are viewport-relative at scroll position 0.** Nothing scrolls.
  An element below the fold legitimately has `y > viewport_height` and is *not*
  flagged — there is no "off-screen bottom" issue type, by design. To reason
  about vertical placement, read `y`/`h` from `inspect_layout` and compare with
  `viewport.scrollH`.
- **`viewport.scrollW > viewport.w` means the page scrolls horizontally.** Check
  this in `inspect_layout` even when no `clipped_right` fired; the culprit may
  be past element 500.
- **The scan stops at 500 elements**, depth-first. On a large page the tail is
  simply not measured. If `total_elements` is 500, scope with `root_selector`
  and scan sections one at a time before concluding the page is clean.
- **Selectors are heuristic**: `#id` when present, otherwise
  `tag.class1.class2` plus `:nth-of-type(n)` among same-tag siblings. They are
  descriptive, not guaranteed-unique. If `element_context` returns
  `Element not found`, the selector was ambiguous — build a real one from the
  `classes`/`id` fields in `inspect_layout`.
- **`z_a`/`z_b` are `"auto"` or an integer.** An unparseable z-index falls back
  to `0`, so a reported `0` may mean "explicitly 0" or "malformed value".
- **Overlap pairs are ordered by DOM position**: `element` appears before
  `details.other` in the tree. It is not a claim about which one is at fault.
- **`compare_viewports` gives per-viewport counts, not deltas.** Its `summary`
  is `total_issues` and `errors_and_warnings` per size. To find the actual
  responsive regression, diff `details[].issues` across viewports yourself: an
  issue present at 375 and absent at 1280 is the breakpoint bug.
- **`accessibility_spatial` takes no `root_selector` and no `wait_for`**, and
  its `covered_interactive` check probes the element's centre point with
  `elementFromPoint`. A control whose centre is under its own overlay,
  decorative pseudo-element, or a full-screen modal reports as covered. Verify
  each hit with `element_context` before calling it a defect.

## Getting the page to render

- `goto` waits for `networkidle` with a 15s timeout. Pages with a persistent
  WebSocket, poller, or analytics beacon may never go idle and will time out.
  Prefer a static `file://` build, or point at a page whose network settles.
- `wait_for` has its own 5s timeout and runs after load — use it for content
  behind a fetch, a framework mount, or an animation.
- Animations and transitions are measured wherever they happen to be. If rects
  look impossible, an entrance animation was mid-flight; `wait_for` a selector
  that only exists once the animation settles.
- Chromium only. Firefox/WebKit differences are out of scope, and saying so is
  better than implying coverage the tool does not have.

## Common defects and where to look

| Symptom | First measurement | Usual cause |
|---|---|---|
| Horizontal scroll on mobile | `detect_issues` at `375x667`, read `clipped_right.overflow_px` | Fixed `width`/`min-width`, a wide image, `100vw` with a scrollbar, unbroken long string |
| Text spilling over a neighbour | `warning` overlaps | Absolute positioning without a positioned ancestor; a collapsed float or grid row |
| Element vanished / behind another | `element_context` → `stackingContext` | An ancestor with `transform`, `filter`, or `opacity < 1` creates a stacking context and traps the child's z-index |
| Squashed or stretched flex child | `element_context` → parent `display` + child `flexGrow`, compare `rect.w` with `computedStyle.width` | Missing `min-width: 0`, or `flex-shrink` on the wrong child |
| Content clipped inside a card | `element_context` → parent `overflow` | `overflow: hidden` on an ancestor with a too-small computed height |
| Breaks only at one size | `compare_viewports`, then diff the issue lists | A media-query boundary; a value that works at only one width |

## Reporting

Lead with the measurement, then the cause, then the fix:

> `.card__title` extends 42px past the right edge at 375x667 (`clipped_right`,
> warning) — the parent `.card` is a flex row and the title has no
> `min-width: 0`, so it refuses to shrink. Adding it drops the overflow to 0px;
> re-measured at all four breakpoints, clean.

Do not report a raw issue dump as a diagnosis, do not call `info` findings bugs
without arguing intent, and do not claim a fix works without the re-measurement
that proves it. When geometry is correct but the design intent is unclear, say
what the numbers show and ask — the tool quantifies, the human judges.
