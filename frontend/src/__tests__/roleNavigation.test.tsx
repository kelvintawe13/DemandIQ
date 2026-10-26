import { describe, it, expect } from 'vitest';
import {
  ALL_ROLES,
  HOME_BY_ROLE,
  canAccess,
  itemsForRole,
  sectionsForRole,
  type AppRole,
} from '../data/navItems';

/**
 * The sidebar must never offer a screen the API refuses. These assertions are
 * the frontend half of the contract in backend/app/security/permissions.py —
 * they are written as capability statements ("an owner does not get the model
 * console") so a role re-scope has to be deliberate on both sides.
 */

const idsFor = (role: AppRole, isSiteAdmin = false) =>
  itemsForRole(role, isSiteAdmin).map((item) => item.id);

const labels = (role: AppRole) => sectionsForRole(role).map((section) => section.titleKey);

describe('role navigation', () => {
  it('gives every role the workspace reads', () => {
    for (const role of ALL_ROLES) {
      const ids = idsFor(role);
      expect(ids).toContain('overview');
      expect(ids).toContain('analytics');
      expect(ids).toContain('forecast');
    }
  });

  it('sends the owner to the business and the administrator to the console', () => {
    expect(HOME_BY_ROLE.Owner).toBe('overview');
    expect(HOME_BY_ROLE.Administrator).toBe('admin-models');
    expect(HOME_BY_ROLE['Data Clerk']).toBe('ingestion');
    for (const role of ALL_ROLES) {
      expect(canAccess(role, HOME_BY_ROLE[role])).toBe(true);
    }
  });

  it('keeps the modelling console to the administrator', () => {
    for (const id of ['admin-models', 'admin-accuracy', 'admin-experiments', 'admin-features', 'admin-datasets']) {
      expect(canAccess('Administrator', id)).toBe(true);
      expect(canAccess('Owner', id)).toBe(false);
      expect(canAccess('Analyst', id)).toBe(false);
      expect(canAccess('Data Clerk', id)).toBe(false);
      expect(canAccess('Viewer', id)).toBe(false);
    }
  });

  it('keeps the owner on the business screens and out of every admin console', () => {
    const ids = idsFor('Owner');
    // The owner runs the restaurant, not the platform: the business workspace
    // and its own settings, and nothing from the console groups.
    expect(ids.sort()).toEqual([
      'alerts',
      'analytics',
      'forecast',
      'ingestion',
      'overview',
      'quality',
      'reports',
      'settings',
      'sources',
    ]);
    for (const id of [
      'admin-models',
      'admin-accuracy',
      'admin-experiments',
      'admin-features',
      'admin-policies',
      'admin-explainer',
      'admin-locations',
      'device-fleet',
      'admin-approvals',
      'admin-access',
      'admin-site',
      'admin-datasets',
      'mirror-approvals',
    ]) {
      expect(canAccess('Owner', id)).toBe(false);
    }
  });

  it('lets the data clerk enter and clean data and nothing more', () => {
    const ids = idsFor('Data Clerk');
    expect(ids).toEqual(
      expect.arrayContaining(['ingestion', 'sources', 'quality', 'alerts', 'reports', 'forecast'])
    );
    expect(ids).not.toContain('admin-models');
    expect(ids).not.toContain('admin-accuracy');
    expect(ids).not.toContain('admin-policies');
    expect(ids).not.toContain('admin-explainer');
    expect(ids).not.toContain('admin-access');
    expect(ids).not.toContain('admin-approvals');
  });

  it('gives the analyst simulation and explanation, but no console', () => {
    const ids = idsFor('Analyst');
    expect(ids).toContain('admin-policies');
    expect(ids).toContain('admin-explainer');
    expect(ids).not.toContain('admin-models');
    expect(ids).not.toContain('admin-features');
    expect(ids).not.toContain('admin-access');
    // Workspace settings only — the governance copy belongs to owner/admin.
    expect(ids.filter((id) => id === 'settings')).toHaveLength(1);
  });

  it('leaves the viewer with reads only', () => {
    expect(idsFor('Viewer').sort()).toEqual(['analytics', 'forecast', 'overview']);
  });

  it("shows the owner the workspace screens the administrator keeps too", () => {
    // The original defect: an owner could not reach their own forecast screen.
    for (const id of ['overview', 'analytics', 'forecast', 'ingestion', 'reports', 'alerts']) {
      expect(canAccess('Owner', id)).toBe(true);
      expect(canAccess('Administrator', id)).toBe(true);
    }
  });

  it('never renders two entries with the same id', () => {
    for (const role of ALL_ROLES) {
      const ids = idsFor(role);
      expect(new Set(ids).size).toBe(ids.length);
      // 'settings' exists in both the workspace and governance sections; each
      // role must see exactly one of them.
      expect(ids.filter((id) => id === 'settings').length).toBeLessThanOrEqual(1);
    }
  });

  it('groups the navigation by role, not by an is-admin flag', () => {
    expect(labels('Viewer')).toEqual(['nav.group.insights']);
    expect(labels('Data Clerk')).toEqual(['nav.group.insights', 'nav.group.data', 'nav.group.system']);
    // The console groups belong to the administrator; the owner is a business
    // role, so its sidebar is the workspace groups only.
    expect(labels('Analyst')).not.toContain('nav.group.governance');
    expect(labels('Owner')).toEqual([
      'nav.group.insights',
      'nav.group.data',
      'nav.group.system',
    ]);
    expect(labels('Administrator')).toContain('nav.group.governance');
  });

  it('gates the Site Admin console on the platform flag, not the role', () => {
    // The flag is necessary but not sufficient: the console is an
    // administrator's screen, so the owner never sees it even as a site admin.
    expect(canAccess('Administrator', 'admin-site')).toBe(false);
    expect(canAccess('Administrator', 'admin-site', true)).toBe(true);
    expect(canAccess('Owner', 'admin-site', true)).toBe(false);
    expect(idsFor('Administrator', true)).toContain('admin-site');
    expect(idsFor('Administrator', false)).not.toContain('admin-site');
    expect(idsFor('Owner', true)).not.toContain('admin-site');
  });
});
