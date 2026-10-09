import '@testing-library/jest-dom';

// jsdom lacks these; framer-motion + recharts need them
class MockIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
class MockResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (!('IntersectionObserver' in globalThis)) {
  (globalThis as unknown as Record<string, unknown>).IntersectionObserver = MockIntersectionObserver;
}
if (!('ResizeObserver' in globalThis)) {
  (globalThis as unknown as Record<string, unknown>).ResizeObserver = MockResizeObserver;
}
Object.defineProperty(globalThis.window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    // Tests run with prefers-reduced-motion ON: framer-motion renders
    // entrances in their final state (Panel starts opacity 1), so assertions
    // are synchronous and deterministic instead of racing the first frame.
    matches: query.includes('prefers-reduced-motion'),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
// framer-motion reducedMotion
Object.defineProperty(globalThis.window, 'scrollTo', { value: () => {}, writable: true });

