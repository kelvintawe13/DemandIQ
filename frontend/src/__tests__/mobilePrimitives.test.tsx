import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Toggle } from '../components/ui/Toggle';
import { TabBar } from '../components/ui/TabBar';
import { ResponsiveTable, type Column } from '../components/ui/Table';

/* ------------------------------------------------------------------- Toggle */

describe('Toggle', () => {
  it('reports the next value rather than making every caller invert it', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="Rule enabled" />);

    await user.click(screen.getByRole('switch', { name: 'Rule enabled' }));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it('reports false when switching off', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Toggle checked onChange={onChange} label="Rule enabled" />);

    await user.click(screen.getByRole('switch', { name: 'Rule enabled' }));
    expect(onChange).toHaveBeenCalledWith(false);
  });

  it('exposes the checked state to assistive tech', () => {
    render(<Toggle checked onChange={() => {}} label="Rule enabled" />);
    expect(screen.getByRole('switch', { name: 'Rule enabled' })).toHaveAttribute('aria-checked', 'true');
  });

  it('keeps a phone-sized touch target while looking its designed size', () => {
    render(<Toggle checked={false} onChange={() => {}} label="Rule enabled" />);
    const button = screen.getByRole('switch', { name: 'Rule enabled' });
    // The track is an inner element precisely so the button can carry padding
    // for a 44px target without painting a 44px-tall switch.
    const cls = button.className;
    expect(cls).toContain('py-2.5');
    expect(cls).toContain('-my-2.5');
    expect(button.querySelector('span.rounded-full')).not.toBeNull();
  });

  it('disables itself and explains why when the seat cannot change it', () => {
    render(<Toggle checked disabled onChange={() => {}} label="Rule enabled" title="Your seat cannot change alert rules" />);
    const button = screen.getByRole('switch', { name: 'Rule enabled' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'Your seat cannot change alert rules');
  });

  it('falls back to a seat message when disabled without a reason', () => {
    render(<Toggle checked disabled onChange={() => {}} label="Rule enabled" />);
    expect(screen.getByRole('switch', { name: 'Rule enabled' })).toHaveAttribute(
      'title',
      'Rule enabled cannot be changed by your seat',
    );
  });
});

/* -------------------------------------------------------------------- TabBar */

describe('TabBar', () => {
  it('gives a phone tab a thumb-sized height', () => {
    render(
      <TabBar
        ariaLabel="Access control"
        value="Members"
        onChange={() => {}}
        tabs={[{ id: 'Members', label: 'Members' }, { id: 'Roles', label: 'Roles' }]}
      />,
    );
    const tab = screen.getByRole('tab', { name: 'Members' });
    // 31px was the old height — too short to hit reliably with a thumb.
    expect(tab.className).toContain('min-h-[40px]');
    expect(tab.className).toContain('sm:min-h-0');
  });

  it('scrolls rather than overflowing when the tabs do not fit', () => {
    render(
      <TabBar
        ariaLabel="Access control"
        value="a"
        onChange={() => {}}
        tabs={[{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }]}
      />,
    );
    expect(screen.getByRole('tablist', { name: 'Access control' }).className).toContain('overflow-x-auto');
  });
});

/* ----------------------------------------------------------- ResponsiveTable */

interface Member {
  id: string;
  name: string;
  role: string;
}

const columns: Column<Member>[] = [
  { key: 'name', label: 'Member', primary: true, render: (m) => m.name },
  { key: 'role', label: 'Role', render: (m) => m.role },
  { key: 'internal', label: 'Internal', cardHidden: true, render: (m) => m.id },
];

const rows: Member[] = [
  { id: 'u1', name: 'Aline', role: 'Owner' },
  { id: 'u2', name: 'Eric', role: 'Viewer' },
];

describe('ResponsiveTable', () => {
  it('renders one tree, not a card copy plus a table copy', () => {
    render(<ResponsiveTable columns={columns} rows={rows} rowKey={(m) => m.id} />);
    // Two rows, two matching values: a duplicated DOM would double these and
    // every query in the app would have to say which copy it meant.
    expect(screen.getAllByText('Aline')).toHaveLength(1);
    expect(screen.getAllByRole('table')).toHaveLength(1);
    expect(screen.getByText('Eric')).toBeInTheDocument();
  });

  it('carries the column label on every cell so CSS can build the card', () => {
    render(<ResponsiveTable columns={columns} rows={rows} rowKey={(m) => m.id} />);
    const row = within(screen.getAllByRole('row')[1]);
    expect(row.getByText('Aline')).toHaveAttribute('data-label', 'Member');
    expect(row.getByText('Owner')).toHaveAttribute('data-label', 'Role');
  });

  it('marks the primary column as the card heading', () => {
    render(<ResponsiveTable columns={columns} rows={rows} rowKey={(m) => m.id} />);
    const row = within(screen.getAllByRole('row')[1]);
    expect(row.getByText('Aline')).toHaveAttribute('data-primary', '');
    expect(row.getByText('Owner')).not.toHaveAttribute('data-primary');
  });

  it('marks desktop-only columns so a phone never shows them', () => {
    render(<ResponsiveTable columns={columns} rows={rows} rowKey={(m) => m.id} />);
    const row = within(screen.getAllByRole('row')[1]);
    expect(row.getByText('u1')).toHaveAttribute('data-card-hidden', '');
  });

  it('renders the empty state instead of an empty table', () => {
    render(
      <ResponsiveTable
        columns={columns}
        rows={[]}
        rowKey={(m) => m.id}
        empty={<p>No members yet</p>}
      />,
    );
    expect(screen.getByText('No members yet')).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('scrolls the desktop table rather than breaking the layout', () => {
    render(<ResponsiveTable columns={columns} rows={rows} rowKey={(m) => m.id} minWidth="min-w-[720px]" />);
    expect(screen.getByRole('table').className).toContain('min-w-[720px]');
  });
});
