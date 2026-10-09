import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

/**
 * The Model Registry's "Serving now" panel is where an operator finds out what
 * the API is answering with. It distinguished stub from bundle and had nothing
 * to say about *how* the bundle got there, or that a configured one had stopped
 * loading — a state the API keeps reporting after boot, because the startup
 * assertion only runs at start. Both now come from `configuration`, the same
 * block `GET /api/v1/health` publishes.
 */
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

function routeGet(serving: Record<string, unknown>) {
  return (path: string) => {
    if (path === '/api/v1/models')
      return Promise.resolve({
        data: [{ id: MODEL_ID, version: 'lightgbm-it30', algorithm: 'lightgbm', status: 'production' }],
      });
    if (path === `/api/v1/models/${MODEL_ID}`)
      return Promise.resolve({ id: MODEL_ID, version: 'lightgbm-it30', algorithm: 'lightgbm', status: 'production' });
    if (path === '/api/v1/models/serving') return Promise.resolve(serving);
    return Promise.resolve({ data: [] });
  };
}

const TRAINED = {
  mode: 'bundle',
  summary: 'trained bundle loaded — algorithm=lightgbm version=lightgbm-it30 feature_set=fs-2026.09.2',
  agreement: 'in_sync',
  note: 'The loaded export is the row marked production (lightgbm-it30).',
  bundle: {
    path: '/model/artifacts/runs/latest/serving_bundle.joblib',
    changed_at: null,
    exported_at: null,
    algorithm: 'lightgbm',
    version: 'lightgbm-it30',
    feature_set_version: 'fs-2026.09.2',
  },
  production: {
    model_version_id: MODEL_ID,
    algorithm: 'lightgbm',
    version: 'lightgbm-it30',
    feature_set_version: 'fs-2026.09.2',
    promoted_at: null,
  },
  configuration: {
    mode: 'trained',
    configured: true,
    path: '/model/artifacts/runs/latest/serving_bundle.joblib',
    reason: null,
  },
};

describe('Model Registry serving mode', () => {
  beforeEach(() => mockGet.mockReset());

  it('says when the export was named explicitly rather than discovered', async () => {
    mockGet.mockImplementation(routeGet(TRAINED));

    render(<ModelRegistry />);

    // `waitFor` re-queries rather than holding the first match: the page re-reads
    // once when the first row selects itself, so a match can be found in the
    // panel render and then be detached by the loading flip that follows it.
    await waitFor(() =>
      expect(
        screen.getByText('Serving the export named by MODEL_BUNDLE_PATH'),
      ).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByText('Trained model, in sync with the registry')).toBeInTheDocument(),
    );
  });

  it('flags a configured export that stopped loading, and says why', async () => {
    // The state the startup assertion cannot report: this process came up with a
    // working bundle and lost it, so the console still renders while the API
    // answers from the stub. It has to read as broken, not as the ordinary stub.
    mockGet.mockImplementation(
      routeGet({
        mode: 'stub',
        summary:
          'STUB forecasts (rolling means): MODEL_BUNDLE_PATH=/model/gone.joblib could not be loaded',
        agreement: 'stub',
        note: 'No serving export is loaded, so every forecast is a rolling-mean average rather than a model prediction.',
        bundle: null,
        production: {
          model_version_id: MODEL_ID,
          algorithm: 'lightgbm',
          version: 'lightgbm-it30',
          feature_set_version: 'fs-2026.09.2',
          promoted_at: null,
        },
        configuration: {
          mode: 'error',
          configured: true,
          path: '/model/gone.joblib',
          reason:
            'MODEL_BUNDLE_PATH=/model/gone.joblib is not a loadable serving bundle. Unset it to run the stub deliberately, or point it at an exported serving_bundle.joblib.',
        },
      }),
    );

    render(<ModelRegistry />);

    await waitFor(() =>
      expect(
        screen.getByText('Misconfigured: the export MODEL_BUNDLE_PATH names is not loadable'),
      ).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByText('The configured export no longer loads')).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByText(/is not a loadable serving bundle/)).toBeInTheDocument(),
    );
  });
});
