/**
 * The Model Registry reads the API, it does not loop on itself.
 *
 * Selecting a default row used to change the list callback's identity, which
 * re-ran the mount effect and re-read every model — and restarted the
 * auto-refresh timer — the moment the first row selected itself. The second
 * half of the same bug was the full-page `LoadingState`, which unmounted the
 * whole screen on every re-read and so threw away the operator's selection,
 * scroll position and, in tests, any node captured just before it.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import '@testing-library/jest-dom';

vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: { name: 'Ada Admin', role: 'Administrator', roleKey: 'admin', permissions: ['models:read'] },
  }),
}));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { message: 'failed' };
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { ModelRegistry } from '../pages/admin/ModelRegistry';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const MODEL_ID = '11111111-2222-3333-4444-555555555555';
const VERSION = 'lightgbm-it30';
const MODEL_ROW = { id: MODEL_ID, version: VERSION, algorithm: 'lightgbm', status: 'production' };

/** Every read the page makes, with the one path whose count is under test. */
function routeGet(list: () => Promise<unknown>, seen: string[]) {
  return (path: string) => {
    seen.push(path);
    if (path === '/api/v1/models') return list();
    if (path === `/api/v1/models/${MODEL_ID}`) return Promise.resolve(MODEL_ROW);
    return Promise.resolve({ data: [] });
  };
}

const listReads = (seen: string[]) => seen.filter((p) => p === '/api/v1/models').length;

describe('Model Registry reads', () => {
  beforeEach(() => mockGet.mockReset());

  it('reads the model list once, even after the first row selects itself', async () => {
    const seen: string[] = [];
    mockGet.mockImplementation(routeGet(() => Promise.resolve({ data: [MODEL_ROW] }), seen));

    render(<ModelRegistry />);

    // Wait for the read to land and the registry to render, which is when the
    // default row has been selected and the detail read issued.
    await waitFor(() => expect(screen.getAllByText(VERSION).length).toBeGreaterThan(0));
    await waitFor(() => expect(seen).toContain(`/api/v1/models/${MODEL_ID}`));

    expect(listReads(seen)).toBe(1);
  });

  it('keeps the rows on screen while a refresh is in flight', async () => {
    const seen: string[] = [];
    let release: ((value: unknown) => void) | null = null;
    let reads = 0;
    mockGet.mockImplementation(
      routeGet(() => {
        reads += 1;
        if (reads === 1) return Promise.resolve({ data: [MODEL_ROW] });
        // The refresh the test triggers, held open so the mid-flight render can
        // be inspected.
        return new Promise((resolve) => {
          release = resolve;
        });
      }, seen),
    );

    render(<ModelRegistry />);
    await waitFor(() => expect(screen.getAllByText(VERSION).length).toBeGreaterThan(0));

    fireEvent.click(screen.getByRole('button', { name: /refresh/i }));

    // The old screen blanked here: `loading` alone flipped the whole page to
    // "Loading models", discarding everything already on it.
    await waitFor(() => expect(listReads(seen)).toBe(2));
    expect(screen.queryByText('Loading models')).toBeNull();
    expect(screen.getAllByText(VERSION).length).toBeGreaterThan(0);

    await act(async () => {
      release?.({ data: [MODEL_ROW] });
    });
  });
});
