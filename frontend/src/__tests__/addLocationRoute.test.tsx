/**
 * Adding a location is a route, not a dialog.
 *
 * It used to be the same `FormModal` editing uses, opened from the registry. It
 * is now its own page (`/admin-locations-new`) built on the same
 * `LocationFields` — these tests pin the three things that makes true: the fields
 * the dialog renders are here, submitting writes once and leaves, and every way
 * out of the form that is *not* submitting writes nothing at all.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }));
vi.mock('../contexts/ToastContext', () => ({ useToast: () => ({ toast }) }));
vi.mock('../api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  ApiException: class ApiException extends Error {},
}));

import { api } from '../api/client';
import { AddLocation } from '../pages/admin/AddLocation';

const mockPost = api.post as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  mockPost.mockReset();
  toast.mockReset();
});

describe('add location route', () => {
  it('renders the same grouped fields the edit dialog does', () => {
    render(<AddLocation />);

    for (const section of ['Identity', 'Trading', 'Contact', 'Notes']) {
      expect(screen.getByRole('heading', { name: section })).toBeInTheDocument();
    }
    // The bar the whole screen exists to explain.
    expect(screen.getByText(/forecasts from 7 days of history/i)).toBeInTheDocument();
    expect(screen.getByText('Day 7')).toBeInTheDocument();
  });

  it('creates the site once and returns to the registry', async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    mockPost.mockResolvedValue({ id: 'loc-new' });
    render(<AddLocation onNavigate={onNavigate} />);

    await user.type(screen.getByLabelText(/Location name/), 'Remera Grill');
    await user.clear(screen.getByLabelText('Seating capacity'));
    await user.type(screen.getByLabelText('Seating capacity'), '120');
    await user.type(screen.getByLabelText('Manager'), 'Aline Umutoni');
    await user.click(screen.getByRole('button', { name: /Add location/i }));

    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(mockPost).toHaveBeenCalledWith(
      '/api/v1/locations',
      expect.objectContaining({ name: 'Remera Grill', seating_capacity: 120, manager_name: 'Aline Umutoni' }),
    );
    // The new site is reported before the list comes back, then the page leaves.
    expect(toast).toHaveBeenCalledWith(expect.stringContaining('Location added'), 'success');
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith('admin-locations'));
  });

  it('will not create a site with no name', async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<AddLocation onNavigate={onNavigate} />);

    await user.click(screen.getByRole('button', { name: /Add location/i }));

    expect(mockPost).not.toHaveBeenCalled();
    expect(onNavigate).not.toHaveBeenCalled();
    expect(screen.getByText(/Give the location a name/i)).toBeInTheDocument();
  });

  it('leaves without writing when the operator backs out', async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<AddLocation onNavigate={onNavigate} />);

    await user.type(screen.getByLabelText(/Location name/), 'Abandoned Site');
    await user.click(screen.getByRole('button', { name: /Back to locations/i }));

    expect(mockPost).not.toHaveBeenCalled();
    expect(onNavigate).toHaveBeenCalledWith('admin-locations');
  });
});
