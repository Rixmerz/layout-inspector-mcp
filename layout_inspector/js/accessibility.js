(args) => {
/* @inject helpers */

const opts = args || {};
const rootSelector = opts.selector || null;
// WCAG 2.2 SC 2.5.8 Target Size (Minimum) is level AA at 24x24 CSS px.
// SC 2.5.5 Target Size (Enhanced) is level AAA at 44x44.
const MIN_AA = opts.minAA || 24;
const MIN_AAA = opts.minAAA || 44;

let root;
if (rootSelector) {
    try {
        root = document.querySelector(rootSelector);
    } catch (e) {
        return { error: 'invalid_selector', selector: rootSelector, message: String((e && e.message) || e) };
    }
    if (!root) return { error: 'root_not_found', selector: rootSelector };
} else {
    root = document.body;
    if (!root) return { error: 'no_body' };
}

const cache = new Map();
const candidates = Array.prototype.slice.call(root.querySelectorAll(LI_INTERACTIVE));
if (root.matches && liIsInteractive(root)) candidates.unshift(root);

const targets = [];
const skipped = { hidden: 0, disabled: 0, not_focusable: 0, no_pointer: 0 };

for (let i = 0; i < candidates.length; i++) {
    const el = candidates[i];
    let style;
    try {
        style = window.getComputedStyle(el);
    } catch (e) {
        continue;
    }
    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) {
        skipped.hidden++;
        continue;
    }
    if (el.disabled === true || el.hasAttribute('disabled') || el.closest('[inert]')) {
        skipped.disabled++;
        continue;
    }
    if (style.pointerEvents === 'none') {
        skipped.no_pointer++;
        continue;
    }
    const rect = liRect(el.getBoundingClientRect());
    if (rect.w === 0 && rect.h === 0) {
        skipped.hidden++;
        continue;
    }
    if (liIsVisuallyHidden(el, rect, style)) {
        skipped.hidden++;
        continue;
    }
    // tabindex="-1" is reachable by script/pointer but is not a keyboard target;
    // WCAG target-size applies to pointer targets, so keep it only if it is a
    // real control rather than a scroll anchor.
    const ti = el.getAttribute('tabindex');
    if (ti !== null && parseInt(ti, 10) < 0 && !el.matches('a[href], button, input, select, textarea, [role]')) {
        skipped.not_focusable++;
        continue;
    }
    targets.push({ el: el, rect: rect, style: style });
}

// SC 2.5.8 "Inline" exception: the target sits inside a sentence, so its size
// is constrained by the surrounding line of text.
function isInlineInText(t) {
    // The exception is for a target whose size is constrained by the line-height
    // of surrounding text. An inline-block button in a toolbar is not that.
    if (t.style.display !== 'inline') return false;
    const parent = t.el.parentElement;
    if (!parent) return false;
    let surrounding = 0;
    for (let n = parent.firstChild; n; n = n.nextSibling) {
        if (n.nodeType === 3 && n.nodeValue) surrounding += n.nodeValue.trim().length;
    }
    return surrounding > 0;
}

// SC 2.5.8 "Spacing" exception: a 24px circle centred on the target does not
// intersect the circle of any other target.
function hasSpacing(t, index) {
    const r = t.rect;
    const cx = r.x + r.w / 2;
    const cy = r.y + r.h / 2;
    const radius = MIN_AA / 2;
    for (let j = 0; j < targets.length; j++) {
        if (j === index) continue;
        const o = targets[j].rect;
        const ox = o.x + o.w / 2;
        const oy = o.y + o.h / 2;
        const dx = cx - ox;
        const dy = cy - oy;
        if (Math.sqrt(dx * dx + dy * dy) < MIN_AA) return false;
    }
    return true;
}

const issues = [];
for (let i = 0; i < targets.length; i++) {
    const t = targets[i];
    const r = t.rect;
    const selector = liBuildSelector(t.el, cache);
    const smallest = Math.min(r.w, r.h);

    if (smallest < MIN_AAA) {
        const inline = isInlineInText(t);
        const spaced = hasSpacing(t, i);
        if (smallest < MIN_AA && !inline && !spaced) {
            issues.push({
                type: 'small_touch_target',
                severity: 'error',
                element: selector,
                criterion: 'WCAG 2.2 SC 2.5.8 Target Size (Minimum), level AA',
                size: { w: r.w, h: r.h },
                minimum: MIN_AA,
                description: 'Target is ' + r.w + 'x' + r.h + 'px, under the ' + MIN_AA + 'px AA minimum, and neither inline in text nor spaced ' + MIN_AA + 'px from its neighbours.'
            });
        } else if (smallest < MIN_AAA && !inline) {
            issues.push({
                type: 'small_touch_target_aaa',
                severity: 'info',
                element: selector,
                criterion: 'WCAG 2.2 SC 2.5.5 Target Size (Enhanced), level AAA',
                size: { w: r.w, h: r.h },
                minimum: MIN_AAA,
                exempt_from_aa: smallest >= MIN_AA ? 'meets AA' : (spaced ? 'spacing exception' : 'inline exception'),
                description: 'Target is ' + r.w + 'x' + r.h + 'px, under the ' + MIN_AAA + 'px AAA target but not an AA failure.'
            });
        }
    }

    // Coverage: sample the target rather than only its centre, so a control
    // half-under an overlay is not silently passed.
    const points = [];
    const fractions = [0.5, 0.3, 0.7];
    for (let a = 0; a < fractions.length; a++) {
        for (let b = 0; b < fractions.length; b++) {
            const px = r.x + r.w * fractions[a];
            const py = r.y + r.h * fractions[b];
            if (px < 0 || py < 0 || px >= window.innerWidth || py >= window.innerHeight) continue;
            points.push([px, py]);
        }
    }
    if (!points.length) continue;

    let covered = 0;
    let topSelector = null;
    for (let p = 0; p < points.length; p++) {
        const top = document.elementFromPoint(points[p][0], points[p][1]);
        if (!top) continue;
        if (top === t.el || t.el.contains(top) || top.contains(t.el)) continue;
        covered++;
        if (!topSelector) topSelector = liBuildSelector(top, cache);
    }
    if (covered === points.length) {
        issues.push({
            type: 'covered_interactive',
            severity: 'error',
            element: selector,
            coveredBy: topSelector,
            coverage_ratio: 1,
            description: 'Every sampled point of this control is painted over by ' + topSelector + '; it cannot be clicked.'
        });
    } else if (covered > points.length / 2) {
        issues.push({
            type: 'partially_covered_interactive',
            severity: 'warning',
            element: selector,
            coveredBy: topSelector,
            coverage_ratio: liRound(covered / points.length),
            description: 'Most of this control is painted over by ' + topSelector + '.'
        });
    }
}

const order = { error: 0, warning: 1, info: 2 };
issues.sort(function (a, b) { return order[a.severity] - order[b.severity]; });

return {
    totalInteractive: targets.length,
    skipped: skipped,
    thresholds: { aa: MIN_AA, aaa: MIN_AAA },
    issues_count: issues.length,
    issues: issues
};
}
