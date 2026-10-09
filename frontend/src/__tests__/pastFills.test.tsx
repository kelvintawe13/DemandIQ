/**
 * `GET /ingest/demand` returns `recorded_at` beside each row — when the row was
 * written, which is not the same day as the `date` it reports. Past Fills listed
 * the reported day and nothing else, so a backfill of last week and a fill made
 * this morning read identically. These render the list against the shape the
 * router returns.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../contexts/NavigationContext', () => ({ useNavigation: () => ({ navigate: vi.fn() }) }));
vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  ApiException: class ApiException extends Error {},
}));

import { api } from '../api/client';
import { ManualFormTab } from '../components/ingestion/ManualFormTab';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

/** Two stored rows: one written after the fact, one the API timestamps itself. */
const ROWS = [
  {
    id: 1,
    location_id: 'loc-1',
    date: '2026-09-19',
    demand_qty: 412,
    notes: null,
    recorded_at: '2026-09-20T06:15:00Z',
  },
  {
    id: 2,
    location_id: 'loc-1',
    date: '2026-09-18',
    demand_qty: 388,
    notes: 'backfill',
    recorded_at: null,
  },
];

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/locations')
      return Promise.resolve({ data: [{ id: 'loc-1', name: 'Kigali Heights Bistro' }] });
    if (path === '/api/v1/ingest/demand') return Promise.resolve({ data: ROWS, total: 2 });
    return Promise.resolve({ data: [] });
  });
});

describe('Past Fills', () => {
  it('says when a row was recorded, on the row that reports a different day', async () => {
    render(<ManualFormTab />);

    await waitFor(() => {
      const row = screen.getByText(/412 visitors/).closest('li') as HTMLElement;
      expect(row.textContent).toMatch(/Recorded /);
      // The reported day is still the row's headline, not replaced by it.
      expect(row.textContent).toMatch(/412 visitors/);
    });
  });

  it('leaves the recorded time out when the API does not report one', async () => {
    render(<ManualFormTab />);
    // One row carries a timestamp and the other does not, so exactly one line
    // is printed — nothing is invented for the row the API left silent.
    await waitFor(() => expect(screen.getAllByText(/^Recorded /)).toHaveLength(1));
  });
});
