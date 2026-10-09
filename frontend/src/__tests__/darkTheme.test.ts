import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

/**
 * The dark theme is a single remap layer in `index.css` (`.dark .bg-white`,
 * `.dark .text-slate-500`, …), not a `dark:` variant on every element. That
 * keeps it consistent, but it also means a colour class only themes correctly if
 * the layer mentions it: `text-red-600` on a dark panel is 3.6:1 and an
 * `bg-amber-100` badge stayed a near-white chip until both were added.
 *
 * This scans the source for the classes that need coverage and fails if the
 * layer does not carry one. It is a source scan rather than a render assertion
 * because jsdom has no paint, so a real contrast measurement is impossible here
 * (see `contrast.test.ts` for the same reasoning applied to the light theme).
 */

const SRC = join(process.cwd(), 'src');
const CSS = readFileSync(join(SRC, 'index.css'), 'utf8');

/**
 * `blue` was missing here, and that is exactly how `Alerts` kept a near-white
 * `bg-blue-50` panel on the dark page: the family was never scanned, so nothing
 * asked the layer for it. Scanning every coloured family the palette offers
 * means a new one cannot be used without a dark counterpart.
 */
const ACCENT = 'brand|red|rose|amber|emerald|sky|teal|green|yellow|orange|violet|blue|cyan|indigo|purple';
const NEUTRAL = 'slate|gray|zinc|neutral|stone|white';
/**
 * An opacity modifier is part of the class name: `bg-brand-50/60` is a
 * different selector from `bg-brand-50`. Leaving it uncaptured let a `/60`
 * tint satisfy this scan via the un-suffixed rule while rendering as a 60%
 * white wash — text on it measured 1.1:1.
 */
const MOD = '(?:\\/\\d{1,3})?\\b';
// Light tints, which need a translucent dark counterpart.
const TINT = new RegExp(`(?:hover:)?bg-(${ACCENT})-(?:50|100|200|300)${MOD}`, 'g');
// Dark inks, which need to lighten to stay above 4.5:1 on a dark surface.
const INK = new RegExp(`(?:hover:)?text-(${ACCENT})-(?:500|600|700|800|900|950)\\b`, 'g');
// Light hairline borders, which otherwise stay pale against the dark surface.
const BORDER = new RegExp(`(?:hover:)?border-(${ACCENT})-(?:50|100|200|300)${MOD}`, 'g');
/**
 * The neutral scale is remapped broadly, so a bare `bg-slate-100` is already
 * safe. Its opacity-modified form is not — that is the same defect as above,
 * just in the family that paints most of the surfaces.
 */
const NEUTRAL_MOD = new RegExp(`(?:hover:)?(?:bg|border)-(${NEUTRAL})-(?:50|100|200|300)\\/\\d{1,3}\\b`, 'g');
/**
 * `disabled:` and `focus-visible:` were never themed, so a control kept the
 * paint of the light theme while sitting on a dark page. `group-hover:` is
 * excluded because its selector has a different shape (`.group:hover
 * .group-hover\:…`) and is covered explicitly in the layer instead.
 */
const INTERACTION = new RegExp(`(?:hover|disabled|focus-visible):(?:bg|border|text)-(${ACCENT}|${NEUTRAL})-(?:50|100|200|300|400|500|600|700|800|900)${MOD}`, 'g');
/**
 * Solid fills that sit under white text are meant to look the same in both
 * themes — a button is a button. Remapping them would put light text on a light
 * background.
 */
const INTERACTION_ALLOWED = new Set([
  'hover:bg-brand-700',
  'hover:bg-brand-800',
  'hover:bg-slate-800',
  'hover:bg-red-700',
  'hover:bg-emerald-700',
  // A light ink is already correct on a dark surface; remapping this one would
  // darken it and make the disabled label harder to read, not easier.
  'disabled:text-slate-300',
]);

/**
 * Deliberate exceptions, each with the reason it is not a themed surface.
 * `SiteBanner` is a solid amber announcement bar with its own palette — it is
 * meant to look the same in both themes, so its gradient, `text-amber-950` and
 * the white-on-amber badge are left exactly as designed.
 */
const ALLOWED: Record<string, string[]> = {
  'components/SiteBanner.tsx': ['bg-amber-400', 'bg-amber-300', 'bg-amber-900', 'bg-emerald-300', 'text-amber-950', 'text-amber-100'],
};

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return entry === '__tests__' ? [] : walk(full);
    return full.endsWith('.tsx') || full.endsWith('.ts') ? [full] : [];
  });
}

/** The pseudo-class each variant compiles to, so the expected selector is the
 *  one Tailwind actually emits (`.disabled\:bg-slate-300:disabled`). */
const PSEUDO: Record<string, string> = {
  hover: ':hover',
  focus: ':focus',
  'focus-visible': ':focus-visible',
  disabled: ':disabled',
};

/** The selector the layer would have to carry for this class to theme. */
function expectedSelector(className: string): string {
  // Tailwind escapes the `/` of an opacity modifier, so `bg-brand-50/60` is
  // written `.bg-brand-50\/60` in the stylesheet.
  const [variant, ...rest] = className.split(':');
  if (!rest.length) return `.dark .${variant.replace(/\//g, '\\/')}`;
  const bare = rest.join(':').replace(/\//g, '\\/');
  return `.dark .${variant}\\:${bare}${PSEUDO[variant] ?? ''}`;
}

describe('dark theme coverage', () => {
  it('remaps every accent tint, ink, border and opacity-modified surface the source uses', () => {
    const uncovered: string[] = [];

    for (const file of walk(SRC)) {
      const relative = file.slice(SRC.length + 1);
      const allowed = ALLOWED[relative] ?? [];
      const source = readFileSync(file, 'utf8');

      for (const pattern of [TINT, INK, BORDER, NEUTRAL_MOD, INTERACTION]) {
        for (const match of source.matchAll(pattern)) {
          const className = match[0];
          if (allowed.includes(className)) continue;
          if (INTERACTION_ALLOWED.has(className)) continue;
          if (!CSS.includes(expectedSelector(className))) {
            uncovered.push(`${relative}: ${className} → missing ${expectedSelector(className)}`);
          }
        }
      }
    }

    expect([...new Set(uncovered)].sort()).toEqual([]);
  });

  /**
   * An arbitrary colour value is unmatchable by the layer: `.dark .bg-[#f4faf8]`
   * is not a selector anyone writes, so the canvas kept painting near-white
   * while every ink class on top of it was remapped. That is how `/login` and
   * `/signup` shipped with a 1.1:1 heading in dark mode — the page was white and
   * the type was themed for a dark page. Semantic tokens only.
   */
  it('uses no arbitrary hex colour classes, which the dark layer cannot remap', () => {
    const arbitrary = /\b(?:hover:|focus:|disabled:)?(?:bg|text|border|ring|fill|stroke|from|via|to)-\[#[0-9a-fA-F]{3,8}\]/g;
    const offenders: string[] = [];

    for (const file of walk(SRC)) {
      const relative = file.slice(SRC.length + 1);
      for (const match of readFileSync(file, 'utf8').matchAll(arbitrary)) {
        offenders.push(`${relative}: ${match[0]}`);
      }
    }

    expect([...new Set(offenders)].sort()).toEqual([]);
  });

  /**
   * The charts cannot be covered by the scan above: recharts paints its grid,
   * axis ticks and tooltip with inline SVG attributes and inline styles rather
   * than classes, so nothing in the source names them. A presence check is the
   * honest level of verification available — jsdom has no paint, so the rule
   * cannot be measured here, only kept from disappearing.
   */
  it('keeps the recharts surfaces themed, since recharts styles them inline', () => {
    for (const selector of [
      '.dark .recharts-cartesian-grid line',
      '.dark .recharts-cartesian-axis-tick text',
      '.dark .recharts-label',
      '.dark .recharts-default-tooltip',
      '.dark .recharts-tooltip-item',
    ]) {
      expect(CSS).toContain(selector);
    }
    // The tooltip keeps recharts' white background unless it is forced: a white
    // box with the page's inherited light text is unreadable.
    const tooltip = CSS.match(/\.dark \.recharts-default-tooltip \{[\s\S]*?\}/)?.[0] ?? '';
    expect(tooltip).toContain('background-color');
  });

  it('keeps the solid fills that sit under white text out of the remap', () => {
    // A solid fill is a button or a dot, not a tint — remapping it would put
    // light text on a light background.
    for (const solid of ['bg-brand-700', 'bg-red-600', 'bg-emerald-600', 'bg-amber-500']) {
      expect(CSS).not.toContain(`.dark .${solid} {`);
    }
  });
});
