import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BrandLogo } from '../components/auth/BrandLogo';
import { withBase } from '../lib/route';

describe('brand logo navigation', () => {
  it('links to the landing route from public and auth screens', () => {
    render(<BrandLogo size="sm" />);

    expect(screen.getByRole('link', { name: 'DemandIQ home' })).toHaveAttribute(
      'href',
      withBase('/')
    );
  });
});