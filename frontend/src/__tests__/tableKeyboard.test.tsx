import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ResponsiveTable, TableShell, TRow, TD } from '../components/ui/Table';

// A selectable row used to be mouse-only: `onRowClick` rendered a plain `<tr
// onClick>`, which no keyboard could activate. The table now makes such rows
// focusable and activates them on Enter or Space.

interface Row {
  id: string;
  name: string;
}

const rows: Row[] = [
  { id: 'm-1', name: 'catboost-fallback' },
  { id: 'm-2', name: 'lightgbm-v3' },
];

function renderTable(onRowClick: (row: Row) => void) {
  render(
    <ResponsiveTable<Row>
      caption="Models"
      rows={rows}
      rowKey={(row) => row.id}
      onRowClick={onRowClick}
      rowAriaLabel={(row) => `Select ${row.name}`}
      columns={[
        { key: 'name', label: 'Model', primary: true, render: (row) => row.name },
      ]}
    />,
  );
}

describe('ResponsiveTable keyboard rows', () => {
  it('activates a selectable row with Enter', async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    renderTable(onRowClick);

    const row = screen.getByRole('row', { name: 'Select lightgbm-v3' });
    row.focus();
    await user.keyboard('{Enter}');

    expect(onRowClick).toHaveBeenCalledTimes(1);
    expect(onRowClick.mock.calls[0][0]).toMatchObject({ id: 'm-2' });
  });

  it('activates a selectable row with Space', async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    renderTable(onRowClick);

    const row = screen.getByRole('row', { name: 'Select catboost-fallback' });
    row.focus();
    await user.keyboard(' ');

    expect(onRowClick).toHaveBeenCalledTimes(1);
    expect(onRowClick.mock.calls[0][0]).toMatchObject({ id: 'm-1' });
  });

  it('leaves a non-selectable row out of the tab order', () => {
    render(
      <ResponsiveTable<Row>
        caption="Models"
        rows={rows}
        rowKey={(row) => row.id}
        columns={[{ key: 'name', label: 'Model', primary: true, render: (row) => row.name }]}
      />,
    );

    expect(screen.getAllByRole('row')[1]).not.toHaveAttribute('tabindex');
  });
});

// `TableShell` had no caption at all, so its tables were announced unnamed: a
// panel heading names the panel, not the table. The caption is hidden by default
// because every table here already sits under a visible heading.

describe('table captions', () => {
  it('names a TableShell table, hidden by default', () => {
    render(
      <TableShell caption="Platform accounts" columns={['User']}>
        <TRow><TD>owner@demandiq.app</TD></TRow>
      </TableShell>,
    );

    const caption = screen.getByRole('table').querySelector('caption');
    expect(caption).toHaveTextContent('Platform accounts');
    expect(caption).toHaveClass('sr-only');
  });

  it('can draw a caption for a table with no heading of its own', () => {
    render(
      <TableShell caption="Standalone" captionVisible columns={['User']}>
        <TRow><TD>a</TD></TRow>
      </TableShell>,
    );

    expect(screen.getByRole('table').querySelector('caption')).not.toHaveClass('sr-only');
  });

  it('keeps the scroll box a containing block for the clipped caption', () => {
    render(
      <TableShell caption="X" columns={['User']}>
        <TRow><TD>a</TD></TRow>
      </TableShell>,
    );

    expect(screen.getByRole('table').parentElement).toHaveClass('relative');
  });

  it('can name a TableShell row with a header cell', () => {
    render(
      <TableShell caption="Workspaces" columns={['Workspace', 'Users']}>
        <TRow>
          <TD scope="row">Kimironko Kitchen</TD>
          <TD>4</TD>
        </TRow>
      </TableShell>,
    );

    const header = screen.getByRole('rowheader');
    expect(header).toHaveTextContent('Kimironko Kitchen');
    // The `th` branch cancels the browser's centred, bold default for `th`, so
    // the cell looks exactly as it did as a `td`.
    expect(header).toHaveClass('text-left', 'font-normal');
    expect(screen.getAllByRole('cell')).toHaveLength(1);
  });

  it('caps a TableShell directory and pins an opaque header', () => {
    render(
      <TableShell caption="Platform accounts" maxHeight="max-h-[520px]" columns={['User']}>
        <TRow><TD scope="row">owner@demandiq.app</TD></TRow>
      </TableShell>,
    );

    const head = screen.getByRole('columnheader', { name: 'User' });
    expect(head.className).toContain('sticky');
    // Without an opaque fill the rows would read through the pinned header.
    expect(head.className).toContain('bg-white');

    const box = screen.getByRole('table').parentElement as HTMLElement;
    expect(box.className).toContain('max-h-[520px]');
    expect(box.className).toContain('overflow-y-auto');
    expect(box.className).toContain('overscroll-contain');
  });

  it('leaves an uncapped TableShell on the page scroll', () => {
    render(
      <TableShell caption="X" columns={['User']}>
        <TRow><TD>a</TD></TRow>
      </TableShell>,
    );

    expect(screen.getByRole('columnheader', { name: 'User' }).className).not.toContain('sticky');
    expect(screen.getByRole('table').parentElement?.className).not.toContain('overflow-y-auto');
  });
});

// The `sr-only` caption is `position: absolute`. Its scroll box used to be
// `position: static`, so the caption resolved against the initial containing
// block, escaped `main`'s overflow clip, and stretched the document — on
// /forecast the whole UI could be scrolled 2342px into blank space. A
// positioned scroll box keeps it clipped inside the table.

describe('ResponsiveTable scroll container', () => {
  it('is a containing block for the clipped caption', () => {
    render(
      <ResponsiveTable<Row>
        caption="Models"
        rows={rows}
        rowKey={(row) => row.id}
        columns={[{ key: 'name', label: 'Model', primary: true, render: (row) => row.name }]}
      />,
    );

    const box = screen.getByRole('table').parentElement as HTMLElement;
    expect(box.className).toContain('relative');
    expect(screen.getByRole('table').querySelector('caption')).toHaveClass('sr-only');
  });
});

// A list that grows with the data (an upload history, a daily breakdown) made
// its panel as tall as the data was long. `maxHeight` caps the box, pins the
// header, and contains the scroll so reaching the end of the list does not drag
// the page along with it.

describe('ResponsiveTable capped lists', () => {
  function renderCapped(maxHeight?: string) {
    render(
      <ResponsiveTable<Row>
        caption="Models"
        rows={rows}
        rowKey={(row) => row.id}
        maxHeight={maxHeight}
        columns={[{ key: 'name', label: 'Model', primary: true, render: (row) => row.name }]}
      />,
    );
  }

  it('caps the scroll box and pins an opaque header', () => {
    renderCapped('max-h-[420px]');

    const head = screen.getByRole('columnheader', { name: 'Model' });
    expect(head.className).toContain('sticky');
    // Without an opaque fill the rows would read through the pinned header.
    expect(head.className).toContain('bg-white');

    const box = screen.getByRole('table').parentElement as HTMLElement;
    expect(box.className).toContain('max-h-[420px]');
    expect(box.className).toContain('overflow-y-auto');
    // A nested scroller that chains its momentum to the page is the trap this
    // avoids: at the end of the list, the page stays where it is.
    expect(box.className).toContain('overscroll-contain');
  });

  it('leaves an uncapped list on the page scroll, with a plain header', () => {
    renderCapped(undefined);

    expect(screen.getByRole('columnheader', { name: 'Model' }).className).not.toContain('sticky');
    const box = screen.getByRole('table').parentElement as HTMLElement;
    expect(box.className).not.toContain('overflow-y-auto');
  });
});
