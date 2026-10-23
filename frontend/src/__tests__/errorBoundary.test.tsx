/**
 * A render error in a page used to unmount the whole tree: a blank document
 * with no navigation, no message and no way back. The boundary turns that into
 * the app's own crash page — the same one used for 404/offline — with a retry.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';

import { ErrorBoundary } from '../components/ErrorBoundary';

function Boom(): JSX.Element {
  throw new Error('chart blew up');
}

describe('ErrorBoundary', () => {
  it('renders the crash page instead of blanking the document', () => {
    // React logs every caught error; the assertion is the page, not the console.
    const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByRole('heading', { name: /Something went wrong/i })).toBeInTheDocument();
    // The thrown message is the diagnostics strip, so support can act on it.
    expect(screen.getByText('chart blew up')).toBeInTheDocument();
    expect(logged).toHaveBeenCalled();
    logged.mockRestore();
  });

  it('retries by re-rendering the children', async () => {
    const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    let boom = true;
    function Flaky(): JSX.Element {
      if (boom) throw new Error('first paint failed');
      return <p>recovered</p>;
    }

    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('heading', { name: /Something went wrong/i })).toBeInTheDocument();

    boom = false;
    await userEvent.click(screen.getByRole('button', { name: /Try again/i }));

    expect(screen.getByText('recovered')).toBeInTheDocument();
    logged.mockRestore();
  });
});
