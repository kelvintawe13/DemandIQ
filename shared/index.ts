// Shared types and utilities for DemandIQ monorepo

export interface User {
  id: string;
  name: string;
  email: string;
  role: 'owner' | 'administrator' | 'analyst' | 'operator' | 'viewer';
  initials: string;
}

export interface Location {
  id: string;
  name: string;
  area: string;
  cuisine: string;
  openedOn: string;
}

export interface Forecast {
  id: string;
  locationId: string;
  targetDate: string;
  pointForecast: number;
  lowerBound: number;
  upperBound: number;
  coverageTarget: number;
  modelVersion: string;
  fallback: boolean;
}

export interface DemandObservation {
  id: string;
  locationId: string;
  date: string;
  demandQty: number;
  source: string;
  recordedAt: string;
}

export interface Alert {
  id: string;
  severity: 'critical' | 'warning' | 'info';
  title: string;
  detail: string;
  source: string;
  time: string;
  resolved: boolean;
}

export interface QualityIssue {
  id: string;
  locationId?: string;
  severity: 'critical' | 'warning' | 'info';
  summary: string;
  suggestedFix?: string;
  resolvedAt?: string;
  dismissedAt?: string;
  createdAt: string;
}

export type UserRole = 'owner' | 'administrator' | 'analyst' | 'operator' | 'viewer';
export type ConnectorType = 'pos' | 'momo' | 'file' | 'ocr' | 'api' | 'sms';
export type UploadStatus = 'parsing' | 'awaiting_mapping' | 'ready' | 'committed' | 'failed';
export type ModelStatus = 'training' | 'candidate' | 'production' | 'archived' | 'failed';
