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

  it('lets the owner plan and govern without training models', () => {
    const ids = idsFor('Owner');
    // Planning and governance are theirs.
    expect(ids).toContain('admin-policies');
    expect(ids).toContain('admin-approvals');
    expect(ids).toContain('admin-access');
    expect(ids).toContain('admin-locations');
    expect(ids).toContain('settings');
    // The model and integration console is not.
    expect(ids).not.toContain('admin-models');
    expect(ids).not.toContain('admin-accuracy');
    expect(ids).not.toContain('admin-experiments');
    expect(ids).not.toContain('admin-connectors');
    expect(ids).not.toContain('admin-webhooks');
    expect(ids).not.toContain('admin-pipeline');
    expect(ids).not.toContain('admin-ai');
    expect(ids).not.toContain('admin-datasets');
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
    // Governance only for the roles that run the workspace.
    expect(labels('Analyst')).not.toContain('nav.group.governance');
    expect(labels('Owner')).toContain('nav.group.governance');
    expect(labels('Administrator')).toContain('nav.group.governance');
  });

  it('gates the Site Admin console on the platform flag, not the role', () => {
    expect(canAccess('Owner', 'admin-site')).toBe(false);
    expect(canAccess('Owner', 'admin-site', true)).toBe(true);
    expect(canAccess('Administrator', 'admin-site', true)).toBe(true);
    expect(idsFor('Owner', true)).toContain('admin-site');
    expect(idsFor('Owner', false)).not.toContain('admin-site');
  });
});
