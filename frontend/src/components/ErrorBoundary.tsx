import { Component, type ErrorInfo, type ReactNode } from 'react';
import { ErrorPage } from '../pages/ErrorPage';

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Offered as "Back to dashboard"; omit where there is no navigation yet. */
  onHome?: () => void;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Turns a render crash into a page, not a white screen.
 *
 * An uncaught error in a page (a chart handed a malformed row, a missing field
 * read with confidence) unmounts the whole React tree, so the user was left
 * looking at an empty document with no navigation, no message and no way back —
 * the same dead end as an expired session. This catches it where the app is
 * already using `ErrorPage` for its 404/offline states and offers a retry.
 *
 * Deliberately a class: `componentDidCatch` has no hook equivalent. Reset the
 * boundary by changing its `key` (App keys it by screen, so navigating away
 * from a crashed page clears the error).
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // The screen is recoverable now, but the failure still has to be visible to
    // whoever is debugging: a swallowed crash is worse than a loud one.
    console.error('Screen crashed:', error, info.componentStack);
  }

  private reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <ErrorPage
        kind="crash"
        detail={error.message || 'Unhandled render error'}
        onRetry={this.reset}
        onHome={this.props.onHome}
      />
    );
  }
}
