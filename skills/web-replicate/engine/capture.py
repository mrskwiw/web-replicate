"""Browser-side capture: the ``page.evaluate`` JS payloads plus pure helpers.

The JS snippets are lifted from web-qa's ``browser.py`` (DOM outline, visible
content, interactive/forms/links inventory) and extended with replication-grade
collectors the QA tool never needed: page metadata, the full asset graph
(stylesheets / scripts / images / fonts / media), Web Storage, and a
client-framework fingerprint. Keeping them here — with the small pure helpers
that classify content types and pick which bodies are worth saving — keeps
``browser.py`` about lifecycle and orchestration.
"""

from __future__ import annotations

from typing import Optional

# --- Structure (borrowed from web-qa) --------------------------------------

# Compact role/text tree of interactive + landmark elements. Structure over raw
# HTML keeps the outline token-efficient — the full markup is saved separately.
DOM_OUTLINE_JS = r"""
() => {
  const MAX_LINES = 300;
  const lines = [];
  const INTERACTIVE = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA']);
  const LANDMARK = new Set(['NAV', 'MAIN', 'HEADER', 'FOOTER', 'FORM', 'SECTION', 'ASIDE']);
  const label = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim().slice(0, 80);
    if (el.tagName === 'INPUT') {
      return (el.getAttribute('placeholder') || el.getAttribute('name') || el.type || '').trim().slice(0, 80);
    }
    const t = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
    return t.slice(0, 80);
  };
  const selectorFor = (el) => {
    if (el.id) return '#' + el.id;
    const testid = el.getAttribute('data-testid');
    if (testid) return '[data-testid="' + testid + '"]';
    const name = el.getAttribute('name');
    if (name) return el.tagName.toLowerCase() + '[name="' + name + '"]';
    let cls = '';
    if (el.className && typeof el.className === 'string') {
      const parts = el.className.trim().split(/\s+/).slice(0, 2);
      if (parts[0]) cls = '.' + parts.join('.');
    }
    return el.tagName.toLowerCase() + cls;
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const walk = (el, depth) => {
    if (lines.length >= MAX_LINES) return;
    for (const child of el.children) {
      const tag = child.tagName;
      const isInt = INTERACTIVE.has(tag) || child.hasAttribute('role') || child.hasAttribute('onclick');
      const isLand = LANDMARK.has(tag);
      if ((isInt || isLand) && visible(child)) {
        const role = child.getAttribute('role') || tag.toLowerCase();
        const indent = '  '.repeat(Math.min(depth, 8));
        lines.push(indent + role + ' "' + label(child) + '" {' + selectorFor(child) + '}');
      }
      walk(child, depth + ((isInt || isLand) ? 1 : 0));
      if (lines.length >= MAX_LINES) return;
    }
  };
  if (document.body) walk(document.body, 0);
  return lines.join('\n');
}
"""

CONTENT_JS = r"""
() => {
  const t = (document.body && document.body.innerText) ? document.body.innerText : '';
  return t.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim().slice(0, 6000);
}
"""

# Interactive controls, forms (with action/method — backend hints), and links.
# Trimmed from web-qa's snapshot: no "incomplete" scan (QA-specific), forms now
# expose their action/method which a rebuild needs.
SNAPSHOT_JS = r"""
() => {
  const originHost = location.host;
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  };
  const label = (el) => {
    const a = el.getAttribute('aria-label');
    if (a) return a.trim().slice(0, 100);
    if (el.tagName === 'INPUT') {
      return (el.getAttribute('placeholder') || el.getAttribute('name') || el.type || '').trim().slice(0, 100);
    }
    return (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 100);
  };
  const sel = (el) => {
    if (el.id) return '#' + el.id;
    const ti = el.getAttribute('data-testid');
    if (ti) return '[data-testid="' + ti + '"]';
    const nm = el.getAttribute('name');
    if (nm) return el.tagName.toLowerCase() + '[name="' + nm + '"]';
    let cls = '';
    if (el.className && typeof el.className === 'string') {
      const p = el.className.trim().split(/\s+/).slice(0, 2);
      if (p[0]) cls = '.' + p.join('.');
    }
    return el.tagName.toLowerCase() + cls;
  };
  const landmark = (el) => {
    let n = el.parentElement;
    while (n) {
      const t = n.tagName;
      if (t === 'MAIN' || t === 'HEADER' || t === 'FOOTER' || t === 'NAV' || t === 'FORM') return t;
      n = n.parentElement;
    }
    return 'BODY';
  };
  const matchCache = new Map();
  const uniqueSel = (el, base) => {
    let arr = matchCache.get(base);
    if (arr === undefined) {
      try { arr = Array.prototype.slice.call(document.querySelectorAll(base)); }
      catch (e) { arr = []; }
      matchCache.set(base, arr);
    }
    if (arr.length <= 1) return base;
    const idx = arr.indexOf(el);
    return idx >= 0 ? base + ' >> nth=' + idx : base;
  };

  // Resolve a control's VISIBLE label, in priority order. React forms often omit
  // `for`/`id`, so native `el.labels` is tried first but backed by wrapping-label,
  // aria, preceding-text, and placeholder fallbacks — the field-to-label pairing
  // is what a rebuild needs, and it's the thing bare selectors don't give.
  const clip = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  const labelFor = (el) => {
    try { if (el.labels && el.labels.length) return clip(el.labels[0].textContent); } catch (e) {}
    const aria = el.getAttribute('aria-label');
    if (aria) return clip(aria);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = lb.split(/\s+/).map((id) => { const n = document.getElementById(id); return n ? n.textContent : ''; }).join(' ');
      if (t.trim()) return clip(t);
    }
    let p = el.parentElement;
    for (let i = 0; i < 3 && p; i++) { if (p.tagName === 'LABEL') return clip(p.textContent); p = p.parentElement; }
    let prev = el.previousElementSibling;
    for (let i = 0; i < 3 && prev; i++) {
      if (/^(LABEL|SPAN|P|DIV|H[1-6])$/.test(prev.tagName)) {
        const t = prev.textContent.trim();
        if (t && t.length < 90) return clip(t);
      }
      prev = prev.previousElementSibling;
    }
    return clip(el.getAttribute('placeholder') || el.getAttribute('name') || '');
  };

  // Full field descriptor: selector + label + declared validation. Used for both
  // per-<form> fields and the page-level `fields` inventory (controls outside a form).
  const fieldInfo = (el) => ({
    selector: uniqueSel(el, sel(el)),
    type: (el.getAttribute('type') || el.tagName.toLowerCase()),
    name: el.getAttribute('name'),
    label: labelFor(el),
    required: !!(el.required || el.getAttribute('aria-required') === 'true'),
    placeholder: el.getAttribute('placeholder'),
    autocomplete: el.getAttribute('autocomplete'),
    minlength: (el.minLength != null && el.minLength >= 0) ? el.minLength : null,
    maxlength: (el.maxLength != null && el.maxLength >= 0) ? el.maxLength : null,
    min: el.getAttribute('min'),
    max: el.getAttribute('max'),
    pattern: el.getAttribute('pattern'),
    options: el.tagName === 'SELECT'
      ? Array.prototype.slice.call(el.options, 0, 40).map((o) => clip(o.textContent)) : [],
  });

  const interactive = [];
  const links = [];
  const MAX_DUP = 12;
  const sigCount = new Map();
  for (const el of document.querySelectorAll('a, button, input, select, textarea, [role=button], [onclick]')) {
    if (!visible(el)) continue;
    const tag = el.tagName;
    const base = sel(el);
    const s = uniqueSel(el, base);
    const text = label(el);
    const role = el.getAttribute('role') || (tag === 'A' ? 'link' : tag === 'BUTTON' ? 'button' : tag.toLowerCase());
    const loc = landmark(el);
    let rank = 4, kind = 'other';
    if (loc === 'MAIN') { const big = (tag === 'BUTTON' || tag === 'A'); rank = big ? 0 : 2; kind = big ? 'cta' : 'field'; }
    else if (loc === 'HEADER' || loc === 'NAV') { rank = 1; kind = 'nav'; }
    else if (loc === 'FORM') { rank = 2; kind = 'field'; }
    else if (loc === 'FOOTER') { rank = 3; kind = 'footer'; }
    const sig = role + '|' + text + '|' + base;
    const n = sigCount.get(sig) || 0;
    if (n < MAX_DUP) { sigCount.set(sig, n + 1); interactive.push({ selector: s, role, text, rank, kind }); }
    if (tag === 'A') {
      const rawHref = el.getAttribute('href');
      const href = rawHref || '';
      let scheme = 'relative', external = false;
      try {
        const u = new URL(href, location.href);
        scheme = u.protocol.replace(':', '');
        external = (u.host !== originHost) && (scheme === 'http' || scheme === 'https');
      } catch (e) { scheme = href ? 'unknown' : 'relative'; }
      links.push({ selector: s, text, href, target: el.getAttribute('target'), external, scheme });
    }
  }
  // Ignore controls that aren't user-facing inputs.
  const SKIP_TYPE = new Set(['hidden', 'submit', 'button', 'reset', 'image']);
  const isField = (el) => (el.tagName === 'SELECT' || el.tagName === 'TEXTAREA'
    || (el.tagName === 'INPUT' && !SKIP_TYPE.has((el.getAttribute('type') || 'text').toLowerCase())));

  const forms = [];
  for (const f of document.querySelectorAll('form')) {
    if (!visible(f)) continue;
    const fields = [];
    for (const inp of f.querySelectorAll('input, select, textarea')) {
      if (isField(inp)) fields.push(fieldInfo(inp));
    }
    const nonSubmit = [...f.querySelectorAll('button:not([type=button]):not([type=reset])')];
    const submitEl =
      f.querySelector('button[type=submit]') ||
      f.querySelector('input[type=submit]') ||
      f.querySelector('input[type=image]') ||
      (nonSubmit.length ? nonSubmit[nonSubmit.length - 1] : null) ||
      f.querySelector('button');
    forms.push({
      selector: sel(f), fields, submit: submitEl ? sel(submitEl) : null,
      action: f.getAttribute('action'), method: f.getAttribute('method'),
    });
  }

  // Page-level field inventory: EVERY visible labeled control, whether or not it
  // sits in a <form>. This is what catches React "formless" inputs (the wizard
  // panels) that the <form>-scoped scan above misses entirely.
  const fields = [];
  for (const inp of document.querySelectorAll('input, select, textarea')) {
    if (!visible(inp) || !isField(inp)) continue;
    if (fields.length >= 80) break;
    fields.push(fieldInfo(inp));
  }

  interactive.sort((a, b) => a.rank - b.rank);
  return { interactive, forms, fields, links };
}
"""

# --- Metadata --------------------------------------------------------------

META_JS = r"""
() => {
  const out = {};
  const html = document.documentElement;
  if (html && html.lang) out['lang'] = html.lang;
  const cs = document.characterSet || document.charset;
  if (cs) out['charset'] = cs;
  for (const m of document.querySelectorAll('meta')) {
    const key = m.getAttribute('name') || m.getAttribute('property') || m.getAttribute('http-equiv');
    const val = m.getAttribute('content');
    if (key && val) out[key.toLowerCase()] = val.slice(0, 400);
  }
  const canonical = document.querySelector('link[rel=canonical]');
  if (canonical) out['canonical'] = canonical.getAttribute('href') || '';
  const manifest = document.querySelector('link[rel=manifest]');
  if (manifest) out['manifest'] = manifest.getAttribute('href') || '';
  return out;
}
"""

# --- Asset graph -----------------------------------------------------------

ASSETS_JS = r"""
() => {
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (e) { return u; } };
  const stylesheets = [];
  const inline_styles = [];
  const scripts = [];
  const images = [];
  const fonts = [];
  const media = [];

  for (const l of document.querySelectorAll('link[rel~="stylesheet"], link[rel="preload"][as="style"]')) {
    const href = l.getAttribute('href');
    if (href) stylesheets.push({ url: abs(href), attrs: {
      media: l.getAttribute('media') || '', integrity: l.getAttribute('integrity') || '',
      crossorigin: l.getAttribute('crossorigin') || '' } });
  }
  for (const s of document.querySelectorAll('style')) {
    const t = s.textContent || '';
    if (t.trim()) inline_styles.push(t);
  }
  for (const s of document.querySelectorAll('script')) {
    const src = s.getAttribute('src');
    if (!src) continue;
    scripts.push({ url: abs(src), attrs: {
      type: s.getAttribute('type') || '', module: (s.getAttribute('type') === 'module') ? 'true' : '',
      async: s.async ? 'true' : '', defer: s.defer ? 'true' : '',
      integrity: s.getAttribute('integrity') || '', crossorigin: s.getAttribute('crossorigin') || '' } });
  }
  for (const img of document.querySelectorAll('img')) {
    const src = img.currentSrc || img.getAttribute('src');
    if (!src) continue;
    images.push({ url: abs(src), attrs: {
      alt: (img.getAttribute('alt') || '').slice(0, 120),
      srcset: (img.getAttribute('srcset') || '').slice(0, 300),
      loading: img.getAttribute('loading') || '',
      width: String(img.naturalWidth || img.width || ''), height: String(img.naturalHeight || img.height || '') } });
  }
  for (const l of document.querySelectorAll('link[rel="preload"][as="font"], link[as="font"]')) {
    const href = l.getAttribute('href');
    if (href) fonts.push({ url: abs(href), attrs: { type: l.getAttribute('type') || '' } });
  }
  try {
    if (document.fonts) {
      for (const ff of document.fonts) {
        fonts.push({ url: '', attrs: { family: ff.family || '', style: ff.style || '',
          weight: ff.weight || '', status: ff.status || '' } });
      }
    }
  } catch (e) {}
  for (const el of document.querySelectorAll('video, audio, source, track')) {
    const src = el.getAttribute('src');
    if (src) media.push({ url: abs(src), attrs: { tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '' } });
  }
  return { stylesheets, inline_styles, scripts, images, fonts, media };
}
"""

STORAGE_JS = r"""
() => {
  const dump = (store) => {
    const out = {};
    try {
      for (let i = 0; i < store.length; i++) {
        const k = store.key(i);
        const v = store.getItem(k);
        out[k] = (v == null) ? '' : String(v).slice(0, 2000);
      }
    } catch (e) {}
    return out;
  };
  return { local: dump(window.localStorage), session: dump(window.sessionStorage) };
}
"""

# Client-stack fingerprint. Detects frameworks even in PRODUCTION builds — where
# React/Vue expose no window globals — by reading the DOM *instance markers* the
# runtimes attach (React fiber keys __reactFiber$/__reactContainer$, Vue's
# __vue_app__), plus build-tool inference from already-loaded asset URLs and
# passive library detection from globals/class patterns. Also returns a
# `build_data` blob (Next.js/Nuxt/Remix hydration state) when present — often the
# single richest source of routes, data shapes, and the API base URL.
TECH_JS = r"""
() => {
  const frameworks = [];
  const libraries = [];
  const evidence = [];
  let build_data = null;
  let build_tool = null;
  const mfw = (name, why) => { if (!frameworks.includes(name)) { frameworks.push(name); evidence.push(name + ': ' + why); } };
  const mlib = (name, why) => { if (!libraries.includes(name)) { libraries.push(name); evidence.push(name + ': ' + why); } };

  // --- Hydration state blobs (richest single source) ---
  if (window.__NEXT_DATA__) { mfw('Next.js', 'window.__NEXT_DATA__'); try { build_data = JSON.stringify(window.__NEXT_DATA__); } catch (e) {} }
  if (window.__NUXT__ || document.querySelector('#__nuxt')) { mfw('Nuxt', '__NUXT__ / #__nuxt'); if (!build_data) { try { build_data = JSON.stringify(window.__NUXT__); } catch (e) {} } }
  if (window.__remixContext) { mfw('Remix', 'window.__remixContext'); if (!build_data) { try { build_data = JSON.stringify(window.__remixContext); } catch (e) {} } }
  if (document.querySelector('[data-sveltekit-preload-data], [data-sveltekit-hydrate], script[data-sveltekit]')) mfw('SvelteKit', 'sveltekit markers');

  // --- Prod-safe framework detection via DOM instance properties ---
  // React attaches own-enumerable __reactContainer$<rand> to the root node and
  // __reactFiber$/__reactProps$ to rendered elements — present even in prod where
  // window.React is absent. Scan a bounded sample of nodes.
  const ownHit = (el, re) => { if (!el) return false; try { return Object.keys(el).some((k) => re.test(k)); } catch (e) { return false; } };
  const scanHit = (re, limit) => {
    const nodes = document.getElementsByTagName('*');
    const n = Math.min(nodes.length, limit || 400);
    for (let i = 0; i < n; i++) { if (ownHit(nodes[i], re)) return true; }
    return false;
  };
  const root = document.getElementById('root') || document.getElementById('app') || document.body;
  if (window.React || document.querySelector('[data-reactroot]') || ownHit(root, /^__reactContainer\$/) || scanHit(/^__react(Fiber|Props)\$/)) mfw('React', 'fiber/container DOM markers');
  if (window.preact || scanHit(/^__preact/)) mfw('Preact', '__preact DOM markers');
  if (window.Vue || (root && root.__vue_app__) || document.querySelector('[data-v-app]') || scanHit(/^__vue(__|Parent)/)) mfw('Vue', 'app/instance markers');
  const ngv = document.querySelector('[ng-version]');
  if (ngv) mfw('Angular', 'ng-version=' + ngv.getAttribute('ng-version'));
  else if (window.ng || window.getAllAngularRootElements) mfw('Angular', 'angular global');
  if (document.querySelector('*[class*="svelte-"]')) mfw('Svelte', 'svelte-* scoped class');
  if (window.Alpine) mfw('Alpine.js', 'window.Alpine');
  if (window.htmx) mfw('htmx', 'window.htmx');
  if (window.jQuery || (window.$ && window.$.fn && window.$.fn.jquery)) mfw('jQuery', 'jQuery ' + ((window.jQuery && window.jQuery.fn && window.jQuery.fn.jquery) || ''));
  if (window.__wcVersion || document.querySelector('meta[name=generator][content*=WordPress]')) mfw('WordPress', 'generator/global');

  // --- Build tool from already-loaded asset URLs (no re-fetch) ---
  const urls = [];
  document.querySelectorAll('script[src]').forEach((s) => urls.push(s.getAttribute('src') || ''));
  document.querySelectorAll('link[href]').forEach((l) => urls.push(l.getAttribute('href') || ''));
  const has = (re) => urls.some((u) => re.test(u));
  const moduleScript = !!document.querySelector('script[type=module][src]');
  if (has(/\/_next\/static\//)) { build_tool = 'Next.js build'; mfw('Next.js', '/_next/static/ bundles'); }
  else if (has(/\/_nuxt\//)) { build_tool = 'Nuxt build'; mfw('Nuxt', '/_nuxt/ bundles'); }
  else if (moduleScript && has(/\/assets\/[\w.-]+-[A-Za-z0-9_-]{6,}\.(js|css)/)) { build_tool = 'Vite'; }
  else if (has(/\/static\/js\/(main|runtime|bundle|\d)[.\-]/)) { build_tool = 'Webpack (CRA-style)'; }
  else if (has(/(chunk|runtime|vendors?)[.\-~][A-Za-z0-9]+\.js/)) { build_tool = 'Webpack'; }

  // --- Passive library detection (globals + class patterns; no fetch) ---
  if (window.gtag || window.dataLayer) mlib('Google Analytics / GTM', 'gtag/dataLayer');
  if (window.Stripe || window.__stripe) mlib('Stripe.js', 'window.Stripe');
  if (window.Sentry) mlib('Sentry', 'window.Sentry');
  if (window.posthog) mlib('PostHog', 'window.posthog');
  if (window.Intercom || window.intercomSettings) mlib('Intercom', 'window.Intercom');
  if (window.mixpanel) mlib('Mixpanel', 'window.mixpanel');
  if (window.Clerk) mlib('Clerk (auth)', 'window.Clerk');
  if (window.supabase || window.__SUPABASE__) mlib('Supabase', 'supabase global');
  // Tailwind: a strong signal is many atomic utility classes co-occurring.
  let sample = '';
  const els = document.querySelectorAll('[class]');
  for (let i = 0; i < Math.min(els.length, 40); i++) sample += ' ' + els[i].className;
  if (/(^|\s)(flex|grid|inline-flex)(\s|$)/.test(sample) && /(^|\s)(rounded-\w+|px-\d|py-\d|text-(xs|sm|lg|xl)|bg-[a-z]+-\d{2,3}|gap-\d)(\s|$)/.test(sample)) mlib('Tailwind CSS', 'atomic utility class patterns');

  const gen = document.querySelector('meta[name=generator]');
  return {
    frameworks, libraries, build_tool, evidence, build_data,
    meta_generator: gen ? gen.getAttribute('content') : null,
  };
}
"""


# --- Design system ---------------------------------------------------------

# Extract the app's design system: its own `:root` CSS custom properties (its
# token source when it has one) PLUS the most-used *rendered* values across a
# bounded element sample (palette, fonts, sizes, radii, spacing, shadows) — so it
# works even for utility-CSS apps (Tailwind) that expose no variables — PLUS
# computed-style samples of representative elements a rebuild can match directly.
# Reads only accessible (same-origin) stylesheets + computed styles; cross-origin
# `cssRules` access throws and is skipped. Passive: no fetch.
DESIGN_TOKENS_JS = r"""
() => {
  const clip = (s) => (s == null ? '' : String(s)).trim().slice(0, 80);
  const top = (m, n) => Object.entries(m).sort((a, b) => b[1] - a[1]).slice(0, n).map((e) => e[0]);
  const bump = (m, k) => { if (k) m[k] = (m[k] || 0) + 1; };

  // 1. :root custom properties + @media breakpoints, from ACCESSIBLE stylesheets.
  const vars = {};
  const bps = new Set();
  const walkRules = (rules) => {
    for (const r of rules) {
      try {
        if (r.style && r.selectorText && /(^|,)\s*:root\b/.test(r.selectorText)) {
          for (let i = 0; i < r.style.length; i++) {
            const p = r.style[i];
            if (p && p.slice(0, 2) === '--') vars[p] = clip(r.style.getPropertyValue(p));
          }
        }
        if (r.conditionText || (r.media && r.media.mediaText)) {
          const ct = r.conditionText || r.media.mediaText;
          const m = ct.match(/(min|max)-width:\s*[\d.]+(px|em|rem)/g);
          if (m) m.forEach((x) => bps.add(x.replace(/\s+/g, ' ')));
        }
        if (r.cssRules) walkRules(r.cssRules);
      } catch (e) {}
    }
  };
  for (const sheet of Array.prototype.slice.call(document.styleSheets)) {
    let rules; try { rules = sheet.cssRules; } catch (e) { continue; }
    if (rules) walkRules(rules);
  }

  // 2. Tally rendered values across a bounded, visible element sample.
  const colorT = {}, bgT = {}, borderT = {}, fontT = {}, sizeT = {}, radT = {}, spaceT = {}, shadowT = {};
  const nodes = document.querySelectorAll('body *');
  const N = Math.min(nodes.length, 700);
  for (let i = 0; i < N; i++) {
    const el = nodes[i];
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const s = getComputedStyle(el);
    bump(colorT, s.color);
    const bg = s.backgroundColor;
    if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') bump(bgT, bg);
    if (parseFloat(s.borderTopWidth) > 0) bump(borderT, s.borderTopColor);
    bump(fontT, clip(s.fontFamily));
    bump(sizeT, s.fontSize);
    if (s.borderTopLeftRadius && s.borderTopLeftRadius !== '0px') bump(radT, s.borderTopLeftRadius);
    if (s.paddingTop && s.paddingTop !== '0px') bump(spaceT, s.paddingTop);
    if (s.gap && s.gap !== 'normal' && s.gap !== '0px') bump(spaceT, s.gap.split(' ')[0]);
    if (s.boxShadow && s.boxShadow !== 'none') bump(shadowT, clip(s.boxShadow));
  }

  // 3. Computed-style samples of representative elements.
  const pick = (sel, props) => {
    const el = document.querySelector(sel); if (!el) return null;
    const s = getComputedStyle(el); const o = {};
    props.forEach((p) => { const v = s.getPropertyValue(p); if (v) o[p] = clip(v); });
    return Object.keys(o).length ? o : null;
  };
  const samples = {};
  const add = (k, v) => { if (v) samples[k] = v; };
  add('body', pick('body', ['color', 'background-color', 'font-family', 'font-size', 'line-height']));
  add('h1', pick('h1', ['font-size', 'font-weight', 'line-height', 'color']));
  add('h2', pick('h2', ['font-size', 'font-weight']));
  add('button', pick('button, [role=button], a.btn', ['color', 'background-color', 'border-radius', 'padding', 'font-size', 'font-weight', 'border']));
  add('link', pick('a[href]', ['color', 'text-decoration-line', 'font-weight']));
  add('input', pick('input, textarea, select', ['border', 'border-radius', 'padding', 'background-color', 'font-size']));

  const rootCS = getComputedStyle(document.documentElement);
  const color_scheme = clip(rootCS.getPropertyValue('color-scheme')) || null;

  return {
    css_variables: vars,
    palette_text: top(colorT, 8),
    palette_bg: top(bgT, 8),
    palette_border: top(borderT, 6),
    fonts: top(fontT, 4),
    font_sizes: top(sizeT, 10),
    radii: top(radT, 6),
    spacing: top(spaceT, 10),
    shadows: top(shadowT, 5),
    breakpoints: Array.from(bps).slice(0, 12),
    color_scheme: color_scheme,
    samples: samples,
  };
}
"""


# ---------------------------------------------------------------------------
# Pure helpers (no browser) — content-type classification & body policy
# ---------------------------------------------------------------------------

# Resource types whose bodies are worth saving for backend inference.
_TEXTUAL_TYPES = (
    "json",
    "javascript",
    "ecmascript",
    "text/",
    "xml",
    "html",
    "graphql",
    "x-www-form-urlencoded",
    "csv",
    "yaml",
)


def is_textual(content_type: Optional[str]) -> bool:
    """Whether a response of this content-type should have its body captured.

    Binary payloads (images, fonts, video, octet-stream) are recorded by URL +
    metadata only — their bytes belong in the asset download, not the JSON log.
    """
    if not content_type:
        return False
    ct = content_type.lower()
    return any(tok in ct for tok in _TEXTUAL_TYPES)


def short_content_type(content_type: Optional[str]) -> Optional[str]:
    """The media type without parameters, e.g. ``application/json; charset=utf-8``
    → ``application/json``."""
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower() or None


def header_get(headers: dict, name: str) -> Optional[str]:
    """Case-insensitive header lookup over a plain dict."""
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None
