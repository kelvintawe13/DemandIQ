import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

// The calibration the detail read carries is the contract under test here:
// coverage figures are percentages (schema NUMERIC(5,2) default 90.00), so the
// panel must render them directly. An extra ×100 used to print "9000%".
vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({ user: { name: 'Ada Admin', role: 'Administrator', roleKey: 'admin', permissions: ['models:read'] } }),
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

const SENSITIVITY = {
  '0.80': { target_coverage: 80.0, empirical_coverage: 78.93, mean_interval_width: 17.66 },
  '0.95': { target_coverage: 95.0, empirical_coverage: 94.82, mean_interval_width: 29.14 },
};

function routeGet(calibration: Record<string, unknown>) {
  return (path: string) => {
    if (path === '/api/v1/models') {
      return Promise.resolve({
        data: [{ id: MODEL_ID, version: 'lightgbm-it30', algorithm: 'lightgbm', status: 'production' }],
      });
    }
    // The detail panel makes one read now: the model row, its conformal
    // calibration and its feature weights arrive together, so the panels cannot
    // land out of step with each other.
    if (path === `/api/v1/models/${MODEL_ID}`) {
      return Promise.resolve({
        id: MODEL_ID,
        version: 'lightgbm-it30',
        algorithm: 'lightgbm',
        status: 'production',
        importance: [{ feature: 'lag_7', importance: 0.22, rank: 1 }],
        importance_method: 'gain',
        calibration,
      });
    }
    return Promise.resolve({ data: [] });
  };
}

describe('Model Registry calibration panel', () => {
  beforeEach(() => mockGet.mockReset());

  it('renders coverage as percentages, never re-scaled', async () => {
    mockGet.mockImplementation(
      routeGet({
        model_version_id: MODEL_ID,
        target_coverage: 90.0,
        empirical_coverage: 89.4,
        mean_interval_width: 12.0,
      }),
    );

    render(<ModelRegistry />);

    await waitFor(() => expect(screen.getByText('90%')).toBeInTheDocument());
    expect(screen.getByText('89.4%')).toBeInTheDocument();
    // The regression being pinned: an extra ×100 printed these.
    expect(screen.queryByText('9000%')).toBeNull();
    expect(screen.queryByText('8940.0%')).toBeNull();
  });

  it('lists the other interval widths tried, in plain words', async () => {
    mockGet.mockImplementation(
      routeGet({
        model_version_id: MODEL_ID,
        target_coverage: 90.0,
        empirical_coverage: 89.4,
        mean_interval_width: 12.0,
        coverage_sensitivity: SENSITIVITY,
      }),
    );

    render(<ModelRegistry />);

    await waitFor(() => expect(screen.getByText('Other interval widths tried')).toBeInTheDocument());
    expect(screen.getByText('Asked for 80%')).toBeInTheDocument();
    expect(screen.getByText('Asked for 95%')).toBeInTheDocument();
    expect(screen.getByText(/got 78\.9% · 17\.66 wide/)).toBeInTheDocument();
    expect(screen.getByText(/got 94\.8% · 29\.14 wide/)).toBeInTheDocument();
  });

  it('omits the block entirely when the run did not measure it', async () => {
    mockGet.mockImplementation(
      routeGet({
        model_version_id: MODEL_ID,
        target_coverage: 90.0,
        empirical_coverage: 89.4,
        mean_interval_width: 12.0,
      }),
    );

    render(<ModelRegistry />);

    await waitFor(() => expect(screen.getByText('89.4%')).toBeInTheDocument());
    // Absent means "not measured", not "measured as zero".
    expect(screen.queryByText('Other interval widths tried')).toBeNull();
  });
});
