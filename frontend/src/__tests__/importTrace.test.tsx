/**
 * After a spreadsheet import, the screen has to be able to name the demand rows
 * that file wrote.
 *
 * The tab used to reset itself the instant a commit landed and say nothing more,
 * so "which rows did this import produce?" had no answer on the screen that
 * produced them. The trace asks `GET /ingest/demand` for the rows *by
 * `upload_id`* — an equality on the row's own foreign key. Matching on dates or
 * on the filename appearing in free-text notes would also "find" a manual entry
 * that merely mentioned the file, which is not a trace.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), postForm: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  ApiException: class ApiException extends Error {
    status = 0;
    error = { message: 'boom' };
    reference = null;
  },
}));

import { api } from '../api/client';
import { ExcelCsvTab } from '../components/ingestion/ExcelCsvTab';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;
const mockPostForm = api.postForm as unknown as ReturnType<typeof vi.fn>;

const UPLOAD = '11111111-1111-1111-1111-111111111111';

function serveApi(demandRows: Array<Record<string, unknown>>) {
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/locations')
      return Promise.resolve({ data: [{ id: 'loc-1', name: 'Kigali Central' }] });
    if (path === '/api/v1/ingest/demand') return Promise.resolve({ data: demandRows, total: demandRows.length });
    return Promise.resolve({ data: [] });
  });
  mockPostForm.mockResolvedValue({
    upload_id: UPLOAD,
    detected_headers: ['date', 'covers', 'location'],
    preview: [{ date: '2026-01-10', covers: '40', location: 'Kigali Central' }],
    row_count: 2,
    status: 'awaiting_mapping',
  });
  mockPost.mockImplementation((path: string) => {
    if (path.endsWith('/mapping'))
      return Promise.resolve({ mapped_rows: 2, error_count: 0, errors: [], status: 'ready' });
    if (path.endsWith('/commit'))
      return Promise.resolve({ committed: 2, reconciled: 0, status: 'committed' });
    return Promise.resolve({});
  });
}

/** Drive upload -> mapping -> commit through the real controls. */
async function importAFile() {
  render(<ExcelCsvTab />);
  const input = screen.getByLabelText('Spreadsheet file') as HTMLInputElement;
  const file = new File(['date,covers,location\n2026-01-10,40,Kigali Central\n'], 'january.csv', {
    type: 'text/csv',
  });
  fireEvent.change(input, { target: { files: [file] } });

  // The mapping guesses 'date'/'covers' from the detected headers.
  await waitFor(() =>
    expect(screen.getByRole('button', { name: /Validate mapping/ })).not.toBeDisabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: /Validate mapping/ }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: /Review & Confirm/ })).not.toBeDisabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: /Review & Confirm/ }));
  fireEvent.click(await screen.findByRole('button', { name: /Import 2 rows/ }));
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPostForm.mockReset();
});

describe('spreadsheet import trace', () => {
  it('names the rows the committed file wrote, by upload id', async () => {
    serveApi([
      { id: 2, location_id: 'loc-1', date: '2026-01-11', demand_qty: 55, upload_id: UPLOAD, upload_filename: 'january.csv' },
      { id: 1, location_id: 'loc-1', date: '2026-01-10', demand_qty: 40, upload_id: UPLOAD, upload_filename: 'january.csv' },
    ]);
    await importAFile();

    // Scoped to the trace's own region: the filename legitimately appears more
    // than once inside it (the heading note and each row's "From" cell).
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Import trace' })).toHaveTextContent(
        'january.csv',
      ),
    );

    // The trace must be an equality on the row's own upload_id, not a guess.
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/ingest/demand',
        expect.objectContaining({ upload_id: UPLOAD }),
      ),
    );

    await waitFor(() => expect(screen.getByText('2026-01-10')).toBeInTheDocument());
    expect(screen.getByText('2026-01-11')).toBeInTheDocument();
    expect(screen.getByText('2 rows')).toBeInTheDocument();
    // The range is read from the rows the API returned.
    expect(screen.getByText(/2026-01-10 → 2026-01-11/)).toBeInTheDocument();
    // Location names come from the locations list, not the raw id.
    expect(screen.getAllByText('Kigali Central').length).toBeGreaterThan(0);
  });

  it('says a reconciled import added no rows instead of showing an empty table', async () => {
    // Every day already existed, so the commit reconciled them and wrote nothing.
    serveApi([]);
    await importAFile();

    await waitFor(() => expect(screen.getByText('No rows from this import')).toBeInTheDocument());
    expect(
      screen.getByText(/already had a stored value, so the import reconciled them/),
    ).toBeInTheDocument();
  });

  it('reports a failed trace read rather than implying there were no rows', async () => {
    serveApi([]);
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/locations')
        return Promise.resolve({ data: [{ id: 'loc-1', name: 'Kigali Central' }] });
      // A 500 on the trace must not read the same as "this import wrote nothing".
      return Promise.reject(new Error('Failed to read the imported rows'));
    });
    await importAFile();

    await waitFor(() => expect(screen.getByText('Could not read the imported rows')).toBeInTheDocument());
  });

  it('can be dismissed', async () => {
    serveApi([
      { id: 1, location_id: 'loc-1', date: '2026-01-10', demand_qty: 40, upload_id: UPLOAD, upload_filename: 'january.csv' },
    ]);
    await importAFile();
    await screen.findByRole('region', { name: 'Import trace' });

    fireEvent.click(screen.getByRole('button', { name: /Dismiss trace/ }));
    await waitFor(() => expect(screen.queryByText('Import trace')).toBeNull());
  });

  it('does not ask for a trace before anything has been imported', async () => {
    serveApi([]);
    render(<ExcelCsvTab />);
    await screen.findByText('Upload Spreadsheet');

    expect(screen.queryByText('Import trace')).toBeNull();
    expect(
      mockGet.mock.calls.some((c) => c[0] === '/api/v1/ingest/demand'),
    ).toBe(false);
  });
});
