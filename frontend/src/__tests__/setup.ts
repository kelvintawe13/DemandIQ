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
if (!('matchMedia' in globalThis.window)) {
  Object.defineProperty(globalThis.window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
// framer-motion reducedMotion
Object.defineProperty(globalThis.window, 'scrollTo', { value: () => {}, writable: true });

