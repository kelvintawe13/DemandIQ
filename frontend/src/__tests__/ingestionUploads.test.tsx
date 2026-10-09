import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, act, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: { id: 'u1', name: 'Olivia Owner', role: 'Owner', roleKey: 'owner', permissions: ['ingest:write'] },
  }),
}));

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));

vi.mock('../components/ingestion/ManualFormTab', () => ({ ManualFormTab: () => <div /> }));
vi.mock('../components/ingestion/ExcelCsvTab', () => ({ ExcelCsvTab: () => <div /> }));
vi.mock('../components/ingestion/PosMomoTab', () => ({ PosMomoTab: () => <div /> }));
vi.mock('../components/UploadImagePanel', () => ({ UploadImagePanel: () => <div /> }));
vi.mock('../components/PreviewPanel', () => ({ PreviewPanel: () => <div /> }));

vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    constructor(
      public status: number,
      public error: { code: string; message: string; reference?: string | null } = {
        code: 'server_error',
        message: 'Request failed',
      },
    ) {
      super(error.message);
    }
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { ImportDemandData } from '../pages/ImportDemandData';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockDelete = api.delete as unknown as ReturnType<typeof vi.fn>;

const upload = (n: number) => ({
  id: `up-${n}`,
  kind: 'csv',
  filename: `demand_${String(n).padStart(2, '0')}.csv`,
  status: 'failed',
  row_count: 10,
  error_count: 1,
  created_at: new Date(Date.UTC(2026, 0, 1, 0, n)).toISOString(),
});

/** Cursors are the `created_at` of the last row on a page, as the API returns. */
const page = (rows: ReturnType<typeof upload>[], hasMore: boolean) => ({
  data: rows,
  has_more: hasMore,
  next_cursor: hasMore ? rows[rows.length - 1]?.created_at ?? null : null,
});

const listRows = () => screen.queryAllByRole('row').slice(1); // drop the header row
// The filename is the row header (the first column names its row).
const filenames = () => listRows().map((r) => within(r).getByRole('rowheader')?.textContent);

beforeEach(() => {
  vi.clearAllMocks();
  window.confirm = vi.fn(() => true);
});

describe('Recent uploads list', () => {
  it('pages forward and back through uploads without duplicating or dropping rows', async () => {
    mockGet
      .mockResolvedValueOnce(page([upload(1), upload(2)], true))
      .mockResolvedValueOnce(page([upload(3)], false))
      // Going back re-requests page 1.
      .mockResolvedValueOnce(page([upload(1), upload(2)], true));

    render(<ImportDemandData />);
    await waitFor(() => expect(filenames()).toHaveLength(2));
    expect(filenames()).toEqual(['demand_01.csv', 'demand_02.csv']);

    await userEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(filenames()).toEqual(['demand_03.csv']));
    expect(mockGet).toHaveBeenLastCalledWith('/api/v1/ingest/uploads', {
      limit: 10,
      cursor: upload(2).created_at,
    });

    await userEvent.click(screen.getByRole('button', { name: 'Previous' }));
    await waitFor(() => expect(filenames()).toEqual(['demand_01.csv', 'demand_02.csv']));
    // Back on page 1 there is nothing before it.
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();
  });

  it('steps back a page when deleting the last row empties the current page', async () => {
    const user = userEvent.setup();
    // Page 1 has one row, page 2 has the only row that will be deleted.
    mockGet
      .mockResolvedValueOnce(page([upload(1)], true))
      .mockResolvedValueOnce(page([upload(2)], false))
      // After the delete, refetching page 2's cursor yields nothing.
      .mockResolvedValueOnce(page([], false))
      // ...so the list walks back to page 1.
      .mockResolvedValueOnce(page([upload(1)], true));

    render(<ImportDemandData />);
    await waitFor(() => expect(filenames()).toEqual(['demand_01.csv']));

    await user.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(filenames()).toEqual(['demand_02.csv']));

    mockDelete.mockResolvedValue(undefined);
    await user.click(screen.getByRole('button', { name: /Delete demand_02\.csv/ }));

    // Lands on page 1 instead of an empty list.
    await waitFor(() => expect(filenames()).toEqual(['demand_01.csv']));
    // Crucially, it must not claim there are no uploads at all.
    expect(screen.queryByText('No imports yet')).not.toBeInTheDocument();
    // And the operator keeps a way back.
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled();
  });

  it('caps the upload list so a busy history cannot push the page down', async () => {
    mockGet.mockResolvedValue(page([upload(1), upload(2)], false));
    render(<ImportDemandData />);
    await waitFor(() => expect(filenames()).toHaveLength(2));

    // Ten rows per page is taller than the panels around it, so the box is
    // capped and its header pinned.
    const table = screen.getByRole('table', { name: /your uploads/i });
    const box = table.parentElement as HTMLElement;
    expect(box.className).toContain('max-h-[420px]');
    expect(box.className).toContain('overflow-y-auto');
    expect(table.querySelector('thead th')?.className).toContain('sticky');
  });

  it('shows the empty state only when the workspace truly has no uploads', async () => {
    mockGet.mockResolvedValue(page([], false));
    render(<ImportDemandData />);
    await waitFor(() => expect(screen.getByText('No imports yet')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Upload a spreadsheet/ })).toBeInTheDocument();
  });

  it('keeps committed uploads undiscardable', async () => {
    mockGet.mockResolvedValue({
      data: [{ ...upload(1), status: 'committed' }],
      has_more: false,
      next_cursor: null,
    });
    render(<ImportDemandData />);
    await waitFor(() => expect(screen.getByText('Imported')).toBeInTheDocument());
    // A committed upload has already produced observations; there is no
    // discard control, and so no way to delete it from this screen.
    expect(screen.queryByRole('button', { name: /Delete demand_01\.csv/ })).not.toBeInTheDocument();
  });

  it('collapses repeated Refresh clicks into a single request', async () => {
    mockGet.mockResolvedValue(page([upload(1), upload(2)], true));

    render(<ImportDemandData />);
    await waitFor(() => expect(filenames()).toEqual(['demand_01.csv', 'demand_02.csv']));
    expect(mockGet).toHaveBeenCalledTimes(1);

    /* Three presses landing in one JS task, the way an impatient double-click
       does: React batches across the burst, so `disabled` never catches up and
       all three handlers run. One `act` for the whole burst is what reproduces
       that; separate `fireEvent` calls would each flush and disable the button,
       hiding the duplicate. */
    const refresh = screen.getByRole('button', { name: /Refresh/ });
    expect(refresh).toBeEnabled();
    act(() => {
      fireEvent.click(refresh);
      fireEvent.click(refresh);
      fireEvent.click(refresh);
    });

    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
    // All three presses asked for the page already in flight, which will deliver
    // the same rows anyway, so only the first may reach the network.
    await waitFor(() => expect(filenames()).toEqual(['demand_01.csv', 'demand_02.csv']));
    expect(mockGet).toHaveBeenCalledTimes(2);
  });

  it('ignores a slow earlier page that resolves after a newer one', async () => {
    const user = userEvent.setup();
    let releasePage2: (v: ReturnType<typeof page>) => void = () => {};
    const slowPage2 = new Promise<ReturnType<typeof page>>((r) => { releasePage2 = r; });

    mockGet
      .mockResolvedValueOnce(page([upload(1), upload(2)], true)) // page 1 — fast
      .mockReturnValueOnce(slowPage2) // page 2 — slow
      .mockResolvedValueOnce(page([upload(1), upload(2)], true)); // back to page 1 — fast

    render(<ImportDemandData />);
    await waitFor(() => expect(filenames()).toEqual(['demand_01.csv', 'demand_02.csv']));

    // Go forward; page 2 is slow and stays open.
    await user.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));

    // Go back while page 2 is still loading. A different cursor, so it is allowed
    // through the guard and becomes the newest request.
    await user.click(screen.getByRole('button', { name: 'Previous' }));
    await waitFor(() => expect(filenames()).toEqual(['demand_01.csv', 'demand_02.csv']));

    // Page 2 finally answers, late. It must not replace the current page.
    await act(async () => {
      releasePage2(page([upload(3)], false));
    });
    expect(filenames()).toEqual(['demand_01.csv', 'demand_02.csv']);
  });
});
