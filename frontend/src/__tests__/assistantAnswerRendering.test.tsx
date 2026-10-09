import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest';
import { render, act, screen, waitFor, fireEvent } from '@testing-library/react';

// jsdom implements no Element.scrollTo, and the panel scrolls its thread to the
// newest turn on every render.
beforeAll(() => {
  Element.prototype.scrollTo = () => {};
  Element.prototype.scrollIntoView = () => {};
});

/**
 * The panel renders `headline` and then each `body` paragraph, and offers
 * `action` as a button. All three are fields the answers must carry, and until
 * this test existed nothing proved they reached the DOM.
 *
 * The payloads below are the shapes the backend actually returns
 * (`backend/app/routers/agent.py` -> the answer contract in `AGENT_DESIGN.md`:
 * headline, body, figures, sources). Two wiring gaps motivated this:
 *
 * 1. `body` was never sent, so the panel fell back to `[headline]` and printed
 *    the same line twice with no reasoning;
 * 2. sessions are created lazily by `ask()` rather than on panel open, so the
 *    response's `greeting` was read on the scope-change/reset path only — the
 *    greeting on screen was always the local fallback.
 *
 * Both are invisible to `tsc` (the fields are optional and the fallbacks are
 * valid), which is why they are pinned here instead.
 */

vi.mock('../contexts/SessionContext', () => ({
  useSession: () => ({
    user: {
      name: 'Kelvin Tawe',
      role: 'Owner',
      roleKey: 'owner',
      email: 'demo@demandiq.app',
      id: 'user-1',
      initials: 'KT',
      permissions: ['agent:read'],
    },
    isAuthenticated: true,
  }),
}));

vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));

vi.mock('../api/client', () => ({
  ApiException: class ApiException extends Error {
    status = 500;
    error = { message: 'failed', code: 'failed' };
    reference = null;
  },
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import { api } from '../api/client';
import { AgentProvider } from '../contexts/AgentContext';
import { AgentLauncher } from '../components/agent/AgentLauncher';
import { AgentPanel } from '../components/agent/AgentPanel';

const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;
const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

// The greeting the API composes, including the location scope it resolves.
const API_GREETING =
  "Hi Kelvin \u2014 I'm your DemandIQ assistant. I answer only from this workspace's data: demand history, forecasts and their intervals, the production model and its calibration, the preparation policies, and ingestion health. Ask me what to prepare tomorrow, why a number moved, or whether something needs your attention. If I cannot source an answer I will say so rather than guess.";

// A grounded answer with no policy applied: headline, two body paragraphs and a
// hand-off button, exactly as the backend shapes it.
const GROUNDED_ANSWER = {
  id: 'turn-1',
  session_id: 'sess-1',
  question: 'how many covers tomorrow?',
  headline: 'Forecast for 2017-03-01: point 164 (90% interval 141\u2013189).',
  body: [
    'The model expects about 164 visitors on 2017-03-01. Its calibrated 90% interval runs 141 to 189 \u2014 about 29% of the point estimate \u2014 and that range is a coverage guarantee rather than a safety margin: the real outcome lands inside it about nine days in ten.',
    'I have the demand forecast but no preparation policy applied to it, so I can tell you what to expect but not how much to prepare.',
  ],
  figures: [
    { label: 'Point forecast', value: '164' },
    { label: '90% range', value: '141\u2013189' },
  ],
  action: { label: 'Open the forecast', route: 'forecast' },
  sources: [{ label: 'Forecast', detail: 'fc-1 2017-03-01 point 164 [141\u2013189]', id: 'fc-1' }],
  tools_used: ['get_forecast'],
  refused: false,
  latency_ms: 7,
  created_at: '2026-09-24T12:00:00Z',
};

const SESSION = {
  session_id: 'sess-1',
  greeting: API_GREETING,
  limits: { idle_timeout_min: 30, max_duration_h: 4, turns: 40, burst_per_min: 6 },
  quota: { used: 0, limit: 60, tier: 'owner' },
};

function mockApi(overrides: Partial<Record<string, unknown>> = {}) {
  mockPost.mockImplementation((path: string) => {
    if (path === '/api/v1/agent/sessions') return Promise.resolve(SESSION);
    if (path === '/api/v1/agent/messages') {
      return Promise.resolve(overrides.answer ?? GROUNDED_ANSWER);
    }
    return Promise.resolve({});
  });
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/agent/messages') return Promise.resolve({ data: [] });
    if (path === '/api/v1/agent/quota') {
      return Promise.resolve({ used: 0, limit: 60, tier: 'owner', resets_at: null });
    }
    if (path === '/api/v1/agent/insights') return Promise.resolve({ data: [] });
    return Promise.resolve({ data: [] });
  });
}

async function flush(times = 6) {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

beforeEach(() => {
  mockPost.mockReset();
  mockGet.mockReset();
  mockApi();
});

describe('assistant answer rendering', () => {
  it('shows the API greeting when the panel is opened', async () => {
    render(
      <AgentProvider>
        <AgentLauncher />
        <AgentPanel activeNavId="overview" onNavigate={vi.fn()} />
      </AgentProvider>,
    );

    await act(async () => {
      screen.getByLabelText(/Open DemandIQ Assistant/i).click();
    });
    await flush();

    // The greeting is API-sourced, so it must be the one on screen — not the
    // local fallback that would render if the field were dropped again.
    await waitFor(() => {
      expect(screen.getByText(API_GREETING)).toBeTruthy();
    });
    // Opening the panel is what creates the session: without that, the greeting
    // arrives only after the first question, by which point it is not rendered.
    expect(mockPost.mock.calls.some(([p]) => p === '/api/v1/agent/sessions')).toBe(true);
  });

  it('renders the headline, every body paragraph and the action button', async () => {
    render(
      <AgentProvider>
        <AgentLauncher />
        <AgentPanel activeNavId="overview" onNavigate={vi.fn()} />
      </AgentProvider>,
    );
    await act(async () => {
      screen.getByLabelText(/Open DemandIQ Assistant/i).click();
    });
    await flush();

    const input = document.getElementById('agent-input') as HTMLInputElement;
    expect(input).toBeTruthy();
    // The composer is a <form onSubmit> with a controlled input and a submit
    // button; jsdom does not implicitly submit on Enter, so send it the way a
    // user does.
    await act(async () => {
      fireEvent.change(input, { target: { value: 'how many covers tomorrow?' } });
    });
    await act(async () => {
      screen.getByLabelText('Send').click();
    });
    await flush(10);

    await waitFor(() => {
      expect(screen.getByText(GROUNDED_ANSWER.headline)).toBeTruthy();
    });
    // Both paragraphs, verbatim. A single-paragraph body is the bug this
    // replaces (the panel used to print the headline twice).
    for (const paragraph of GROUNDED_ANSWER.body) {
      expect(screen.getByText(paragraph)).toBeTruthy();
    }
    // The hand-off button, labelled for the screen it opens.
    expect(screen.getByText('Open the forecast')).toBeTruthy();
    // Figures still render as chips.
    expect(screen.getByText('Point forecast')).toBeTruthy();
    // And the headline is not duplicated as body text.
    expect(screen.queryAllByText(GROUNDED_ANSWER.headline)).toHaveLength(1);
  });
});
