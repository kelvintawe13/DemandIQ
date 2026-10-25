/**
 * The image review screen has two jobs beyond showing the rows: put each row back
 * on the photo it was read from, and say when two rows claim the same day.
 *
 * Both exist because a wrong or duplicated demand record is worse than a missing
 * one — nothing downstream can tell it apart from a real trading day. The commit
 * upserts on (location, date), so a repeated date silently replaces a row the
 * operator was asked to check, and a row with no highlight leaves them comparing
 * numbers against a photo by eye.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../api/client', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    postForm: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    blob: vi.fn(),
  },
  ApiException: class ApiException extends Error {},
}));

import { api } from '../api/client';
import { PreviewPanel } from '../components/PreviewPanel';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockBlob = api.blob as unknown as ReturnType<typeof vi.fn>;

function serveJob(rows: Array<Record<string, unknown>>) {
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/locations')
      return Promise.resolve({ data: [{ id: 'loc-1', name: 'Kigali Central' }] });
    if (path.startsWith('/api/v1/ingest/image/'))
      return Promise.resolve({
        job_id: 'job-1',
        status: 'ready',
        filename: 'log.jpg',
        headers: ['date', 'demand_qty'],
        row_count: rows.length,
        extracted_rows: rows,
        engine: 'tesseract',
        note: 'OCR extraction complete. Review every row before committing.',
      });
    return Promise.resolve({ data: [] });
  });
}

beforeEach(() => {
  mockGet.mockReset();
  mockBlob.mockReset();
});

describe('duplicate dates in the preview', () => {
  it('says which date was read twice and marks both rows', async () => {
    serveJob([
      { row_index: 0, date: '2026-01-12', demand_qty: 112, confidence: 0.9 },
      { row_index: 1, date: '2026-01-12', demand_qty: 98, confidence: 0.9 },
      { row_index: 2, date: '2026-01-13', demand_qty: 87, confidence: 0.9 },
    ]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    // Only one row per day can be imported, so the operator has to settle it now.
    await waitFor(() => expect(screen.getByText(/1 date repeated/)).toBeInTheDocument());
    expect(screen.getByRole('note')).toHaveTextContent('2026-01-12 (112 / 98)');
    expect(screen.getAllByText('Same date twice')).toHaveLength(2);
  });

  it('stays quiet when every date appears once', async () => {
    serveJob([
      { row_index: 0, date: '2026-01-12', demand_qty: 112, confidence: 0.9 },
      { row_index: 1, date: '2026-01-13', demand_qty: 87, confidence: 0.9 },
    ]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    expect(await screen.findAllByText('One number on the line')).toHaveLength(2);
    expect(screen.queryByText(/date repeated/)).toBeNull();
    expect(screen.queryByText('Same date twice')).toBeNull();
  });
});

describe('highlights on the stored photo', () => {
  it('draws each row where it was read and releases the blob on unmount', async () => {
    const createObjectURL = vi.fn(() => 'blob:log-photo');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true });
    mockBlob.mockResolvedValue(new Blob(['jpeg'], { type: 'image/jpeg' }));

    serveJob([
      {
        row_index: 0,
        date: '2026-01-12',
        demand_qty: 112,
        confidence: 0.9,
        boxes: { date: [0.1, 0.2, 0.3, 0.05], demand_qty: [0.6, 0.2, 0.1, 0.05] },
      },
    ]);
    const { unmount } = render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    // The authenticated endpoint is fetched with the session token, not linked.
    await waitFor(() =>
      expect(mockBlob).toHaveBeenCalledWith('/api/v1/ingest/uploads/job-1/source'),
    );
    const photo = await screen.findByRole('img', { name: 'Photo of log.jpg' });
    expect(photo).toHaveAttribute('src', 'blob:log-photo');

    const boxes = document.querySelectorAll('[data-ocr-box]');
    expect(boxes).toHaveLength(2);
    expect(boxes[0]).toHaveStyle({ left: '10%', top: '20%', width: '30%', height: '5%' });
    expect(boxes[1]).toHaveStyle({ left: '60%', top: '20%', width: '10%', height: '5%' });

    unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:log-photo');
  });

  it('still lists the rows when the photo cannot be fetched', async () => {
    // An upload from before originals were kept, or a failed fetch: the review
    // still works, it just has nothing to draw the highlights on.
    mockBlob.mockRejectedValue(new Error('404'));
    serveJob([{ row_index: 0, date: '2026-01-12', demand_qty: 112, confidence: 0.9 }]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await screen.findByText('One number on the line');
    expect(screen.queryByRole('img', { name: 'Photo of log.jpg' })).toBeNull();
    expect(document.querySelectorAll('[data-ocr-box]')).toHaveLength(0);
  });

  it('ignores a box the engine did not report', async () => {
    mockBlob.mockResolvedValue(new Blob(['jpeg'], { type: 'image/jpeg' }));
    Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:photo'), writable: true });
    serveJob([
      {
        row_index: 0,
        date: '2026-01-12',
        demand_qty: 112,
        confidence: 0.9,
        boxes: { date: [0.1, 0.2, 0.3, 0.05], demand_qty: null },
      },
    ]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await screen.findByRole('img', { name: 'Photo of log.jpg' });
    // Only the date was located; the count was not, and a highlight is not
    // invented at the origin to fill the gap.
    expect(document.querySelectorAll('[data-ocr-box="date"]')).toHaveLength(1);
    expect(document.querySelectorAll('[data-ocr-box="demand_qty"]')).toHaveLength(0);
  });
});
