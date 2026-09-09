/**
 * Shared types for the analysis (diagnosis / evidence) presentation primitives.
 * These are view-model shapes consumed by the presentation components — the
 * page layer maps backend responses into them (frontend only renders; it never
 * re-derives root cause / evidence / diagnosis).
 */

export interface EvidenceItem {
  type: string;
  source: string;
  locator?: string | null;
  content?: unknown;
  metadata?: unknown;
}

export interface DiagnosisView {
  failureType: string | null;
  relatedMetric: string | null;
  severity: string;
  confidence: string;
  evidenceContract: string;
  rootCause: string | null;
  status: 'diagnosed' | 'undetermined';
  reason?: string | null;
  missingEvidence?: string[];
  recordId?: string | null;
  question?: string | null;
  evidence?: EvidenceItem[];
}

export interface RecommendationView {
  action: string;
  priority: number;
  source: string;
}
