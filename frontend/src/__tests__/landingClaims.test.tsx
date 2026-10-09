import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Landing } from '../pages/Landing';
import { evidencePoints } from '../data/landing';

/* The landing page is the one screen every visitor sees before any of the
   product's own evidence exists, so its numbers have to survive the same
   scrutiny the app applies to its data. Two rules: a published figure names the
   report it came from, and the page never states a performance result the
   repository cannot evidence. */

function renderLanding() {
  return render(<Landing onSignIn={() => {}} onGetStarted={() => {}} />);
}

describe('landing evidence figures', () => {
  it('names the report and year behind every published figure', () => {
    for (const point of evidencePoints) {
      expect(point.source).toMatch(/Food Waste Index Report 20\d\d/);
    }
  });

  it('gives every figure a value and a plain-language label', () => {
    for (const point of evidencePoints) {
      expect(point.value.trim().length).toBeGreaterThan(0);
      expect(point.label.trim().length).toBeGreaterThan(0);
    }
  });

  /* "Research" and "Various academic studies" were rendered in a slot styled
     as a statistic, so a reader took them for one. A figure that is not a
     number does not belong in the number grid. */
  it('never puts a non-numeric placeholder in the statistics row', () => {
    for (const point of evidencePoints) {
      expect(point.value).toMatch(/^[\d$]/);
      expect(point.value).not.toMatch(/research|varies|various|etc/i);
    }
  });
});

describe('landing performance claims', () => {
  /* "90% of days land in our range" appeared twice — as a hero stat and as the
     community tile. Ninety percent is the model's *target* interval coverage
     (conformal.py defaults to target_coverage=0.90); no run manifest reports
     realised coverage, so the page was asserting a measured result that does not
     exist anywhere in this repository. */
  it('does not claim a hit rate the repository cannot evidence', () => {
    renderLanding();
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/\d+%\s*of days/i);
    expect(text).not.toMatch(/days land in our range/i);
    expect(text).not.toMatch(/accuracy of \d/i);
    expect(text).not.toMatch(/\d+%\s*accuracy/i);
  });

  /* A bare "90%" elsewhere on the page is fine — the demo card's interval badge
     names the level of the interval being shown. What is not fine is a percentage
     presented as an outcome without a source. Every sourced figure on the page
     lives in the evidence row and names its report. */
  it('keeps every published figure inside the sourced evidence row', () => {
    renderLanding();
    for (const point of evidencePoints) {
      const value = screen.getByText(point.value);
      expect(value.closest('li')?.textContent).toContain(point.source);
    }
    // The evidence row is the only place a sourced figure appears, so the
    // citation count matches the figure count exactly.
    expect(screen.getAllByText(/Food Waste Index Report 20\d\d/)).toHaveLength(
      evidencePoints.length);
  });

  it('attributes each figure to a source on the page itself', () => {
    renderLanding();
    for (const point of evidencePoints) {
      const value = screen.getByText(point.value);
      const item = value.closest('li');
      expect(item, `${point.value} is not in the evidence list`).not.toBeNull();
      expect(item?.textContent).toContain(point.source);
    }
  });
});

describe('landing header', () => {
  /* `#root` carries `overflow: hidden` for the app shell, which makes it a
     scrollport; `position: sticky` binds to the nearest scrollport, so a header
     stuck to the document scrolled away instead of pinning. */
  it('pins the header inside a scroll container it controls', () => {
    renderLanding();
    const header = screen.getByRole('banner');
    expect(header.className).toMatch(/sticky/);
    expect(header.className).toMatch(/top-0/);

    const scroller = header.parentElement as HTMLElement;
    expect(scroller.className).toMatch(/overflow-y-auto/);
    expect(scroller.className).toMatch(/100dvh/);
  });
});
