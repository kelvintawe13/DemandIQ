/**
 * When a manual entry is saved, the form has to come back empty.
 *
 * Why this is a correctness rule and not a nicety: `commit_demand` upserts on
 * `(location_id, date)`. A draft that survives its own save therefore sits one
 * click away from *overwriting* the day just stored — the operator types a new
 * count, presses Save, and the previous row is silently replaced instead of a
 * second day being added. The date going back to the workspace's today is part
 * of the same guard: leaving the saved day in place is what makes that re-write
 * possible.
 *
 * Past Fills is pinned here too. It used to refresh on `entries.length`, which
 * is 0 both before and after a save of the bare form — so the row the operator
 * had just written was the one row missing from the list below.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../contexts/NavigationContext', () => ({ useNavigation: () => ({ navigate: vi.fn() }) }));
vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  ApiException: class ApiException extends Error {},
}));

import { api } from '../api/client';
import { ManualFormTab } from '../components/ingestion/ManualFormTab';
import { todayISO } from '../lib/workspaceTime';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

const DEMAND_PATH = '/api/v1/ingest/demand';
const demandFetches = () => mockGet.mock.calls.filter((c) => c[0] === DEMAND_PATH).length;

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/locations')
      return Promise.resolve({ data: [{ id: 'loc-1', name: 'Kimironko Kitchen' }] });
    if (path === DEMAND_PATH) return Promise.resolve({ data: [], total: 0 });
    return Promise.resolve({ data: [] });
  });
  mockPost.mockResolvedValue({ committed: 1, reconciled: 0, total: 1 });
});

/** Render the tab and fill every field the form needs to be saveable. */
async function fillForm() {
  render(<ManualFormTab />);
  const visitors = (await screen.findByLabelText(
    /Number of Visitors \/ Demand/,
  )) as HTMLInputElement;
  await waitFor(() =>
    expect(screen.getByLabelText(/Location \/ Site/)).not.toBeDisabled(),
  );
  fireEvent.change(screen.getByLabelText(/Location \/ Site/), { target: { value: 'loc-1' } });
  fireEvent.change(visitors, { target: { value: '150' } });
  fireEvent.change(screen.getByLabelText(/Notes \(optional\)/), {
    target: { value: 'market day' },
  });
  return visitors;
}

describe('manual entry form reset after a save', () => {
  it('blanks the count and notes the moment the row is stored', async () => {
    const visitors = await fillForm();
    expect(visitors.value).toBe('150');

    // Saving the bare form — no queued entries — is the path that used to leave
    // the draft sitting there untouched.
    fireEvent.click(screen.getByRole('button', { name: /Save Entry/i }));
    await waitFor(() => expect(mockPost).toHaveBeenCalled());

    await waitFor(() => expect(visitors.value).toBe(''));
    expect((screen.getByLabelText(/Notes \(optional\)/) as HTMLTextAreaElement).value).toBe('');
  });

  it('puts the date back to the workspace day, so the next save is a new day', async () => {
    const visitors = await fillForm();
    const date = screen.getByLabelText(/^Date$/) as HTMLInputElement;
    fireEvent.change(date, { target: { value: '2026-01-05' } });

    fireEvent.click(screen.getByRole('button', { name: /Save Entry/i }));
    await waitFor(() => expect(visitors.value).toBe(''));

    expect(date.value).toBe(todayISO());
  });

  it('refetches Past Fills after saving the bare form', async () => {
    await fillForm();
    const before = demandFetches();

    fireEvent.click(screen.getByRole('button', { name: /Save Entry/i }));
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    await waitFor(() => expect(demandFetches()).toBeGreaterThan(before));
  });

  it('blanks the count when an entry is queued, not only the notes', async () => {
    const visitors = await fillForm();

    fireEvent.click(screen.getByRole('button', { name: /Add another entry/i }));

    // The queued row is listed...
    await waitFor(() => expect(screen.getByText(/150 visitors/)).toBeInTheDocument());
    // ...and the field no longer holds the value that was just queued, so the
    // next Add cannot be the same day of demand twice.
    await waitFor(() => expect(visitors.value).toBe(''));
  });

  it('keeps the location selected across a save', async () => {
    const visitors = await fillForm();

    fireEvent.click(screen.getByRole('button', { name: /Save Entry/i }));
    await waitFor(() => expect(visitors.value).toBe(''));

    // Logging several days for one site is the common case, so the site stays
    // chosen; only the row-level fields are cleared.
    expect((screen.getByLabelText(/Location \/ Site/) as HTMLSelectElement).value).toBe('loc-1');
  });
});
