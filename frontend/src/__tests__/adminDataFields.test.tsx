/**
 * Two administration screens read fields their endpoints really send.
 *
 * Both had drifted into reading keys the API never returns, with a literal
 * fallback baked into the page — which is worse than a blank, because the page
 * kept stating a number:
 *
 * * the location registry read `history_days`/`forecastable` off a list payload
 *   that did not carry them, so the History column was permanently "—", every
 *   location claimed to be forecasting, and the "Show only locations that cannot
 *   be forecast" filter could only ever empty the table;
 * * the datasets panel read `waste.observations`/`waste.mean_waste` where the
 *   summary nests them under `descriptive_stats`, so the URBAN-WASTE figures on
 *   screen were fixture numbers compiled into the component.
 *
 * These tests render the pages against the payloads the routers produce.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';

// A stable spy: two of these screens only report the API's answer through a
// toast, which a per-call `vi.fn()` could not be asserted against.
const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));
vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  ApiException: class ApiException extends Error {},
}));

import { api } from '../api/client';
import { LocationRegistry } from '../pages/admin/LocationRegistry';
import { DatasetsAdmin } from '../pages/admin/DatasetsAdmin';
import { Connectors } from '../pages/admin/Connectors';
import { ModelRegistry } from '../pages/admin/ModelRegistry';
import { FeatureStore } from '../pages/admin/FeatureStore';
import { AiGovernance } from '../pages/admin/AiGovernance';
import { Webhooks } from '../pages/admin/Webhooks';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;
const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;
const mockPatch = api.patch as unknown as ReturnType<typeof vi.fn>;

/** Rows shaped like `PaginatedResponse[LocationListResponse]`. */
const FORECASTABLE = {
  id: 'loc-ready',
  name: 'Kigali Heights',
  area: 'Kigali',
  cuisine: 'Rwandan',
  district: 'Gasabo',
  seating_capacity: 80,
  service_type: 'buffet',
  archived_at: null,
  created_at: '2026-01-02T09:00:00Z',
  history_days: 12,
  gap_days: 2,
  forecastable: true,
};

const TOO_YOUNG = {
  ...FORECASTABLE,
  id: 'loc-young',
  name: 'Kimironko Kiosk',
  seating_capacity: 20,
  created_at: '2026-08-20T09:00:00Z',
  history_days: 3,
  gap_days: 0,
  forecastable: false,
};

/** The dataset entry and waste summary the admin routers return. */
const DATASETS = [
  {
    id: 'recruit',
    name: 'Recruit Restaurant Visitor Forecasting',
    table: 'demand_observations',
    source_dataset: 'recruit-restaurant-visitor-forecasting',
    licence: 'Kaggle competition conditions',
    access_conditions: 'Kaggle competition · academic/research use',
    records: 252108,
    coverage: '829 restaurants · 85% avg daily coverage',
    period: '1 Jan 2016 – 22 Apr 2017',
    status: 'validated',
    notes: 'No missing values, no duplicate restaurant-date pairs.',
  },
];

const WASTE_SUMMARY = {
  descriptive_stats: {
    total_records: 3321,
    non_missing_waste: 3160,
    missing_waste: 161,
    establishments: 34,
    countries: 6,
    ordered_portions_available: 9,
    waste_kg: { count: 3160, mean: 37.803, median: 21.4, std: 45.19, min: 0, max: 425.5 },
    // The per-kitchen spread. The API bounds this list, so both halves matter:
    // the rows it returned, and the count in the dataset they came from.
    establishment_variation: [
      { establishment_code: 'Tenerife / El Tope', count: 231, mean: 125.83, std: 47.62, min: 0.4, max: 410.1 },
      { establishment_code: 'Portugal / Onj São Lázaro', count: 127, mean: 2.88, std: 3.36, min: 0.1, max: 21.3 },
    ],
    date_range: { start: '2018-03-02', end: '2018-11-21' },
  },
  stats_source: 'audited_dataset_fallback',
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPatch.mockReset();
  toast.mockReset();
});

describe('location registry', () => {
  beforeEach(() => {
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/locations'
        ? Promise.resolve({ data: [FORECASTABLE, TOO_YOUNG], has_more: false, next_cursor: null })
        : Promise.resolve({ data: [] }),
    );
  });

  it('prints the history and forecastability the list reports', async () => {
    render(<LocationRegistry />);

    await waitFor(() => expect(screen.getByText('12d')).toBeInTheDocument());
    expect(screen.getByText('2 gaps')).toBeInTheDocument();
    expect(screen.getByText('3d')).toBeInTheDocument();
    // 7 days is the onboarding bar, so a 3-day location is 4 days short.
    expect(screen.getByText('4d to go')).toBeInTheDocument();

    // The status column read "forecasting" for every row, including this one.
    expect(screen.getByText('needs history')).toBeInTheDocument();
    expect(screen.getByText('forecasting')).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 locations forecastable/)).toBeInTheDocument();
  });

  it('filters to the locations that cannot be forecast yet', async () => {
    const user = userEvent.setup();
    render(<LocationRegistry />);

    await waitFor(() =>
      expect(
        screen.getByLabelText(/Show only locations that cannot be forecast/),
      ).toBeInTheDocument(),
    );
    await user.click(screen.getByLabelText(/Show only locations that cannot be forecast/));

    await waitFor(() => {
      expect(screen.getByText('Kimironko Kiosk')).toBeInTheDocument();
      expect(screen.queryByText('Kigali Heights')).toBeNull();
    });
  });

  it('summarises the same list it lists, in KPI tiles', async () => {
    render(<LocationRegistry />);

    // "Locations" is both the panel heading (`h2`) and its KPI tile (`p`).
    const tile = async (label: string) => {
      const el = (await screen.findAllByText(label)).find((node) => node.tagName === 'P');
      return el?.closest('div.flex.min-w-0.flex-col')?.textContent ?? '';
    };

    // 80 + 20 seats across two sites; the counts are the list's own, so the
    // strip cannot disagree with the table under it.
    expect(await tile('Seats')).toContain('100');
    expect(await tile('Seats')).toContain('across 2 sites');
    expect(await tile('Locations')).toContain('2');
    expect(await tile('Locations')).toContain('1 waiting on history');
    expect(await tile('Menu groups')).toContain('No items mapped');
    expect(await tile('Forecasting')).toContain('1');
  });

  it('says a site was not scored rather than calling it forecastable', async () => {
    // `GET /locations` answers `forecastable: null` and `history_days: null` for
    // a site the pipeline has not scored. Defaulting that to *true* made the row
    // read "forecasting" and the panel "All set.", neither of which the API said.
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/locations'
        ? Promise.resolve({
            data: [{ ...FORECASTABLE, id: 'loc-new', name: 'Unscored Site', history_days: null, gap_days: null, forecastable: null }],
            has_more: false,
            next_cursor: null,
          })
        : Promise.resolve({ data: [] }),
    );
    render(<LocationRegistry />);

    await waitFor(() => expect(screen.getByText('Unscored Site')).toBeInTheDocument());
    expect(screen.getByText('not scored')).toBeInTheDocument();
    expect(screen.queryByText('All set.')).toBeNull();
    expect(screen.getByText(/1 not scored yet\./)).toBeInTheDocument();
    // And no history bar: an empty bar is "zero days", not "unknown".
    expect(screen.queryByRole('img')).toBeNull();
  });

  it('leaves for the add route instead of opening a dialog', async () => {
    // Adding a location is a page (`/admin-locations-new`), not a modal: the
    // form is long enough to want its own address, so the registry hands off.
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<LocationRegistry onNavigate={onNavigate} />);

    await user.click(await screen.findByRole('button', { name: /Add location/i }));

    expect(onNavigate).toHaveBeenCalledWith('admin-locations-new');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('opens the edit form in sections, wired as one form', async () => {
    // The dialog that stayed shares `LocationFields` with the add route, so the
    // two cannot ask for different fields — this pins that they still do.
    const user = userEvent.setup();
    mockPatch.mockResolvedValue({});
    render(<LocationRegistry />);

    await user.click(await screen.findByLabelText('Edit Kigali Heights'));

    for (const section of ['Identity', 'Trading', 'Contact', 'Notes']) {
      expect(screen.getByRole('heading', { name: section })).toBeInTheDocument();
    }

    const dialog = screen.getByRole('dialog');
    const form = dialog.querySelector('form');
    expect(form).not.toBeNull();
    // Built on `FormModal`, so the footer button points at the `<form>` — which
    // is what makes Enter submit from any field.
    expect(within(dialog).getByRole('button', { name: 'Save changes' })).toHaveAttribute('form', form!.id);

    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith(
        '/api/v1/locations/loc-ready',
        expect.objectContaining({ name: 'Kigali Heights' }),
      ),
    );
  });

  it('fixes a manager or a phone straight in the row, without a dialog', async () => {
    const user = userEvent.setup();
    mockPatch.mockResolvedValue({});
    render(<LocationRegistry />);

    // Neither field is set on these rows, so the row offers the placeholder as
    // the control rather than a dash nobody can click.
    await waitFor(() => expect(screen.getByLabelText('Edit manager for Kigali Heights')).toBeInTheDocument());
    const row = screen.getByLabelText('Edit manager for Kigali Heights').closest('tr');
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText('Add manager')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('Add phone')).toBeInTheDocument();

    await user.click(within(row as HTMLElement).getByLabelText('Edit manager for Kigali Heights'));
    await user.type(within(row as HTMLElement).getByLabelText('manager for Kigali Heights'), 'Aline Umutoni');
    await user.click(within(row as HTMLElement).getByLabelText('Save manager for Kigali Heights'));

    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith('/api/v1/locations/loc-ready', {
        manager_name: 'Aline Umutoni',
      }),
    );
    // One field, one PATCH: the rest of the record is not resubmitted.
    expect(mockPatch).toHaveBeenCalledTimes(1);
    expect(mockPatch.mock.calls[0][1]).toEqual({ manager_name: 'Aline Umutoni' });
    await waitFor(() => expect(screen.getByText('Aline Umutoni')).toBeInTheDocument());
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('clears a field with an empty string, not a null the API ignores', async () => {
    // `PATCH /locations` applies a field only when it is not `None`, so sending
    // `null` to clear a phone would leave the old number in the database while
    // the list showed it gone.
    const user = userEvent.setup();
    mockPatch.mockResolvedValue({});
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/locations'
        ? Promise.resolve({
            data: [{ ...FORECASTABLE, phone: '+250 788 111 222' }],
            has_more: false,
            next_cursor: null,
          })
        : Promise.resolve({ data: [] }),
    );
    render(<LocationRegistry />);

    // The label carries the value it is about to replace, which is how a screen
    // reader user knows which number the pencil will overwrite.
    await waitFor(() => expect(screen.getByLabelText(/^Edit phone for Kigali Heights — currently/)).toBeInTheDocument());
    const row = screen.getByLabelText(/^Edit phone for Kigali Heights — currently/).closest('tr') as HTMLElement;
    await user.click(within(row).getByLabelText(/^Edit phone for Kigali Heights/));
    await user.clear(within(row).getByLabelText('phone for Kigali Heights'));
    await user.click(within(row).getByLabelText('Save phone for Kigali Heights'));

    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith('/api/v1/locations/loc-ready', { phone: '' }));
    await waitFor(() => expect(screen.getByText('Add phone')).toBeInTheDocument());
  });

  it('shows what the server stored, not what was typed, when the two differ', async () => {
    // The write is accepted but the read model comes back with the *old* value
    // (a field the API chose not to apply). The row must not claim the edit.
    const user = userEvent.setup();
    mockPatch.mockResolvedValue({ ...FORECASTABLE, manager_name: null });
    render(<LocationRegistry />);

    await waitFor(() => expect(screen.getByLabelText('Edit manager for Kigali Heights')).toBeInTheDocument());
    const row = screen.getByLabelText('Edit manager for Kigali Heights').closest('tr') as HTMLElement;
    await user.click(within(row).getByLabelText('Edit manager for Kigali Heights'));
    await user.type(within(row).getByLabelText('manager for Kigali Heights'), 'Aline Umutoni');
    await user.click(within(row).getByLabelText('Save manager for Kigali Heights'));

    // It went back to the placeholder, because that is what is stored.
    await waitFor(() => expect(within(row).getByText('Add manager')).toBeInTheDocument());
    expect(within(row).queryByText('Aline Umutoni')).toBeNull();
  });

  it('puts the old value back when the inline write is refused', async () => {
    const user = userEvent.setup();
    mockPatch.mockRejectedValue(new Error('403'));
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/locations'
        ? Promise.resolve({
            data: [{ ...FORECASTABLE, manager_name: 'Aline Umutoni' }],
            has_more: false,
            next_cursor: null,
          })
        : Promise.resolve({ data: [] }),
    );
    render(<LocationRegistry />);

    await waitFor(() => expect(screen.getByLabelText(/^Edit manager for Kigali Heights/)).toBeInTheDocument());
    const row = screen.getByLabelText(/^Edit manager for Kigali Heights/).closest('tr') as HTMLElement;
    await user.click(within(row).getByLabelText(/^Edit manager for Kigali Heights/));
    await user.clear(within(row).getByLabelText('manager for Kigali Heights'));
    await user.type(within(row).getByLabelText('manager for Kigali Heights'), 'Someone Else');
    await user.click(within(row).getByLabelText('Save manager for Kigali Heights'));

    // The control never keeps a value the server did not take.
    await waitFor(() => expect(screen.getByText('Aline Umutoni')).toBeInTheDocument());
    expect(screen.queryByText('Someone Else')).toBeNull();
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Failed to update', 'error'));
  });
});

describe('datasets and waste summary', () => {
  it('states the waste figures the API returns, never fixture literals', async () => {
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/datasets'
        ? Promise.resolve({ data: DATASETS })
        : path === '/api/v1/waste/summary'
          ? Promise.resolve(WASTE_SUMMARY)
          : Promise.resolve({ data: [] }),
    );
    render(<DatasetsAdmin />);

    // Provenance used to render as an empty line: neither `role` nor
    // `description` exists on a dataset.
    await waitFor(() => expect(screen.getByText('Stored in demand_observations')).toBeInTheDocument());

    await waitFor(() => expect(screen.getByText('3321')).toBeInTheDocument());
    expect(screen.getByText('3160')).toBeInTheDocument();
    expect(screen.getByText('34 across 6 countries')).toBeInTheDocument();
    expect(screen.getByText('37.803 kg per record')).toBeInTheDocument();
    expect(screen.getByText('21.4 kg')).toBeInTheDocument();
    expect(screen.getByText('9 records')).toBeInTheDocument();
    expect(screen.getByText(/audited URBAN-WASTE fixture/)).toBeInTheDocument();

    // A single mean hides that these kitchens are nothing alike, so the panel
    // states the spread behind it — and says how much of the dataset the list
    // it was given covers, rather than presenting the returned rows as all 34.
    expect(screen.getByText('Spread by establishment')).toBeInTheDocument();
    expect(screen.getByText('Tenerife / El Tope')).toBeInTheDocument();
    expect(screen.getByText(/125\.83 kg avg ± 47\.62/)).toBeInTheDocument();
    expect(screen.getByText(/0\.4–410\.1 kg/)).toBeInTheDocument();
    expect(screen.getByText(/2 establishments reported · 34 in the dataset/)).toBeInTheDocument();

    // The numbers the panel used to print when it could not find its keys.
    expect(screen.queryByText('3,160 weights')).toBeNull();
    expect(screen.queryByText(/4\.8 kg per record/)).toBeNull();
    expect(screen.queryByText(/2\.9 kg/)).toBeNull();
    expect(screen.queryByText(/9 records only/)).toBeNull();
  });

  it('says the summary is unavailable rather than showing invented figures', async () => {
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/datasets'
        ? Promise.resolve({ data: DATASETS })
        : Promise.reject(new Error('service unavailable')),
    );
    render(<DatasetsAdmin />);

    await waitFor(() => expect(screen.getByText('Waste summary unavailable')).toBeInTheDocument());
    expect(screen.queryByText(/4\.8 kg per record/)).toBeNull();
    expect(screen.queryByText(/3,160 weights/)).toBeNull();
  });
});

describe('feature coverage', () => {
  it('leaves coverage unstated when the API does not report it', async () => {
    // A missing figure was defaulted to 0, so a feature the API said nothing
    // about was drawn and labelled "0%" — and the reservation warning then
    // asserted "~45%" from a literal in this file.
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/features'
        ? Promise.resolve({
            data: [
              { id: 'f1', name: 'lag_7', feature_group: 'Historical demand', dtype: 'float', description: 'Demand 7 days before target', leakage_safe: true, in_production: true },
              { id: 'f2', name: 'reservation_count', feature_group: 'Reservations', dtype: 'int', description: 'Reservation count for target date', leakage_safe: true, in_production: true },
            ],
          })
        : Promise.resolve({ data: [] }),
    );
    render(<FeatureStore />);

    await waitFor(() => expect(screen.getByText('lag_7')).toBeInTheDocument());
    expect(screen.queryByText('0%')).toBeNull();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
    // The optional/essential advice stands; the invented percentage does not.
    expect(screen.getByText(/places the model treats them as supplementary|treats them as supplementary/)).toBeInTheDocument();
    expect(screen.queryByText(/~45%/)).toBeNull();
  });

  it('opens the per-location rows behind one coverage figure', async () => {
    // `GET /features/{name}/coverage` is the only place the per-location detail
    // lives; no screen called it, so a feature could read "94%" while being
    // absent from a location entirely, with nothing on screen to say which.
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/features') {
        return Promise.resolve({
          data: [
            { id: 'f1', name: 'lag_7', feature_group: 'historical', dtype: 'float', description: 'Demand 7 days before target', coverage_pct: 94.2, leakage_safe: true, in_production: true },
          ],
        });
      }
      if (path === '/api/v1/features/lag_7/coverage') {
        return Promise.resolve({
          feature: 'lag_7',
          feature_group: 'historical',
          leakage_safe: true,
          in_production: true,
          overall_coverage_pct: 90.5,
          per_location: [
            { location_id: 'loc-1', location_name: 'Kigali Heights Bistro', coverage_pct: 100, available_records: 90, total_records: 90 },
            { location_id: 'loc-2', location_name: 'Kimironko Kiosk', coverage_pct: 81, available_records: 24, total_records: 30 },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    });
    const user = userEvent.setup();
    render(<FeatureStore />);

    // Until a feature is chosen the panel says what the one figure cannot.
    await waitFor(() => expect(screen.getByText(/Pick a feature to see its coverage location by location/)).toBeInTheDocument());

    await user.click(await screen.findByRole('button', { name: 'Per-location coverage for lag_7' }));

    await waitFor(() => expect(screen.getByText('Kigali Heights Bistro')).toBeInTheDocument());
    expect(screen.getByText('Kimironko Kiosk')).toBeInTheDocument();
    // The API's own roll-up, not one recomputed from the rows on screen.
    expect(screen.getByText('90.5% across 2 locations')).toBeInTheDocument();
    // The figure the percentage is made of, per location.
    expect(screen.getByText(/100% · 90 of 90 records/)).toBeInTheDocument();
    expect(screen.getByText(/81% · 24 of 30 records/)).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith('/api/v1/features/lag_7/coverage');
  });

  it('states the reported reservation coverage', async () => {
    mockGet.mockImplementation((path: string) =>
      path === '/api/v1/features'
        ? Promise.resolve({
            data: [
              { id: 'f2', name: 'reservation_count', feature_group: 'Reservations', dtype: 'int', description: 'Reservation count for target date', coverage_pct: 45, leakage_safe: true, in_production: true },
            ],
          })
        : Promise.resolve({ data: [] }),
    );
    render(<FeatureStore />);

    await waitFor(() => expect(screen.getByText('reservation_count')).toBeInTheDocument());
    expect(screen.getByText(/~45% of locations/)).toBeInTheDocument();
  });
});

describe('connector field mapping', () => {
  it('renders the source → canonical pair the API names', async () => {
    // `GET /connectors/{id}/mapping` returns `source_field`/`canonical_field`. The
    // rows read `source`/`target`, so both chips rendered empty — and every row
    // shared the key `undefined`.
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/connectors')
        return Promise.resolve({ data: [{ id: 'conn-1', name: 'Momo main', type: 'momo', status: 'active' }] });
      if (path === '/api/v1/api-keys') return Promise.resolve({ data: [] });
      if (path === '/api/v1/connectors/conn-1/mapping')
        return Promise.resolve({
          connector_id: 'conn-1',
          mappings: [
            { id: 'map-1', source_field: 'momo.transaction_time', canonical_field: 'date', transform: 'Truncate to local day' },
          ],
        });
      return Promise.resolve({ data: [] });
    });
    render(<Connectors />);

    await waitFor(() => expect(screen.getByText('momo.transaction_time')).toBeInTheDocument());
    expect(screen.getByText('date')).toBeInTheDocument();
    expect(screen.getByText('Truncate to local day')).toBeInTheDocument();
  });

  it('shows no mappings rather than a demo mapping the workspace never configured', async () => {
    // A failed read used to be answered with two hardcoded momo/pos rows, which
    // read as the workspace's own configuration.
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/connectors')
        return Promise.resolve({ data: [{ id: 'conn-1', name: 'Momo main', type: 'momo', status: 'active' }] });
      if (path === '/api/v1/connectors/conn-1/mapping')
        return Promise.reject(new Error('service unavailable'));
      return Promise.resolve({ data: [] });
    });
    render(<Connectors />);

    await waitFor(() => expect(screen.getByText('No mappings')).toBeInTheDocument());
    expect(screen.queryByText('momo.transaction_time')).toBeNull();
    expect(screen.queryByText('pos.order_count')).toBeNull();
  });
});

describe('webhook test delivery', () => {
  it('keeps what was sent and names the header the receiver must verify', async () => {
    // `POST /webhooks/{id}/test` answers with the event, the response code, the
    // attempt, the sample payload and `signature_header`. The page fired it and
    // kept a toast for two seconds, so an integrator writing a signature check
    // had nothing to compare against.
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/webhooks')
        return Promise.resolve({
          data: [
            {
              id: 'wh-1',
              url: 'https://ops.example.rw/hooks/demandiq',
              events: ['forecast.generated'],
              status: 'active',
              created_at: '2026-09-01T09:00:00Z',
            },
          ],
        });
      if (path === '/api/v1/webhooks/events')
        return Promise.resolve({
          data: [{ id: 'forecast.generated', label: 'forecast.generated', detail: 'A forecast was generated' }],
        });
      if (path === '/api/v1/webhooks/wh-1/deliveries') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    mockPost.mockResolvedValue({
      endpoint_id: 'wh-1',
      event: 'forecast.generated',
      attempt: 1,
      response_code: 200,
      delivered_at: '2026-09-25T09:30:00Z',
      payload: '{"event":"forecast.generated"}',
      signature_header: 'X-DemandIQ-Signature',
    });

    const user = userEvent.setup();
    render(<Webhooks />);
    await user.click(await screen.findByRole('button', { name: /send test event/i }));

    await waitFor(() => expect(screen.getByText('Test delivery: forecast.generated')).toBeInTheDocument());
    expect(screen.getByText('HTTP 200')).toBeInTheDocument();
    expect(screen.getByText('attempt 1')).toBeInTheDocument();
    // The header to verify the HMAC against, named rather than described.
    expect(screen.getByText('X-DemandIQ-Signature')).toBeInTheDocument();
    expect(screen.getByText(/"event":"forecast.generated"/)).toBeInTheDocument();
  });
});

describe('model rollback', () => {
  it('names the version it restored and the one it replaced', async () => {
    // The endpoint reports `model_id` and `previous_production`; the page showed
    // a bare "Rolled back", which does not say what is in production now.
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/models')
        return Promise.resolve({
          data: [
            { id: 'model-v3', version: 'v3', algorithm: 'lightgbm', status: 'candidate', mae: 6.9 },
            { id: 'model-v4', version: 'v4', algorithm: 'lightgbm', status: 'production', mae: 6.7 },
          ],
        });
      return Promise.resolve({ data: [] });
    });
    mockPost.mockResolvedValue({
      model_id: 'model-v3',
      status: 'production',
      previous_production: 'model-v4',
    });

    const user = userEvent.setup();
    render(<ModelRegistry />);
    await user.click(await screen.findByRole('row', { name: 'Select v3' }));
    await user.click(await screen.findByRole('button', { name: 'Roll back' }));

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('v3 is production. v4 was rolled back'),
    );
  });
});

describe('assistant quota grant', () => {
  it('quotes the grant it issued and when it expires', async () => {
    // `POST /admin/agent/grants` returns the grant's id and expiry; the admin
    // saw "Issued +50 … for 7 days" with nothing to quote back later.
    mockGet.mockImplementation(() => Promise.resolve({ data: [] }));
    mockPost.mockResolvedValue({
      grant_id: '4f2a91c3-0000-4000-8000-000000000000',
      expires_at: '2026-10-02T09:00:00Z',
    });

    const user = userEvent.setup();
    render(<AiGovernance />);
    await user.click(await screen.findByRole('button', { name: 'Issue quota' }));
    await user.click(await screen.findByRole('button', { name: 'Issue grant' }));

    await waitFor(() => expect(toast).toHaveBeenCalled());
    const message = String(toast.mock.calls.at(-1)?.[0] ?? '');
    expect(message).toContain('grant 4f2a91c3');
    expect(message).toContain('expires ');
  });
});

describe('model registry detail', () => {
  it('states how the model was configured and what it produced', async () => {
    // The model row carries `hyperparameters`, the training window and the
    // `artifact_uri`; the panel showed none of them.
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/models')
        return Promise.resolve({
          data: [
            {
              id: 'model-1',
              algorithm: 'lightgbm',
              version: 'v4',
              status: 'production',
              mae: 6.7,
              rmse: 11.3,
              rmsle: 0.493,
              hyperparameters: { n_estimators: 400, learning_rate: 0.05, feature_importance: { lag_7: 0.2 } },
              train_start: '2026-01-01',
              train_end: '2026-06-30',
              test_start: '2026-07-01',
              test_end: '2026-07-28',
              artifact_uri: 's3://demandiq/models/lightgbm-v4.joblib',
              trained_at: '2026-08-01T09:00:00Z',
              promoted_at: '2026-08-02T09:00:00Z',
            },
          ],
        });
      return Promise.resolve({ data: [] });
    });
    render(<ModelRegistry />);

    await waitFor(() => expect(screen.getByText(/n_estimators=400/)).toBeInTheDocument());
    // Nested maps are left out rather than printed as [object Object].
    expect(screen.queryByText(/\[object Object\]/)).toBeNull();
    expect(screen.getByText('s3://demandiq/models/lightgbm-v4.joblib')).toBeInTheDocument();
    expect(screen.getByText('2026-01-01 → 2026-06-30')).toBeInTheDocument();
  });
});

describe('connector test run', () => {
  it('shows what the test checked, the schema to produce, and when it ran', async () => {
    // `POST /connectors/{id}/test` answers with `checks`, `sample_payload` and
    // `checked_at`. The page fired it, kept only `status`/`latency_ms` in a
    // toast, and dropped the rest — so a blocked connector could not say which
    // precondition failed, and the schema it must produce was never on screen.
    mockGet.mockImplementation((path: string) => {
      if (path === '/api/v1/connectors')
        return Promise.resolve({ data: [{ id: 'conn-1', name: 'Momo main', type: 'momo', status: 'active' }] });
      if (path === '/api/v1/api-keys') return Promise.resolve({ data: [] });
      if (path === '/api/v1/connectors/conn-1/mapping') return Promise.resolve({ mappings: [] });
      return Promise.resolve({ data: [] });
    });
    mockPost.mockResolvedValue({
      connector_id: 'conn-1',
      latency_ms: 3,
      status: 'blocked',
      checked_at: '2026-09-25T09:30:00Z',
      checks: [
        { name: 'credentials', ok: true, detail: 'credentials stored' },
        { name: 'mapping', ok: false, detail: 'no field mapping configured' },
      ],
      sample_payload: { date: '<momo.transaction_time>', demand_qty: '<integer>' },
    });

    const user = userEvent.setup();
    render(<Connectors />);
    await user.click(await screen.findByRole('button', { name: 'Test Momo main' }));

    await waitFor(() => expect(screen.getByText('Test run: Momo main')).toBeInTheDocument());
    expect(screen.getByText(/1 of 2 passed/)).toBeInTheDocument();
    const checks = screen.getByRole('list', { name: /connector test checks/i });
    // Check names are read as what they verified, not as the API's key names.
    expect(within(checks).getByText(/^Field mapping/)).toBeInTheDocument();
    expect(within(checks).getByText(/failed/)).toBeInTheDocument();
    expect(within(checks).getByText('no field mapping configured')).toBeInTheDocument();

    // A failing check names where its fix is. The mapping has a panel on this
    // screen, so it is offered as a jump; the check that passed offers nothing.
    expect(
      within(checks).getByText(/Nothing maps this source onto the columns a sync needs/),
    ).toBeInTheDocument();
    expect(within(checks).queryByText(/Credentials belong to the channel/)).toBeNull();
    const jump = within(checks).getByRole('button', { name: 'Show field mapping' });
    await user.click(jump);
    const mappingPanel = document.querySelector('div[tabindex="-1"]') as HTMLElement | null;
    expect(mappingPanel?.textContent).toContain('No mappings');
    // Focus landed on the panel the button names, not merely somewhere below.
    expect(document.activeElement).toBe(mappingPanel);

    // The canonical schema the connector must produce, with its source field.
    expect(screen.getByText('demand_qty')).toBeInTheDocument();
    expect(screen.getByText('<momo.transaction_time>')).toBeInTheDocument();
    // And when the run happened.
    expect(screen.getByText(/^Checked /)).toBeInTheDocument();
  });
});
