/**
 * The photo review screen has to say what `confidence` means for the engine that
 * produced it.
 *
 * The two engines put different things in that field. A remote vision service
 * returns a real model score, so a percentage is honest. The local parser's value
 * counts how many numbers survived on the line after the date was stripped — 0.9
 * means the demand count was the only candidate, 0.75 means several were and the
 * last one was taken for the operator. Rendering the second as "75% confident"
 * would dress a readability heuristic up as a probability, so these tests pin the
 * wording to the engine.
 *
 * The feature exists because the value was already being returned and thrown
 * away: `ExtractedRow` declared `confidence` and nothing ever read it, so a row
 * whose figure was guessed was indistinguishable from a clean one until after
 * the commit.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), postForm: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  ApiException: class ApiException extends Error {},
}));

import { api } from '../api/client';
import { PreviewPanel } from '../components/PreviewPanel';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

function serveJob(engine: string, rows: Array<Record<string, unknown>>) {
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
        engine,
        note: 'OCR extraction complete. Review every row before committing.',
      });
    return Promise.resolve({ data: [] });
  });
}

beforeEach(() => {
  mockGet.mockReset();
});

describe('OCR row scores', () => {
  it('words the local parser by line legibility, never as a percentage', async () => {
    serveJob('tesseract', [
      { row_index: 0, date: '2026-01-12', demand_qty: 112, confidence: 0.9 },
      { row_index: 1, date: '2026-01-13', demand_qty: 87, confidence: 0.75 },
    ]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await waitFor(() => expect(screen.getByText('One number on the line')).toBeInTheDocument());
    expect(screen.getByText('Several numbers on the line — check')).toBeInTheDocument();
    // The heuristic must not be shown as a confidence figure.
    expect(screen.queryByText(/90%|75%/)).toBeNull();
  });

  it('reports a remote vision score as a percentage', async () => {
    serveJob('remote', [
      { row_index: 0, date: '2026-01-12', demand_qty: 112, confidence: 0.98 },
      { row_index: 1, date: '2026-01-13', demand_qty: 87, confidence: 0.72 },
    ]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await waitFor(() => expect(screen.getByText('98% model score')).toBeInTheDocument());
    expect(screen.getByText('72% model score — check')).toBeInTheDocument();
  });

  it('summarises the rows worth a second look', async () => {
    serveJob('tesseract', [
      { row_index: 0, date: '2026-01-12', demand_qty: 112, confidence: 0.9 },
      { row_index: 1, date: '2026-01-13', demand_qty: 87, confidence: 0.75 },
      { row_index: 2, date: '2026-01-14', demand_qty: 90, confidence: 0.75 },
    ]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await waitFor(() =>
      expect(screen.getByRole('note')).toHaveTextContent('2 of 3 rows to check'),
    );
  });

  it('stays quiet when every row was unambiguous', async () => {
    serveJob('tesseract', [
      { row_index: 0, date: '2026-01-12', demand_qty: 112, confidence: 0.9 },
    ]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await screen.findByText('One number on the line');
    // No warning band for a clean read — a blanket caveat would train the
    // operator to ignore it.
    expect(screen.queryByRole('note')).toBeNull();
  });

  it('names the engine that read the photo', async () => {
    serveJob('tesseract', [
      { row_index: 0, date: '2026-01-12', demand_qty: 112, confidence: 0.9 },
    ]);
    const { unmount } = render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);
    await waitFor(() => expect(screen.getByText(/Read by the local OCR engine/)).toBeInTheDocument());
    unmount();

    serveJob('remote', [
      { row_index: 0, date: '2026-01-12', demand_qty: 112, confidence: 0.9 },
    ]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);
    await waitFor(() => expect(screen.getByText(/Read by the OCR service/)).toBeInTheDocument());
  });

  it('does not invent a score the engine did not report', async () => {
    serveJob('tesseract', [{ row_index: 0, date: '2026-01-12', demand_qty: 112 }]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await waitFor(() => expect(screen.getByText('No score reported')).toBeInTheDocument());
  });

  it('marks a row the operator typed as added by hand, not scored', async () => {
    serveJob('tesseract', [
      { row_index: 0, date: '2026-01-12', demand_qty: 112, confidence: 0.9 },
    ]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await waitFor(() => expect(screen.getByRole('button', { name: /\+ Add row/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /\+ Add row/ }));

    await waitFor(() => expect(screen.getByText('Added by hand')).toBeInTheDocument());
    // A blank hand-typed row is not an ambiguous line and must not be flagged as one.
    expect(screen.queryByText('Several numbers on the line — check')).toBeNull();
  });

  it('keeps the score through an edit of the same row', async () => {
    serveJob('tesseract', [
      { row_index: 0, date: '2026-01-12', demand_qty: 112, confidence: 0.75 },
    ]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);
    await waitFor(() => expect(screen.getByLabelText('Visitors for row 1')).toBeInTheDocument());

    // Correcting the quantity does not re-read the photo, so the engine's doubt
    // about which number it picked still stands until the operator resolves it.
    fireEvent.change(screen.getByLabelText('Visitors for row 1'), { target: { value: '120' } });
    await waitFor(() =>
      expect(screen.getByText('Several numbers on the line — check')).toBeInTheDocument(),
    );
  });
});

/**
 * The local engine reads a page several ways and merges the results, so a row's
 * figure can be a consensus or a majority. That is a different claim from the
 * per-row `confidence` pill — which describes one read's line — and the screen
 * has to say so, and put the doubtful rows where the operator meets them first.
 */
describe('OCR readings', () => {
  const contested = { row_index: 0, date: '2026-10-15', demand_qty: 24, confidence: 0.9, readers: 3, agreement: 0.67 };
  const unanimous = { row_index: 1, date: '2026-10-02', demand_qty: 28, confidence: 0.9, readers: 3, agreement: 1 };
  const singleRead = { row_index: 2, date: '2026-10-07', demand_qty: 38, confidence: 0.9, readers: 1, agreement: 1 };

  it('names the split and lists the disputed rows first', async () => {
    // The disputed row is served last, so its position at the top can only come
    // from the panel's ordering.
    serveJob('tesseract', [unanimous, singleRead, contested]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await waitFor(() => expect(screen.getByText('2 of 3 readings agree')).toBeInTheDocument());

    const dateFields = screen.getAllByLabelText(/Date for row \d+/);
    expect(dateFields.map((field) => (field as HTMLInputElement).value)).toEqual([
      '2026-10-15',
      '2026-10-02',
      '2026-10-07',
    ]);

    // A row only one read produced is not a dispute, and must not be labelled as
    // one — a badge on every row trains the operator to ignore badges.
    expect(screen.getAllByText(/readings agree/)).toHaveLength(1);
  });

  it('summarises the disputed rows without claiming they are certain', async () => {
    serveJob('tesseract', [contested, unanimous]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await waitFor(() =>
      expect(screen.getByText('1 row the readings disagreed on')).toBeInTheDocument(),
    );
    expect(screen.getByText(/majority reading rather than a unanimous one/)).toBeInTheDocument();
  });

  it('stays quiet for a row whose readings all agreed', async () => {
    serveJob('tesseract', [unanimous]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await screen.findByText('One number on the line');
    expect(screen.queryByText(/readings agree/)).toBeNull();
    expect(screen.queryByText(/readings disagreed/)).toBeNull();
  });

  it('leaves a vision model score to speak for itself', async () => {
    // A remote service returns one read of the page: there is no merge to report,
    // so no agreement is invented for it.
    serveJob('remote', [
      { row_index: 0, date: '2026-10-15', demand_qty: 24, confidence: 0.93 },
    ]);
    render(<PreviewPanel imageName="log.jpg" jobId="job-1" />);

    await waitFor(() => expect(screen.getByText('93% model score')).toBeInTheDocument());
    expect(screen.queryByText(/readings agree/)).toBeNull();
  });
});
