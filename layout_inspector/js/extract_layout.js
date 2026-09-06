(args) => {
/* @inject helpers */

const opts = args || {};
const rootSelector = opts.selector || null;
const MAX_ELEMENTS = opts.maxElements || 500;
const WANT_OCCLUSION = opts.occlusion !== false;
const MAX_OCCLUSION_PROBES = 400;
const MAX_CULPRITS = 25;

let root;
if (rootSelector) {
    try {
        root = document.querySelector(rootSelector);
    } catch (e) {
        return {
            error: 'invalid_selector',
            selector: rootSelector,
            message: String((e && e.message) || e)
        };
    }
    if (!root) {
        return { error: 'root_not_found', selector: rootSelector };
    }
} else {
    root = document.body;
    if (!root) return { error: 'no_body' };
}

const de = document.documentElement;
const body = document.body;
const viewport = {
    w: window.innerWidth,
    h: window.innerHeight,
    scrollW: Math.max(de.scrollWidth, body ? body.scrollWidth : 0),
    scrollH: Math.max(de.scrollHeight, body ? body.scrollHeight : 0),
    scrollX: Math.round(window.scrollX),
    scrollY: Math.round(window.scrollY),
    devicePixelRatio: window.devicePixelRatio
};

const viewportMeta = document.querySelector('meta[name="viewport"]');

const elements = [];
const nodeIndex = new Map();
const selectorCache = new Map();
const domNodes = [];
let truncated = false;

// Three clip chains, because different position schemes escape different
// ancestors: normal flow is clipped by every clipping ancestor, absolute only
// by clipping ancestors that are themselves positioned, fixed by none.
function walk(el, depth, parentIdx, clips, inheritedZ, opacityHidden, visHidden, clipperIdx) {
    if (elements.length >= MAX_ELEMENTS) {
        truncated = true;
        return;
    }
    if (el.nodeType !== 1) return;
    const tag = el.tagName.toLowerCase();
    if (LI_SKIP_TAGS.has(tag)) return;

    const style = window.getComputedStyle(el);
    // display:none renders nothing at all — the whole subtree is absent.
    if (style.display === 'none') return;

    const rect = liRect(el.getBoundingClientRect());

    const myClip = style.position === 'fixed'
        ? clips.fixed
        : (style.position === 'absolute' ? clips.abs : clips.static);
    const myClipper = style.position === 'fixed'
        ? clips.fixedBy
        : (style.position === 'absolute' ? clips.absBy : clips.staticBy);

    const nowOpacityHidden = opacityHidden || parseFloat(style.opacity) === 0;
    let nowVisHidden = visHidden;
    if (style.visibility === 'visible') nowVisHidden = false;
    else if (style.visibility === 'hidden' || style.visibility === 'collapse') nowVisHidden = true;
    const visible = !nowOpacityHidden && !nowVisHidden && style.contentVisibility !== 'hidden';

    const ownZ = style.zIndex === 'auto' ? 'auto' : (parseInt(style.zIndex, 10) || 0);
    const effectiveZ = ownZ === 'auto' ? inheritedZ : ownZ;
    const stacking = liStackingReason(el, style);

    // A zero-size element renders nothing itself but can still hold positioned
    // children (a 0-height wrapper around an absolute child is a common
    // pattern). Skip recording it; never skip its subtree.
    const record = rect.w > 0 || rect.h > 0;
    let myIdx = parentIdx;

    if (record) {
        myIdx = elements.length;
        const visibleRect = liIntersect(rect, myClip);
        const clippedX = visibleRect.w < rect.w - 0.5;
        const clippedY = visibleRect.h < rect.h - 0.5;
        const scrollOverflowX = Math.max(0, el.scrollWidth - el.clientWidth);
        const hasText = liHasDirectText(el);

        // Only report truncation when content genuinely exceeds the box.
        // 'overflow:hidden + ellipsis' on a box the text fits inside is not a
        // defect, and a nowrap carousel of element children is not text.
        let textTruncated = false;
        let truncatedPx = 0;
        if (hasText && scrollOverflowX > 1 && liClipsX(style)) {
            if (style.textOverflow === 'ellipsis' || style.whiteSpace === 'nowrap'
                || style.whiteSpace === 'pre') {
                textTruncated = true;
                truncatedPx = liRound(scrollOverflowX);
            }
        }

        // Default values are omitted rather than repeated 500 times: absent
        // means zIndex 'auto', position 'static', visible, no text, and an
        // effective z-index equal to the element's own.
        const entry = {
            index: myIdx,
            parent: parentIdx,
            depth: depth,
            selector: liBuildSelector(el, selectorCache),
            tag: tag,
            rect: rect,
            overflow: style.overflow,
            childrenCount: el.children.length
        };
        if (ownZ !== 'auto') entry.zIndex = ownZ;
        if (effectiveZ !== ownZ) entry.effectiveZIndex = effectiveZ;
        if (style.position !== 'static') entry.computedPosition = style.position;
        if (!visible) entry.visible = false;
        if (hasText) entry.hasText = true;
        if (!liSameRect(visibleRect, rect)) {
            entry.visibleRect = visibleRect;
            entry.clippedByAncestor = clippedX || clippedY;
            if (myClipper >= 0) entry.clippedBy = myClipper;
        }
        if (el.id) entry.id = el.id;
        const cls = liClassTokens(el).join(' ');
        if (cls) entry.classes = cls;
        if (textTruncated) {
            entry.textTruncated = true;
            entry.truncatedPx = truncatedPx;
        }
        if (stacking) entry.stackingContext = stacking;
        if (liIsVisuallyHidden(el, rect, style)) entry.visuallyHidden = true;
        if (liIsInteractive(el)) entry.interactive = true;

        elements.push(entry);
        nodeIndex.set(el, myIdx);
        domNodes.push(el);
    }

    // Clip chains handed to the children.
    const clipsX = liClipsX(style);
    const clipsY = liClipsY(style);
    let childStatic = myClip;
    let childStaticBy = myClipper;
    if (record && (clipsX || clipsY)) {
        const own = {
            x: clipsX ? rect.x : LI_HUGE.x,
            y: clipsY ? rect.y : LI_HUGE.y,
            w: clipsX ? rect.w : LI_HUGE.w,
            h: clipsY ? rect.h : LI_HUGE.h
        };
        childStatic = liIntersect(myClip, own);
        childStaticBy = myIdx;
    }
    const positioned = style.position !== 'static';
    const childAbs = positioned ? childStatic : clips.abs;
    const childAbsBy = positioned ? childStaticBy : clips.absBy;

    const childClips = {
        static: childStatic,
        staticBy: childStaticBy,
        abs: childAbs,
        absBy: childAbsBy,
        fixed: clips.fixed,
        fixedBy: clips.fixedBy
    };

    for (let i = 0; i < el.children.length; i++) {
        walk(el.children[i], depth + 1, record ? myIdx : parentIdx, childClips,
             effectiveZ, nowOpacityHidden, nowVisHidden, myClipper);
    }
}

const initialClips = {
    static: LI_HUGE, staticBy: -1,
    abs: LI_HUGE, absBy: -1,
    fixed: LI_HUGE, fixedBy: -1
};
walk(root, 0, -1, initialClips, 'auto', false, false, -1);

// ── Occlusion pass ────────────────────────────────────────────
// elementFromPoint is the only reliable answer to "what actually paints on
// top here" — it accounts for stacking contexts, which pairwise z-index
// comparison cannot.
if (WANT_OCCLUSION) {
    let probed = 0;
    for (let i = 0; i < elements.length && probed < MAX_OCCLUSION_PROBES; i++) {
        const entry = elements[i];
        if (entry.visible === false || entry.visuallyHidden) continue;
        if (!entry.hasText && !entry.interactive) continue;
        const vr = entry.visibleRect || entry.rect;
        if (vr.w < 6 || vr.h < 6) continue;

        const el = domNodes[i];
        const points = [];
        const fractions = [0.5, 0.25, 0.75];
        for (let a = 0; a < fractions.length; a++) {
            for (let b = 0; b < fractions.length; b++) {
                const px = vr.x + vr.w * fractions[a];
                const py = vr.y + vr.h * fractions[b];
                if (px < 0 || py < 0 || px >= viewport.w || py >= viewport.h) continue;
                points.push([px, py]);
            }
        }
        if (!points.length) continue;
        probed++;

        let covered = 0;
        const tally = new Map();
        for (let p = 0; p < points.length; p++) {
            const top = document.elementFromPoint(points[p][0], points[p][1]);
            if (!top) continue;
            if (top === el || el.contains(top) || top.contains(el)) continue;
            covered++;
            let anc = top;
            let idx;
            while (anc && (idx = nodeIndex.get(anc)) === undefined) anc = anc.parentElement;
            if (idx !== undefined) tally.set(idx, (tally.get(idx) || 0) + 1);
        }
        if (!covered) continue;

        let best = -1;
        let bestCount = 0;
        tally.forEach(function (count, idx) {
            if (count > bestCount) { bestCount = count; best = idx; }
        });
        entry.occludedRatio = liRound(covered / points.length);
        if (best >= 0) entry.occludedBy = best;
    }
}

// ── Horizontal overflow culprits ─────────────────────────────
// Scanned over the whole document, not just the recorded elements, so a
// culprit past the element cap is still named.
const clipMemo = new Map();
function clipsSelfOrAncestorX(el) {
    if (!el || el.nodeType !== 1) return false;
    if (clipMemo.has(el)) return clipMemo.get(el);
    let value;
    try {
        const s = window.getComputedStyle(el);
        value = liClipsX(s) || s.position === 'fixed' || clipsSelfOrAncestorX(el.parentElement);
    } catch (e) {
        value = false;
    }
    clipMemo.set(el, value);
    return value;
}

const overflowCulprits = [];
const pageScrollsX = viewport.scrollW > viewport.w + 1;
if (pageScrollsX && body) {
    const all = body.querySelectorAll('*');
    const limit = Math.min(all.length, 8000);
    for (let i = 0; i < limit && overflowCulprits.length < MAX_CULPRITS; i++) {
        const el = all[i];
        if (LI_SKIP_TAGS.has(el.tagName.toLowerCase())) continue;
        let style;
        try {
            style = window.getComputedStyle(el);
        } catch (e) {
            continue;
        }
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        // Fixed elements do not contribute to document scroll width.
        if (style.position === 'fixed') continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        const right = r.x + r.width + viewport.scrollX;
        if (right <= viewport.w + 1) continue;
        if (clipsSelfOrAncestorX(el.parentElement)) continue;
        if (liIsVisuallyHidden(el, liRect(r), style)) continue;
        overflowCulprits.push({
            selector: liBuildSelector(el, selectorCache),
            rect: liRect(r),
            overflow_px: liRound(right - viewport.w)
        });
    }
    overflowCulprits.sort(function (a, b) { return b.overflow_px - a.overflow_px; });
}

return {
    viewport: viewport,
    elements: elements,
    truncated: truncated,
    max_elements: MAX_ELEMENTS,
    page_scrolls_horizontally: pageScrollsX,
    has_viewport_meta: !!viewportMeta,
    viewport_meta: viewportMeta ? viewportMeta.getAttribute('content') : null,
    overflow_culprits: overflowCulprits
};
}
