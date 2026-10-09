import { describe, expect, it } from 'vitest';
import {
  auditActionLabel,
  auditClientLabel,
  auditDetailLabel,
  auditLine,
  auditOutcomeLabel,
  auditTargetLabel,
  modelLabel,
  plainStatus,
  qualityDimensionHint,
  qualityDimensionLabel,
} from '../lib/plainLanguage';

describe('plainStatus', () => {
  it('translates the ingest/pipeline tokens an operator would never guess', () => {
    expect(plainStatus('committed')).toBe('Imported');
    expect(plainStatus('awaiting_mapping')).toBe('Needs column mapping');
    expect(plainStatus('queued')).toBe('Waiting to start');
    expect(plainStatus('running')).toBe('Running now');
    expect(plainStatus('failed')).toBe('Failed');
  });

  it('is case-insensitive and never blanks out', () => {
    expect(plainStatus('Committed')).toBe('Imported');
    expect(plainStatus(null)).toBe('—');
    expect(plainStatus(undefined)).toBe('—');
  });

  it('still reads as words for a status the UI has not seen before', () => {
    expect(plainStatus('partially_backfilled')).toBe('Partially backfilled');
  });
});

describe('audit action labels', () => {
  // The middleware stores the literal request line as `action` (see
  // backend/app/middleware/audit.py), so these are the real values that reach the UI.
  it('names the routes the dashboard actually sees', () => {
    expect(auditActionLabel('POST /api/v1/agent/sessions')).toBe('Assistant session started');
    expect(auditActionLabel('POST /api/v1/agent/messages')).toBe('Question asked in the assistant');
    expect(auditActionLabel('POST /api/v1/policies')).toBe('Preparation policy saved');
  });

  it('never shows an operator a request path', () => {
    // The original bug: an unrecognised action fell through to the raw endpoint,
    // putting "POST /api/v1/agent/messages" on the Overview screen.
    const labels = [
      auditActionLabel('POST /api/v1/agent/sessions'),
      auditActionLabel('PATCH /api/v1/workspace/settings'),
      auditActionLabel('DELETE /api/v1/api-keys/1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d'),
      auditActionLabel('POST /api/v1/something/brand-new'),
    ];
    for (const label of labels) {
      expect(label).not.toMatch(/\/api\//);
      expect(label).not.toMatch(/^\/|\/$/);
    }
  });

  it('describes an unknown route by its area, in words', () => {
    expect(auditActionLabel('POST /api/v1/agent/messages')).not.toBe('POST /api/v1/agent/messages');
    // The last two path segments, so the area is still recognisable.
    expect(auditActionLabel('POST /api/v1/something/brand-new')).toBe('Something brand new');
    expect(auditActionLabel('POST /api/v1/agent/sessions/1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d')).toBe(
      'Agent sessions',
    );
    // A bare verb (older rows) still reads as words.
    expect(auditActionLabel('view')).toBe('View');
    expect(auditActionLabel(null)).toBe('Activity');
  });
});

describe('audit action labels for the routes a personal feed shows', () => {
  it('names what the person did, not the endpoint area', () => {
    // These used to read "Me preferences" / "Me avatar"; the endpoint's area
    // rather than the change the person made.
    expect(auditActionLabel('PATCH /api/v1/me/preferences')).toBe('Preferences updated');
    expect(auditActionLabel('POST /api/v1/me/avatar')).toBe('Profile photo changed');
    expect(auditActionLabel('POST /api/v1/auth/login')).toBe('Signed in');
  });

  it('names the id-bearing routes, which an exact key cannot match', () => {
    expect(
      auditActionLabel('DELETE /api/v1/me/sessions/1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d'),
    ).toBe('Another session signed out');
    expect(
      auditActionLabel('POST /api/v1/quality/issues/2b3c4d5e-6f70-8a9b-0c1d-2e3f4a5b6c7d/apply-fix'),
    ).toBe('Quality fix applied');
  });

  it('tells the console’s own writes apart, which the area fallback cannot', () => {
    // Every one of these fell back to a shared area word: "Quality rules",
    // "Locations", "Members" — the same reading whether a rule was switched, a
    // site added or edited, or a seat revoked. All five are in this workspace's
    // own trail (see the audit_log actions), so this is the page's real copy.
    const uuid = '0eadab84-db10-4ea4-b2e9-431f5e586189';
    expect(auditActionLabel(`PATCH /api/v1/quality/rules/${uuid}`)).toBe('Quality rule switched');
    expect(auditActionLabel(`PATCH /api/v1/locations/${uuid}`)).toBe('Location details updated');
    expect(auditActionLabel(`DELETE /api/v1/locations/${uuid}`)).toBe('Location archived');
    expect(auditActionLabel('POST /api/v1/locations')).toBe('Location added');
    expect(auditActionLabel('POST /api/v1/members/invite')).toBe('Member invited');
    expect(auditActionLabel(`PATCH /api/v1/members/${uuid}`)).toBe('Member role or name changed');
    expect(auditActionLabel(`DELETE /api/v1/members/${uuid}`)).toBe('Member access revoked');
    // And the identifier still cannot reach the screen.
    for (const action of [`PATCH /api/v1/quality/rules/${uuid}`, `DELETE /api/v1/members/${uuid}`]) {
      expect(auditActionLabel(action)).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}/i);
    }
  });

  it('names a router-written token the same way as its request line', () => {
    // Two rows are written for one request: the middleware's request line and
    // the router's own token (backend/app/routers/governance.py). They must not
    // read as two different acts.
    expect(auditActionLabel('member.invite')).toBe(auditActionLabel('POST /api/v1/members/invite'));
    expect(auditActionLabel('member.update')).toBe(
      auditActionLabel('PATCH /api/v1/members/0eadab84-db10-4ea4-b2e9-431f5e586189'),
    );
    expect(auditActionLabel('member.revoke')).toBe(
      auditActionLabel('DELETE /api/v1/members/0eadab84-db10-4ea4-b2e9-431f5e586189'),
    );
    expect(auditActionLabel('workspace.update')).toBe(auditActionLabel('PATCH /api/v1/workspace'));
    // A token nobody has paired yet still reads as words, not as its enum.
    expect(auditActionLabel('change_request.approved')).toBe('Change request approved');
    expect(auditActionLabel('mirror.consent_recorded')).toBe('Mirror consent recorded');
  });
});

describe('audit detail labels', () => {
  it('says what happened and where, instead of repeating the endpoint', () => {
    // The middleware stores the request path as `target`, so a path-valued target
    // must never reach the screen; the outcome and surface are what is left.
    expect(
      auditDetailLabel({
        target: '/api/v1/me/preferences',
        client: 'web',
        meta_data: { status_code: 200 },
      }),
    ).toBe('accepted from the web app');
    expect(
      auditDetailLabel({
        target: '/api/v1/me/preferences',
        client: 'mobile',
        meta_data: { status_code: 403 },
      }),
    ).toBe('rejected (403) from a phone');
  });

  it('keeps a real identifier as the target', () => {
    expect(auditDetailLabel({ target: '6f6826b2-28f0-4e90-8fd7-e80605cb3d48' })).toBe(
      '6f6826b2-28f0-4e90-8fd7-e80605cb3d48',
    );
  });

  it('keeps a pipeline row\'s own summary', () => {
    expect(
      auditDetailLabel({ target: '/api/v1/pipeline/runs', detail: 'synced 2 active connector(s)' }),
    ).toBe('synced 2 active connector(s)');
  });

  it('falls back to the surface alone for rows written before meta_data', () => {
    expect(auditDetailLabel({ target: '/api/v1/me/preferences', client: 'web' })).toBe(
      'from the web app',
    );
    expect(auditDetailLabel({})).toBe('');
    expect(auditDetailLabel(null)).toBe('');
  });
});

describe('audit outcome labels', () => {
  it('reads the status code the middleware stored', () => {
    expect(auditOutcomeLabel({ meta_data: { status_code: 201 } })).toBe('accepted');
    expect(auditOutcomeLabel({ meta_data: { status_code: 403 } })).toBe('rejected (403)');
  });

  it('reads the site-admin list\'s flattened `meta` too', () => {
    // GET /admin/site/audit-log returns the same field as `meta`.
    expect(auditOutcomeLabel({ meta: { status_code: 204 } })).toBe('accepted');
  });

  it('says nothing when the row has no status code', () => {
    expect(auditOutcomeLabel({})).toBeNull();
    expect(auditOutcomeLabel({ meta_data: {} })).toBeNull();
    expect(auditOutcomeLabel(null)).toBeNull();
  });
});

describe('audit client labels', () => {
  it('names the surface in operator words', () => {
    expect(auditClientLabel('web')).toBe('the web app');
    expect(auditClientLabel('mobile')).toBe('a phone');
    expect(auditClientLabel('api')).toBe('the API');
    expect(auditClientLabel(null)).toBeNull();
  });
});

describe('audit target labels', () => {
  it('refuses the request path the middleware stores as `target`', () => {
    // An 8-char slice of this used to render as the detail line "forecast /api/v1/".
    expect(auditTargetLabel('/api/v1/forecasts')).toBeNull();
    expect(auditTargetLabel('/api/v1/ingest/manual')).toBeNull();
  });

  it('keeps a real identifier', () => {
    expect(auditTargetLabel('6f6826b2-28f0-4e90-8fd7-e80605cb3d48')).toBe(
      '6f6826b2-28f0-4e90-8fd7-e80605cb3d48',
    );
    expect(auditTargetLabel(null)).toBeNull();
    expect(auditTargetLabel('')).toBeNull();
  });
});

describe('auditLine', () => {
  it('returns the action in words and drops a path-valued target', () => {
    // The middleware stores action="POST /api/v1/agent/messages" and
    // target="/api/v1/agent/messages"; neither may reach the screen.
    expect(auditLine('POST /api/v1/agent/messages', '/api/v1/agent/messages')).toBe(
      'Question asked in the assistant',
    );
    expect(auditLine('PATCH /api/v1/workspace/settings', '/api/v1/workspace/settings')).toBe(
      'Workspace settings',
    );
  });

  it('keeps a real identifier as the target', () => {
    expect(auditLine('PATCH /api/v1/policies', '7')).toBe('Preparation policy updated: 7');
  });

  it('handles a missing action', () => {
    expect(auditLine(null, null)).toBe('Activity');
  });
});

describe('quality dimensions', () => {
  it('gives freshness and timeliness the same human label', () => {
    expect(qualityDimensionLabel('timeliness')).toBe('Freshness');
    expect(qualityDimensionLabel('freshness')).toBe('Freshness');
    expect(qualityDimensionHint('completeness')).toMatch(/days that have demand/i);
  });

  it('falls back to a readable hint rather than the raw key', () => {
    expect(qualityDimensionLabel('unknown_check')).toBe('Unknown check');
    expect(qualityDimensionHint('unknown_check')).toMatch(/data quality check/i);
  });
});

describe('modelLabel', () => {
  it('prefixes the version so a bare UUID is not shown', () => {
    expect(modelLabel('b474cfb0-dce0-49bc-b78f-f6ccc8950ac3')).toBe('version b474cfb0');
  });

  it('handles a missing version', () => {
    expect(modelLabel(null)).toBe('—');
  });
});
