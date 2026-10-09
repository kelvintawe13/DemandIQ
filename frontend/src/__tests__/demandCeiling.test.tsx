/**
 * The manual entry field mirrors the server's `MAX_DEMAND_QTY` cap.
 *
 * Why the client repeats a server rule: a single entry of 222,000,100 was once
 * accepted and stored, and then failed every later forecast for that location
 * with a 500 — the operator had no way to connect the two, because nothing they
 * typed looked wrong. The server still refuses it (a 422 naming the field); this
 * just says so while the number is being typed, at the field, rather than after
 * a round trip.
 *
 * The lower bound is deliberately *not* treated the same way: a negative is
 * floored to 0 as it is typed, while an out-of-range number is left exactly as
 * typed and reported. Silently rewriting 222,000,100 to 10,000,000 would hide the
 * typo instead of naming it, which is how the original bug stayed invisible.
 *
 * The field also starts **empty**. It used to be pre-filled with 100, which is a
 * number the operator never chose and was one stray click away from being saved
 * as a real day of demand.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('../contexts/NavigationContext', () => ({ useNavigation: () => ({ navigate: vi.fn() }) }));
vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  ApiException: class ApiException extends Error {},
}));

import { api } from '../api/client';
import { ManualFormTab } from '../components/ingestion/ManualFormTab';
import { MAX_DEMAND_QTY, MAX_DEMAND_QTY_MESSAGE } from '../components/ingestion/limits';

const mockGet = api.get as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockImplementation((path: string) => {
    if (path === '/api/v1/locations')
      return Promise.resolve({ data: [{ id: 'loc-1', name: 'Kigali Heights Bistro' }] });
    return Promise.resolve({ data: [], total: 0 });
  });
});

/** The visitors field, with the location chosen so the form can otherwise submit. */
async function openForm() {
  render(<ManualFormTab />);
  const visitors = await screen.findByLabelText(/Number of Visitors \/ Demand/);
  await waitFor(() =>
    expect(screen.getByLabelText(/Location \/ Site/)).not.toBeDisabled(),
  );
  fireEvent.change(screen.getByLabelText(/Location \/ Site/), { target: { value: 'loc-1' } });
  return visitors as HTMLInputElement;
}

describe('manual entry demand ceiling', () => {
  it('accepts a value at the cap and reports one over it', async () => {
    const visitors = await openForm();

    fireEvent.change(visitors, { target: { value: String(MAX_DEMAND_QTY) } });
    expect(screen.queryByText(MAX_DEMAND_QTY_MESSAGE)).toBeNull();

    fireEvent.change(visitors, { target: { value: '222000100' } });
    await waitFor(() => expect(screen.getByText(MAX_DEMAND_QTY_MESSAGE)).toBeInTheDocument());
  });

  it('leaves an out-of-range number exactly as typed', async () => {
    const visitors = await openForm();

    fireEvent.change(visitors, { target: { value: '222000100' } });
    await screen.findByText(MAX_DEMAND_QTY_MESSAGE);

    // Quietly clamping to the ceiling would hide the typo.
    expect(visitors.value).toBe('222000100');
  });

  it('clears the message once the value is back inside the range', async () => {
    const visitors = await openForm();

    fireEvent.change(visitors, { target: { value: '222000100' } });
    await screen.findByText(MAX_DEMAND_QTY_MESSAGE);

    fireEvent.change(visitors, { target: { value: '250' } });
    expect(screen.queryByText(MAX_DEMAND_QTY_MESSAGE)).toBeNull();
  });

  it('still floors a negative to zero rather than reporting it', async () => {
    const visitors = await openForm();

    fireEvent.change(visitors, { target: { value: '-5' } });
    expect(visitors.value).toBe('0');
    expect(screen.queryByText(MAX_DEMAND_QTY_MESSAGE)).toBeNull();
  });

  it('reports the field to assistive tech while it is invalid', async () => {
    const visitors = await openForm();

    expect(visitors).not.toHaveAttribute('aria-invalid');

    fireEvent.change(visitors, { target: { value: '222000100' } });
    await screen.findByText(MAX_DEMAND_QTY_MESSAGE);

    // `aria-describedby` ties the message to the field, so the refusal is not
    // sitting on the page unannounced.
    expect(visitors).toHaveAttribute('aria-invalid', 'true');
    const describedBy = visitors.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy as string)).toHaveTextContent(
      MAX_DEMAND_QTY_MESSAGE,
    );
  });

  it('stops the stepper at the cap instead of stepping past it', async () => {
    const visitors = await openForm();

    fireEvent.change(visitors, { target: { value: String(MAX_DEMAND_QTY - 5) } });
    fireEvent.click(screen.getByLabelText('Increase visitors'));
    expect(visitors.value).toBe(String(MAX_DEMAND_QTY));

    fireEvent.click(screen.getByLabelText('Increase visitors'));
    expect(visitors.value).toBe(String(MAX_DEMAND_QTY));
    expect(screen.queryByText(MAX_DEMAND_QTY_MESSAGE)).toBeNull();
  });

  it('starts empty instead of pre-filled with 100', async () => {
    const visitors = await openForm();

    // A default is a value nobody typed. Clicking through the confirm dialog
    // used to write 100 visitors for that day, which is how a stray click became
    // a day of demand.
    expect(visitors.value).toBe('');
  });

  it('refuses to add an entry with a blank count, naming the field', async () => {
    await openForm();

    fireEvent.click(screen.getByRole('button', { name: /Add another entry/i }));

    await waitFor(() => expect(screen.getByText(/Enter the number of visitors/)).toBeInTheDocument());
  });

  it('steps from a blank field rather than ignoring the press', async () => {
    const visitors = await openForm();

    expect(visitors.value).toBe('');
    fireEvent.click(screen.getByLabelText('Increase visitors'));
    // A press on a blank field has to produce a real value, or the control looks
    // broken.
    expect(visitors.value).toBe('10');
  });
});
