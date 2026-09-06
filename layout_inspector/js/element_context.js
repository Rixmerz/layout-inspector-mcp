(args) => {
/* @inject helpers */

const opts = args || {};
const selector = opts.selector;

let matches;
try {
    matches = document.querySelectorAll(selector);
} catch (e) {
    return {
        error: 'invalid_selector',
        selector: selector,
        message: String((e && e.message) || e)
    };
}
if (!matches.length) {
    return { error: 'not_found', selector: selector };
}

const el = matches[0];
const style = window.getComputedStyle(el);
const rect = liRect(el.getBoundingClientRect());
const parent = el.parentElement;
const cache = new Map();

function styleOf(node, keys) {
    const s = window.getComputedStyle(node);
    const out = {};
    for (let i = 0; i < keys.length; i++) out[keys[i]] = s[keys[i]];
    return out;
}

const SELF_KEYS = [
    'position', 'display', 'zIndex', 'overflow', 'overflowX', 'overflowY',
    'margin', 'padding', 'border', 'boxSizing',
    'width', 'height', 'minWidth', 'minHeight', 'maxWidth', 'maxHeight',
    'flexGrow', 'flexShrink', 'flexBasis', 'alignSelf', 'gridArea',
    'whiteSpace', 'wordBreak', 'overflowWrap', 'textOverflow',
    'transform', 'opacity', 'filter', 'isolation', 'mixBlendMode', 'willChange',
    'contain', 'float', 'inset', 'aspectRatio', 'visibility', 'pointerEvents'
];
const PARENT_KEYS = [
    'display', 'position', 'overflow', 'overflowX', 'overflowY',
    'flexDirection', 'flexWrap', 'alignItems', 'justifyContent', 'gap',
    'gridTemplateColumns', 'gridTemplateRows', 'width', 'height',
    'minWidth', 'padding', 'boxSizing'
];

// Every ancestor that creates a stacking context, and the property that does
// it. This chain is what traps a child's z-index.
const stackingContext = [];
let ancestor = el;
while (ancestor && ancestor.nodeType === 1) {
    const s = window.getComputedStyle(ancestor);
    const reason = liStackingReason(ancestor, s);
    if (reason) {
        stackingContext.push({
            selector: liBuildSelector(ancestor, cache),
            tag: ancestor.tagName.toLowerCase(),
            id: ancestor.id || null,
            zIndex: s.zIndex,
            position: s.position,
            creates_stacking_context_because: reason,
            is_self: ancestor === el
        });
    }
    if (ancestor === document.documentElement) break;
    ancestor = ancestor.parentElement;
}

// Every ancestor that clips this element, with the surviving visible box.
const clippingAncestors = [];
let clipRect = { x: LI_HUGE.x, y: LI_HUGE.y, w: LI_HUGE.w, h: LI_HUGE.h };
let node = el.parentElement;
let childPosition = style.position;
while (node && node.nodeType === 1) {
    const s = window.getComputedStyle(node);
    const escapes = (childPosition === 'fixed')
        || (childPosition === 'absolute' && s.position === 'static');
    if (!escapes && (liClipsX(s) || liClipsY(s))) {
        const r = liRect(node.getBoundingClientRect());
        const own = {
            x: liClipsX(s) ? r.x : LI_HUGE.x,
            y: liClipsY(s) ? r.y : LI_HUGE.y,
            w: liClipsX(s) ? r.w : LI_HUGE.w,
            h: liClipsY(s) ? r.h : LI_HUGE.h
        };
        clipRect = liIntersect(clipRect, own);
        clippingAncestors.push({
            selector: liBuildSelector(node, cache),
            overflow: s.overflow,
            rect: r
        });
    }
    if (s.position !== 'static') childPosition = 'static';
    if (node === document.documentElement) break;
    node = node.parentElement;
}
const visibleRect = liIntersect(rect, clipRect);

// What actually paints on top of this element's centre.
let occludedBy = null;
const cx = rect.x + rect.w / 2;
const cy = rect.y + rect.h / 2;
if (cx >= 0 && cy >= 0 && cx < window.innerWidth && cy < window.innerHeight) {
    const top = document.elementFromPoint(cx, cy);
    if (top && top !== el && !el.contains(top) && !top.contains(el)) {
        const ts = window.getComputedStyle(top);
        occludedBy = {
            selector: liBuildSelector(top, cache),
            zIndex: ts.zIndex,
            position: ts.position,
            stacking_context_because: liStackingReason(top, ts)
        };
    }
}

const siblings = parent
    ? Array.prototype.slice.call(parent.children)
        .filter(function (c) { return c !== el && c.nodeType === 1 && !LI_SKIP_TAGS.has(c.tagName.toLowerCase()); })
        .slice(0, 12)
        .map(function (c) {
            return {
                selector: liBuildSelector(c, cache),
                rect: liRect(c.getBoundingClientRect()),
                display: window.getComputedStyle(c).display
            };
        })
    : [];

return {
    selector: selector,
    matched: matches.length,
    element: {
        tag: el.tagName.toLowerCase(),
        id: el.id || '',
        classes: liClassTokens(el).join(' '),
        selector: liBuildSelector(el, cache),
        rect: rect,
        visibleRect: visibleRect,
        clipped_by_ancestor: !liSameRect(visibleRect, rect),
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
        content_overflows_x: el.scrollWidth > el.clientWidth + 1,
        content_overflows_y: el.scrollHeight > el.clientHeight + 1,
        has_direct_text: liHasDirectText(el),
        interactive: liIsInteractive(el),
        visually_hidden: liIsVisuallyHidden(el, rect, style),
        computedStyle: styleOf(el, SELF_KEYS)
    },
    parent: parent ? {
        selector: liBuildSelector(parent, cache),
        tag: parent.tagName.toLowerCase(),
        rect: liRect(parent.getBoundingClientRect()),
        computedStyle: styleOf(parent, PARENT_KEYS)
    } : null,
    siblings: siblings,
    stackingContext: stackingContext,
    clippingAncestors: clippingAncestors,
    occludedBy: occludedBy,
    viewport: {
        w: window.innerWidth,
        h: window.innerHeight,
        scrollW: document.documentElement.scrollWidth,
        scrollH: document.documentElement.scrollHeight
    }
};
}
