/**
 * The admin Connectors page can preview a pull.
 *
 * `POST /connectors/{id}/pull?dry_run=true` fetches and maps the provider's rows
 * without committing any of them. The page must therefore show *what a pull
 * would read* and never imply it has imported anything — the same rule the POS /
 * MoMo tab keeps when it says a sync is queued rather than finished.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

const toast = vi.fn();
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));
vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 409;
    error = { message: 'This connector has no pull endpoint.' };
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api, ApiException } from '../api/client';
import { Connectors } from '../pages/admin/Connectors';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;
// The mocked class takes no constructor arguments; the real module's type does.
const ApiError = ApiException as unknown as new () => Error & { error: { message: string } };

const CONNECTOR = {
  id: 'c1',
  name: 'Kimironko Till',
  type: 'pos',
  status: 'active',
  health: 'healthy',
  last_sync_at: null,
  credentials: { detail: '' },
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  toast.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/connectors') return Promise.resolve({ data: [CONNECTOR] });
    if (path === '/api/v1/api-keys') return Promise.resolve({ data: [] });
    if (String(path).endsWith('/mapping')) return Promise.resolve({ mappings: [] });
    return Promise.resolve({ data: [] });
  });
});

describe('Connectors — pull preview', () => {
  it('previews a dry-run pull and shows the rows it would commit', async () => {
    mockPost.mockResolvedValue({
      connector_id: 'c1',
      dry_run: true,
      status: 'preview',
      pulled: 2,
      committed: 2,
      skipped: 0,
      rows: [
        { location_id: 'l1', date: '2026-09-28', demand_qty: 51, notes: null },
        { location_id: 'l1', date: '2026-09-27', demand_qty: 47, notes: 'dinner' },
      ],
      errors: [],
      detail: 'preview: 2 of 2 row(s) ready to commit',
      run_id: null,
    });
    render(<Connectors />);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Preview pull Kimironko Till' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Preview pull Kimironko Till' }));

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/api/v1/connectors/c1/pull?dry_run=true'),
    );
    await waitFor(() => expect(screen.getByText('2 of 2 ready to commit')).toBeInTheDocument());
    expect(screen.getByText('2026-09-28')).toBeInTheDocument();
    expect(screen.getByText('51')).toBeInTheDocument();
    // The copy says preview, not imported.
    expect(screen.getByText('preview: 2 of 2 row(s) ready to commit')).toBeInTheDocument();
  });

  it('surfaces a refusal instead of an empty preview', async () => {
    mockPost.mockRejectedValue(new ApiError());
    render(<Connectors />);

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Preview pull Kimironko Till' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Preview pull Kimironko Till' }));

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('This connector has no pull endpoint.', 'error'),
    );
    // No panel is opened for a pull that never ran.
    expect(screen.queryByText(/ready to commit/)).toBeNull();
  });
});
