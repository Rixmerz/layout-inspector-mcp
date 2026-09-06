// Shared helpers injected into every extraction script.
// Kept as plain function declarations so they can be spliced into the body
// of the arrow function each script exports.

var LI_SKIP_TAGS = new Set([
    'script', 'style', 'link', 'meta', 'head', 'noscript', 'template', 'title', 'base'
]);

var LI_INTERACTIVE = [
    'a[href]', 'button', 'input:not([type="hidden"])', 'select', 'textarea',
    'summary', 'label[for]', '[contenteditable="true"]',
    '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
    '[role="tab"]', '[role="menuitem"]', '[role="switch"]', '[role="option"]',
    '[role="slider"]', '[role="spinbutton"]', '[role="textbox"]', '[role="combobox"]',
    '[tabindex]'
].join(', ');

var LI_HUGE = { x: -1e7, y: -1e7, w: 2e7, h: 2e7 };

function liRound(n) {
    return Math.round(n * 100) / 100;
}

function liRect(domRect) {
    return {
        x: liRound(domRect.x),
        y: liRound(domRect.y),
        w: liRound(domRect.width),
        h: liRound(domRect.height)
    };
}

function liIntersect(a, b) {
    var x = Math.max(a.x, b.x);
    var y = Math.max(a.y, b.y);
    var r = Math.min(a.x + a.w, b.x + b.w);
    var bt = Math.min(a.y + a.h, b.y + b.h);
    return { x: liRound(x), y: liRound(y), w: liRound(Math.max(0, r - x)), h: liRound(Math.max(0, bt - y)) };
}

function liSameRect(a, b) {
    return Math.abs(a.x - b.x) < 0.5 && Math.abs(a.y - b.y) < 0.5
        && Math.abs(a.w - b.w) < 0.5 && Math.abs(a.h - b.h) < 0.5;
}

function liClassTokens(el) {
    try {
        return Array.prototype.slice.call(el.classList);
    } catch (e) {
        return [];
    }
}

// One path segment: '#id' when the element carries one, otherwise
// tag + up to two escaped classes + :nth-of-type() among same-tag siblings.
function liSelectorPart(el) {
    if (el.id) {
        return '#' + CSS.escape(el.id);
    }
    var part = el.tagName.toLowerCase();
    var tokens = liClassTokens(el).slice(0, 2);
    for (var i = 0; i < tokens.length; i++) {
        // Tailwind and BEM classes contain ':' '/' '.' '[' — all of which need
        // escaping or the resulting selector throws in querySelector().
        part += '.' + CSS.escape(tokens[i]);
    }
    var parent = el.parentElement;
    if (parent) {
        var sameTag = [];
        for (var c = 0; c < parent.children.length; c++) {
            if (parent.children[c].tagName === el.tagName) sameTag.push(parent.children[c]);
        }
        if (sameTag.length > 1) {
            part += ':nth-of-type(' + (sameTag.indexOf(el) + 1) + ')';
        }
    }
    return part;
}

// Shortest ancestor path that resolves back to exactly this element.
// Verified with querySelectorAll, so a returned selector is usable as-is.
function liBuildSelector(el, cache) {
    if (cache && cache.has(el)) return cache.get(el);
    var chain = [];
    var cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
        chain.unshift(liSelectorPart(cur));
        if (cur.id) break;
        cur = cur.parentElement;
        if (chain.length >= 8) break;
    }
    var result = chain.join(' > ');
    for (var i = chain.length - 1; i >= 0; i--) {
        var candidate = chain.slice(i).join(' > ');
        try {
            var found = document.querySelectorAll(candidate);
            if (found.length === 1 && found[0] === el) {
                result = candidate;
                break;
            }
        } catch (e) {
            // Unescapable selector — keep walking up.
        }
    }
    if (cache) cache.set(el, result);
    return result;
}

function liHasDirectText(el) {
    for (var n = el.firstChild; n; n = n.nextSibling) {
        if (n.nodeType === 3 && n.nodeValue && n.nodeValue.trim().length) return true;
    }
    return false;
}

function liIsInteractive(el) {
    try {
        return el.matches(LI_INTERACTIVE);
    } catch (e) {
        return false;
    }
}

// The .sr-only / .visually-hidden family: present for screen readers, parked
// off-screen or clipped to nothing on purpose. Never a layout defect.
function liIsVisuallyHidden(el, rect, style) {
    if (rect.w <= 1 && rect.h <= 1) return true;
    var clip = style.clip || '';
    if (clip.replace(/\s/g, '') === 'rect(0px,0px,0px,0px)') return true;
    var cp = style.clipPath || '';
    if (cp === 'inset(50%)' || cp === 'inset(100%)') return true;
    if (rect.w > 0 && rect.h > 0 && (rect.x + rect.w) < -9000) return true;
    return false;
}

// Which CSS property, if any, makes this element a stacking context.
// Named rather than boolean: the name is the diagnosis for a trapped z-index.
function liStackingReason(el, style) {
    if (style.position === 'fixed') return 'position:fixed';
    if (style.position === 'sticky') return 'position:sticky';
    if (style.zIndex !== 'auto') {
        if (style.position !== 'static') return 'z-index on positioned element';
        var p = el.parentElement;
        if (p) {
            var pd = window.getComputedStyle(p).display;
            if (pd.indexOf('flex') !== -1 || pd.indexOf('grid') !== -1) {
                return 'z-index on flex/grid child';
            }
        }
    }
    if (parseFloat(style.opacity) < 1) return 'opacity:' + style.opacity;
    if (style.transform && style.transform !== 'none') return 'transform';
    if (style.filter && style.filter !== 'none') return 'filter';
    if (style.backdropFilter && style.backdropFilter !== 'none') return 'backdrop-filter';
    if (style.perspective && style.perspective !== 'none') return 'perspective';
    if (style.mixBlendMode && style.mixBlendMode !== 'normal') return 'mix-blend-mode';
    if (style.isolation === 'isolate') return 'isolation:isolate';
    if (style.willChange && /transform|opacity|filter|perspective|contain/.test(style.willChange)) {
        return 'will-change:' + style.willChange;
    }
    if (style.contain && /paint|layout|strict|content/.test(style.contain)) {
        return 'contain:' + style.contain;
    }
    if (style.clipPath && style.clipPath !== 'none') return 'clip-path';
    return null;
}

function liClipsX(style) {
    return style.overflowX !== 'visible';
}

function liClipsY(style) {
    return style.overflowY !== 'visible';
}
