import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

// `TableShell` and `ResponsiveTable` both take an `empty` slot, and the two
// render it in opposite places:
//
//   * `TableShell.empty` goes *inside* the `<tbody>`, so a `<tr>` is correct.
//   * `ResponsiveTable.empty` replaces the whole table, so it is mounted in a
//     `<div>` and a `<tr>` there is invalid nesting — React logs
//     `validateDOMNesting` and the browser has to guess at the layout.
//
// Two `SiteAdmin` tables and the manual-entry preview had copied the
// `TableShell` form into `ResponsiveTable`, silently breaking their empty
// states. The rule is about markup, not behaviour, so it is a source scan: the
// audit only catches it when a screen happens to have no rows at the moment it
// is opened.

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

/**
 * Drop comments before scanning.
 *
 * The comments beside these slots exist to explain the rule, so they quote the
 * markup they warn about; scanning them would make an explanatory comment fail
 * the build.
 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
}

/** The text of every `empty={...}` expression in a file, brace-balanced. */
function emptyExpressions(source: string): string[] {
  const found: string[] = [];
  const marker = 'empty={';
  let from = 0;
  for (;;) {
    const start = source.indexOf(marker, from);
    if (start === -1) return found;
    let depth = 0;
    let i = start + marker.length - 1; // sit on the opening brace
    for (; i < source.length; i += 1) {
      if (source[i] === '{') depth += 1;
      else if (source[i] === '}') {
        depth -= 1;
        if (depth === 0) break;
      }
    }
    found.push(source.slice(start + marker.length, i));
    from = i;
  }
}

const TABLE_MARKUP = /<\/?(tr|td|th|tbody|thead)\b/;

/** True when the file renders a `ResponsiveTable` at all. */
function usesResponsiveTable(source: string): boolean {
  return /<ResponsiveTable[\s<]/.test(source);
}

describe('ResponsiveTable empty slot', () => {
  it('is never handed table markup, which would nest a row inside a div', () => {
    const offenders: string[] = [];

    for (const file of walk(SRC)) {
      const source = readFileSync(file, 'utf8');
      if (!usesResponsiveTable(source)) continue;

      // Only the expressions in a file that renders ResponsiveTable matter;
      // TableShell's `empty` legitimately holds a `<tr>`, and pairing the two
      // needs the JSX tree rather than a regex. Every offender found here is
      // re-checked by hand against the component it belongs to.
      for (const expression of emptyExpressions(stripComments(source))) {
        if (TABLE_MARKUP.test(expression)) {
          offenders.push(`${file.slice(SRC.length + 1)}: ${expression.trim().slice(0, 80)}`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });

  it('renders the whole table away rather than leaving an empty one', () => {
    // The contract the guard above depends on: the empty slot is mounted in a
    // plain container, with no table around it.
    const source = readFileSync(join(SRC, 'components/ui/Table.tsx'), 'utf8');
    const branch = source.slice(source.indexOf('if (rows.length === 0)'));
    const emptyBranch = branch.slice(0, branch.indexOf('\n  }'));
    expect(emptyBranch).toContain('<div');
    expect(emptyBranch).not.toContain('<table');
  });
});
