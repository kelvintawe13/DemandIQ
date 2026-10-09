import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

// `text-slate-400` (#94a3b8) on white is ~2.6:1, below WCAG AA's 4.5:1 for
// normal text, so it must not be used for light-mode copy. The `dark:` variant
// is fine (slate-400 on slate-900 is ~6:1) and is left alone. This is a source
// scan rather than a render assertion because the token appears in many files
// and the rule is about the design token, not one component.

const SRC = join(process.cwd(), 'src');

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      return entry === '__tests__' ? [] : walk(full);
    }
    return full.endsWith('.tsx') || full.endsWith('.ts') ? [full] : [];
  });
}

describe('light-mode text contrast', () => {
  it('never uses slate-400 for text outside a dark-mode variant', () => {
    const offenders = walk(SRC).filter((file) =>
      /(?<!dark:)text-slate-400/.test(readFileSync(file, 'utf8')),
    );
    expect(offenders).toEqual([]);
  });

  it('uses a passing grey for the responsive-card field labels', () => {
    const css = readFileSync(join(SRC, 'index.css'), 'utf8');
    const cardLabel = css.match(/\.responsive-cards td::before \{[\s\S]*?\}/)?.[0] ?? '';
    // slate-500, not slate-400.
    expect(cardLabel).toContain('#64748b');
    expect(cardLabel).not.toContain('#94a3b8');
  });
});
