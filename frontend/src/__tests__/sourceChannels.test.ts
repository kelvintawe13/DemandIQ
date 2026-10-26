import { describe, it, expect } from 'vitest';
import {
  importKindLabel,
  importKindOf,
  kindLabel,
  syncBlockedReason,
} from '../lib/sourceChannels';

// One vocabulary for channels, shared by the Sources page and the ingestion
// page's POS / Mobile Money tab. `manual` arrived with the channels ingestion
// now registers itself, and had no name until this mapping existed.

describe('source channel vocabulary', () => {
  it('names a hand-entry channel rather than showing the raw token', () => {
    expect(kindLabel('manual')).toBe('Manual entry');
    expect(kindLabel('file')).toBe('Spreadsheet');
    expect(kindLabel('ocr')).toBe('Image OCR');
  });

  it('reads the import kind only when it is a non-empty string', () => {
    expect(importKindOf({ import_kind: 'file' })).toBe('file');
    expect(importKindOf({ import_kind: null })).toBeNull();
    expect(importKindOf({ import_kind: '' })).toBeNull();
    expect(importKindOf({})).toBeNull();
    expect(importKindOf(null)).toBeNull();
  });

  it('words each import kind as what actually fills the channel', () => {
    expect(importKindLabel('file')).toBe('spreadsheet uploads');
    expect(importKindLabel('image')).toBe('photo uploads');
    expect(importKindLabel('manual')).toBe('manual entry');
    expect(importKindLabel('device')).toBe('till pushes');
  });

  it('blocks a sync with the channel’s own reason', () => {
    // An import channel has no endpoint to pull.
    expect(syncBlockedReason({ status: 'available', type: 'manual', import_kind: 'manual' })).toBe(
      'Filled by manual entry — there is nothing to pull',
    );
    // A connector nobody has activated is a different case, and gets the advice
    // that can actually fix it.
    expect(syncBlockedReason({ status: 'paused', type: 'pos', import_kind: null })).toBe(
      'Only an active channel can be pulled — an administrator activates it on Connectors',
    );
    // An active channel is syncable and carries no reason at all.
    expect(syncBlockedReason({ status: 'active', type: 'pos' })).toBeUndefined();
  });
});
