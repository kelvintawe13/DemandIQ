import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));

vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    constructor(
      public status: number,
      public error: { code: string; message: string; reference?: string | null },
    ) {
      super(error.message);
    }
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn(), blob: vi.fn() },
}));

import { api, ApiException } from '../api/client';
import { UploadEntriesPanel } from '../components/UploadEntriesPanel';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPatch = api.patch as unknown as ReturnType<typeof vi.fn>;
const mockDelete = api.delete as unknown as ReturnType<typeof vi.fn>;
const mockBlob = api.blob as unknown as ReturnType<typeof vi.fn>;

const LOCATION = '2d6c6d77-df96-47bf-bd58-c3412a636a87';

const entries = [
  { id: 11, date: '2026-10-01', demand_qty: 58, location_id: LOCATION, notes: null },
  { id: 12, date: '2026-10-02', demand_qty: 47, location_id: LOCATION, notes: null },
];

/** The panel asks for its entries and, separately, for location names. */
const routeGet = (rows = entries) =>
  mockGet.mockImplementation((path: string) =>
    path === '/api/v1/locations'
      ? Promise.resolve({ data: [{ id: LOCATION, name: 'Remer Grills' }] })
      : Promise.resolve({ data: rows }),
  );

const renderPanel = (overrides: Partial<Parameters<typeof UploadEntriesPanel>[0]> = {}) =>
  render(
    <UploadEntriesPanel
      upload={{ id: 'up-1', filename: 'remera grills.jpeg', status: 'committed', row_count: 2, has_source: false }}
      onClose={vi.fn()}
      onChanged={vi.fn()}
      {...overrides}
    />,
  );

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom has no object URLs; the photo tests observe their lifetime through
  // these stubs.
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:page-1'), writable: true });
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), writable: true });
  window.confirm = vi.fn(() => true);
});

describe('Entries from an import', () => {
  it('lists the stored entries for the import, with readable location names', async () => {
    routeGet();
    renderPanel();

    await waitFor(() => expect(screen.getByText('2026-10-01')).toBeInTheDocument());
    // Scoped to the upload, not to whatever is newest in the workspace.
    expect(mockGet).toHaveBeenCalledWith('/api/v1/ingest/demand', {
      upload_id: 'up-1',
      limit: 50,
      offset: 0,
    });
    expect(screen.getByText('58')).toBeInTheDocument();
    expect(screen.getAllByText('Remer Grills')).toHaveLength(2);
  });

  it('saves an edit through the same endpoint the hand-entry editor uses', async () => {
    routeGet();
    mockPatch.mockResolvedValue({});
    const onChanged = vi.fn();
    renderPanel({ onChanged });

    await userEvent.click(await screen.findByRole('button', { name: 'Edit the entry of 2026-10-01' }));
    const qty = screen.getByRole('spinbutton', { name: 'Visitors for the entry of 2026-10-01' });
    await userEvent.clear(qty);
    await userEvent.type(qty, '61');
    await userEvent.click(screen.getByRole('button', { name: 'Save the entry of 2026-10-01' }));

    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith('/api/v1/ingest/demand/11', {
        date: '2026-10-01',
        demand_qty: 61,
      }),
    );
    expect(toast).toHaveBeenCalledWith('Entry updated', 'success');
    // The upload's row counts may have changed, so the list is told to refresh.
    expect(onChanged).toHaveBeenCalled();
  });

  it('names the day and the value in a dialog before deleting from history', async () => {
    routeGet();
    mockDelete.mockResolvedValue(undefined);
    renderPanel();

    await userEvent.click(await screen.findByRole('button', { name: 'Delete the entry of 2026-10-02' }));

    // Permanent deletion is confirmed in the app's dialog, which states what
    // goes (the day and its count) — `window.confirm` cannot be styled and can
    // be suppressed outright by the browser.
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveAccessibleName('Delete the entry for 2026-10-02?');
    expect(within(dialog).getByText(/47 visitors/)).toBeInTheDocument();
    expect(window.confirm).not.toHaveBeenCalled();
    // Opening the question must not delete anything.
    expect(mockDelete).not.toHaveBeenCalled();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete entry' }));

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('/api/v1/ingest/demand/12'));
    expect(toast).toHaveBeenCalledWith('Entry deleted', 'success');
  });

  it('leaves the entry alone when the dialog is dismissed', async () => {
    routeGet();
    renderPanel();

    await userEvent.click(await screen.findByRole('button', { name: 'Delete the entry of 2026-10-02' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mockDelete).not.toHaveBeenCalled();
  });

  it('surfaces the API rule that refuses a duplicate day', async () => {
    routeGet();
    mockPatch.mockRejectedValue(
      new ApiException(409, {
        code: 'conflict',
        message: 'Another observation already exists for this location and date',
      }),
    );
    renderPanel();

    await userEvent.click(await screen.findByRole('button', { name: 'Edit the entry of 2026-10-01' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save the entry of 2026-10-01' }));

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        'Another observation already exists for this location and date',
        'error',
      ),
    );
    // The row stays in edit mode so the value can be corrected, not discarded.
    expect(
      screen.getByRole('spinbutton', { name: 'Visitors for the entry of 2026-10-01' }),
    ).toBeInTheDocument();
  });

  it('does not invent entries for an import that wrote none', async () => {
    routeGet([]);
    renderPanel({ upload: { id: 'up-2', filename: 'empty.csv', status: 'committed', row_count: 0 } });

    await waitFor(() => expect(screen.getByText('No entries yet')).toBeInTheDocument());
    expect(screen.queryAllByRole('row')).toHaveLength(0);
    // And it explains why rather than leaving a blank box.
    expect(screen.getByText(/wrote no rows/i)).toBeInTheDocument();
  });

  it('shows the stored page beside the entries so a value can be checked against it', async () => {
    routeGet();
    mockBlob.mockResolvedValue(new Blob(['jpeg'], { type: 'image/jpeg' }));
    renderPanel({
      upload: { id: 'up-1', filename: 'remera grills.jpeg', status: 'committed', row_count: 2, has_source: true },
    });

    // The photo belongs next to the rows being corrected, not in a panel that
    // replaces them.
    const page = await screen.findByAltText('Original page: remera grills.jpeg');
    expect(page).toHaveAttribute('src', 'blob:page-1');
    expect(mockBlob).toHaveBeenCalledWith('/api/v1/ingest/uploads/up-1/source');
    expect(screen.getByText('2026-10-01')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit the entry of 2026-10-01' })).toBeInTheDocument();
  });

  it('offers no photo pane for an import whose original was not kept', async () => {
    routeGet();
    renderPanel();
    await waitFor(() => expect(screen.getByText('2026-10-01')).toBeInTheDocument());

    expect(mockBlob).not.toHaveBeenCalled();
    expect(screen.queryByAltText(/Original page:/)).not.toBeInTheDocument();
  });

  it('releases the photo when the editor closes', async () => {
    routeGet();
    mockBlob.mockResolvedValue(new Blob(['jpeg'], { type: 'image/jpeg' }));
    const { unmount } = renderPanel({
      upload: { id: 'up-1', filename: 'remera grills.jpeg', status: 'committed', row_count: 2, has_source: true },
    });

    await screen.findByAltText('Original page: remera grills.jpeg');
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:page-1');
  });

  it('keeps the entries editable when the photo cannot be loaded', async () => {
    routeGet();
    mockBlob.mockRejectedValue(new ApiException(404, { code: 'no_source', message: 'no source' }));
    renderPanel({
      upload: { id: 'up-1', filename: 'old.jpeg', status: 'committed', row_count: 2, has_source: true },
    });

    await waitFor(() =>
      expect(screen.getByText(/stored photo could not be loaded/i)).toBeInTheDocument(),
    );
    // A missing photo is not a reason to take the editor away.
    expect(screen.getByRole('button', { name: 'Edit the entry of 2026-10-01' })).toBeInTheDocument();
  });

  it('marks the rows as already stored so an edit is understood as a rewrite', async () => {
    routeGet();
    renderPanel();
    await waitFor(() => expect(screen.getByText('2026-10-01')).toBeInTheDocument());
    const panel = screen.getByRole('region', { name: /entries from remera grills\.jpeg/i });
    expect(within(panel).getByText(/rewrites the stored value/i)).toBeInTheDocument();
  });
});
