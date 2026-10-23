/**
 * A real logbook keyed into Excel is not a template: decorated headers
 * ("Visitors (adults)"), title lines above the table, totals and "n/a" weeks.
 *
 * Two behaviours are pinned here, both of which used to lose the whole import:
 * the backend's suggested map fills the dropdowns (so a file whose columns are
 * named the way people name them maps without hand-matching four selects), and
 * an unreadable footer row no longer blocks importing the days that are fine.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));

vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));

vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { code: 'server_error', message: 'failed' };
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), postForm: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { ExcelCsvTab } from '../components/ingestion/ExcelCsvTab';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;
const mockPostForm = api.postForm as unknown as ReturnType<typeof vi.fn>;

const UPLOAD = {
  upload_id: 'up-1',
  filename: 'logbook.csv',
  detected_headers: ['Date', 'Visitors (adults)', 'Location', 'Notes'],
  preview: [{ Date: '01/12/2025', 'Visitors (adults)': '1,112', Location: 'Kigali', Notes: '' }],
  row_count: 4,
  status: 'awaiting_mapping',
  suggested_map: {
    date: 'Date',
    demand_qty: 'Visitors (adults)',
    location: 'Location',
    notes: 'Notes',
  },
  header_row: 3,
  preamble_rows: 3,
};

const MAPPING = {
  upload_id: 'up-1',
  mapped_rows: 4,
  error_count: 1,
  errors: [{ row_index: 4, field: 'demand_qty', message: 'Unparseable demand_qty' }],
  skipped_count: 2,
  skipped: [
    { row_index: 3, reason: 'summary_row' },
    { row_index: 4, reason: 'no_date_or_count' },
  ],
  status: 'awaiting_mapping',
};

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockResolvedValue({ data: [{ id: 'loc-1', name: 'Kigali Heights' }] });
  mockPostForm.mockResolvedValue(UPLOAD);
  mockPost.mockResolvedValue(MAPPING);
});

async function uploadFile() {
  render(<ExcelCsvTab />);
  const input = screen.getByLabelText('Spreadsheet file') as HTMLInputElement;
  const file = new File(['Date,Visitors\n01/12/2025,40\n'], 'logbook.csv', { type: 'text/csv' });
  await userEvent.upload(input, file);
  await waitFor(() => expect(mockPostForm).toHaveBeenCalled());
}

describe('messy spreadsheet import', () => {
  it('fills the column mapping from the backend suggestion', async () => {
    await uploadFile();
    await waitFor(() =>
      expect(screen.getByLabelText(/Visitors \/ Demand/)).toHaveValue('Visitors (adults)'),
    );
    expect(screen.getByLabelText(/Date \(Required\)/)).toHaveValue('Date');
    // Title lines above the header are reported, not silently swallowed.
    expect(screen.getByText(/Skipped 3 title lines above the table/i)).toBeInTheDocument();
  });

  it('imports the readable days even when a row cannot be read', async () => {
    const user = userEvent.setup();
    await uploadFile();

    await user.click(screen.getByRole('button', { name: /Validate mapping/i }));
    await waitFor(() => expect(screen.getByText(/Mapped 4 row\(s\), 1 error\(s\)/)).toBeInTheDocument());
    expect(screen.getByText(/2 line\(s\) skipped as not-data/i)).toBeInTheDocument();

    const review = screen.getByRole('button', { name: /Review & Confirm/i });
    expect(review).toBeEnabled();
    await user.click(review);
    expect(screen.getByText(/4 rows will be added/)).toBeInTheDocument();
    expect(screen.getByText(/1 row\(s\) cannot be read and will be left out/i)).toBeInTheDocument();
    expect(screen.getByText(/totals, blank rows and “n\/a” cells/i)).toBeInTheDocument();
  });
});
