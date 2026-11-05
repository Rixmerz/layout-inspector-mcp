(args) => {
/* @inject helpers */

// Compares what the page renders with the design system it is built on.
// The system's tokens are read from the page itself: the CSS custom
// properties its stylesheets define (optionally only those with a prefix),
// plus any values the caller passes (a brand overlay's). Each element's
// computed color, background, border, font size, radius and spacing is then
// checked against them, and text contrast is measured on the real render.

const opts = args || {};
const rootSelector = opts.selector || null;
const prefixes = (opts.prefixes || []).filter(Boolean);
const extra = opts.extra || [];
const MAX = opts.maxElements || 3000;
const MAX_EXAMPLES = 3;

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

const selCache = new Map();

// ── Read the tokens ─────────────────────────────────────────────
const declared = new Map(); // custom property -> a declared value
let unreadableSheets = 0;
function walkRules(rules) {
    for (let i = 0; i < rules.length; i++) {
        const r = rules[i];
        if (r.style) {
            for (let j = 0; j < r.style.length; j++) {
                const p = r.style[j];
                if (p.startsWith('--') && !declared.has(p)) declared.set(p, r.style.getPropertyValue(p).trim());
            }
        }
        if (r.cssRules) walkRules(r.cssRules);
    }
}
for (let i = 0; i < document.styleSheets.length; i++) {
    try {
        walkRules(document.styleSheets[i].cssRules);
    } catch (e) {
        unreadableSheets++;
    }
}
const html = document.documentElement;
for (let j = 0; j < html.style.length; j++) {
    const p = html.style[j];
    if (p.startsWith('--')) declared.set(p, html.style.getPropertyValue(p).trim());
}

const probe = document.createElement('div');
probe.style.cssText = 'position:absolute;visibility:hidden;pointer-events:none;width:0;height:0;';
(document.body || html).appendChild(probe);

// A custom property's computed value has its var() references substituted;
// CSS.supports says what kind of value it is, and a probe normalises it.
function asColor(value) {
    if (!value || !CSS.supports('color', value)) return null;
    probe.style.color = value;
    const c = getComputedStyle(probe).color;
    probe.style.color = '';
    return c;
}
function asLength(value) {
    if (!value || !CSS.supports('padding-left', value) || /^[0-9.]+$/.test(value)) return null;
    probe.style.paddingLeft = value;
    const v = getComputedStyle(probe).paddingLeft;
    probe.style.paddingLeft = '';
    return v;
}
const rootStyle = getComputedStyle(html);

const colorTokens = new Map();   // rgb string -> token names
const lengthTokens = new Map();  // px string -> token names
let tokensRead = 0;
declared.forEach((raw, name) => {
    if (prefixes.length && !prefixes.some((p) => name.startsWith(p))) return;
    tokensRead++;
    const ref = rootStyle.getPropertyValue(name).trim() || raw;
    const c = asColor(ref);
    if (c) {
        if (!colorTokens.has(c)) colorTokens.set(c, []);
        colorTokens.get(c).push(name);
        return;
    }
    const l = asLength(ref);
    if (l) {
        if (!lengthTokens.has(l)) lengthTokens.set(l, []);
        lengthTokens.get(l).push(name);
    }
});
extra.forEach((value) => {
    const c = asColor(String(value));
    if (c) {
        if (!colorTokens.has(c)) colorTokens.set(c, []);
        colorTokens.get(c).push('overlay:' + value);
        return;
    }
    const l = asLength(String(value));
    if (l) {
        if (!lengthTokens.has(l)) lengthTokens.set(l, []);
        lengthTokens.get(l).push('overlay:' + value);
    }
});
probe.remove();

// ── Colors and contrast ─────────────────────────────────────────
function parseRgb(s) {
    const m = /rgba?\(([^)]+)\)/.exec(s || '');
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
}
function key(c) {
    return 'rgb(' + c.r + ', ' + c.g + ', ' + c.b + ')' + (c.a < 1 ? ' @' + liRound(c.a) : '');
}
function lum(c) {
    const ch = [c.r, c.g, c.b].map((v) => {
        v /= 255;
        return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
}
function blend(top, bottom) {
    const a = top.a;
    return {
        r: Math.round(top.r * a + bottom.r * (1 - a)),
        g: Math.round(top.g * a + bottom.g * (1 - a)),
        b: Math.round(top.b * a + bottom.b * (1 - a)),
        a: 1
    };
}
const bgCache = new Map();
// The color painted behind an element: its own background over its
// ancestors', or null when an image or gradient makes it unknowable.
function backdrop(el) {
    if (!el || el.nodeType !== 1) return { r: 255, g: 255, b: 255, a: 1 };
    if (bgCache.has(el)) return bgCache.get(el);
    const s = getComputedStyle(el);
    let result;
    if (s.backgroundImage && s.backgroundImage !== 'none') {
        result = null;
    } else {
        const own = parseRgb(s.backgroundColor);
        const under = backdrop(el.parentElement);
        if (!own || own.a === 0) result = under;
        else if (own.a >= 1 || !under) result = own.a >= 1 ? own : null;
        else result = blend(own, under);
    }
    bgCache.set(el, result);
    return result;
}
function ratio(a, b) {
    const x = lum(a), y = lum(b);
    return liRound((Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05));
}

// ── Walk the elements ───────────────────────────────────────────
const off = { color: new Map(), background: new Map(), border: new Map(), font_size: new Map(), radius: new Map(), spacing: new Map() };
function note(kind, value, el) {
    const m = off[kind];
    if (!m.has(value)) m.set(value, { value: value, count: 0, examples: [] });
    const e = m.get(value);
    e.count++;
    if (e.examples.length < MAX_EXAMPLES) e.examples.push(liBuildSelector(el, selCache));
}
function colorOk(c) {
    if (!c || c.a === 0) return true;
    return colorTokens.has('rgb(' + c.r + ', ' + c.g + ', ' + c.b + ')') ||
        (c.a < 1 && [...colorTokens.keys()].some((k) => k.startsWith('rgba(' + c.r + ', ' + c.g + ', ' + c.b)));
}
function lengthOk(v) {
    return v === '0px' || v === 'auto' || v === 'normal' || lengthTokens.has(v);
}

const contrast = [];
let checked = 0, unknownBackdrop = 0;
const all = root.querySelectorAll('*');
const els = [root].concat(Array.prototype.slice.call(all));
for (let i = 0; i < els.length && checked < MAX; i++) {
    const el = els[i];
    if (LI_SKIP_TAGS.has(el.tagName.toLowerCase())) continue;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    checked++;
    const parent = el.parentElement ? getComputedStyle(el.parentElement) : null;

    const fg = parseRgb(s.color);
    const ownText = liHasDirectText(el);
    // Inherited values are judged once, where they are set.
    if (ownText && fg && (!parent || parent.color !== s.color || el === root) && !colorOk(fg)) note('color', key(fg), el);
    const bg = parseRgb(s.backgroundColor);
    if (bg && bg.a > 0 && !colorOk(bg)) note('background', key(bg), el);
    if (parseFloat(s.borderTopWidth) > 0 && s.borderTopStyle !== 'none') {
        const bc = parseRgb(s.borderTopColor);
        if (bc && !colorOk(bc)) note('border', key(bc), el);
    }
    if (ownText && (!parent || parent.fontSize !== s.fontSize || el === root) && !lengthOk(s.fontSize)) note('font_size', s.fontSize, el);
    if (!lengthOk(s.borderTopLeftRadius)) note('radius', s.borderTopLeftRadius, el);
    ['paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'marginTop', 'marginBottom', 'rowGap', 'columnGap'].forEach((p) => {
        const v = s[p];
        if (v && v.endsWith('px') && !v.startsWith('-') && !lengthOk(v)) note('spacing', v, el);
    });

    if (ownText && fg) {
        const back = backdrop(el);
        if (!back) {
            unknownBackdrop++;
        } else {
            const shown = fg.a < 1 ? blend(fg, back) : fg;
            const r = ratio(shown, back);
            const size = parseFloat(s.fontSize);
            const bold = parseInt(s.fontWeight, 10) >= 700;
            const large = size >= 24 || (bold && size >= 18.66);
            const need = large ? 3 : 4.5;
            if (r < need) {
                contrast.push({
                    selector: liBuildSelector(el, selCache),
                    text: el.textContent.trim().slice(0, 60),
                    ratio: r,
                    required: need,
                    large_text: large,
                    color: key(shown),
                    background: key(back)
                });
            }
        }
    }
}

function list(m) {
    return [...m.values()].sort((a, b) => b.count - a.count);
}
const offToken = {};
Object.keys(off).forEach((k) => { offToken[k] = list(off[k]); });

return {
    tokens: {
        read: tokensRead,
        colors: colorTokens.size,
        lengths: lengthTokens.size,
        prefixes: prefixes,
        overlay_values: extra.length,
        unreadable_stylesheets: unreadableSheets
    },
    elements_checked: checked,
    element_cap_reached: checked >= MAX,
    off_token: offToken,
    contrast_failures: contrast,
    contrast_unknown: unknownBackdrop
};
}
