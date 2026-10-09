import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

// DESIGN.md § 13: "Use a named scale token — don't sprinkle `text-[12.5px]` by
// eye." Type sizes were migrated to the semantic scale in
// `tailwind.config.js`; this guards the rule so they cannot creep back in. It
// is a source scan rather than a render assertion because the rule is about the
// token, and a raw size can appear in 1,300-odd places across the app.

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

describe('semantic type scale', () => {
  it('never sizes type with an arbitrary pixel value', () => {
    const offenders = walk(SRC).filter((file) =>
      /text-\[[0-9.]+px\]/.test(readFileSync(file, 'utf8')),
    );
    expect(offenders).toEqual([]);
  });
});
