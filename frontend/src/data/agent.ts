/**
 * Shared vocabulary for the assistant: the shapes its answers and insights come
 * in, and the opening prompts offered on each screen.
 *
 * **Types and prompts only; no answers.** This module used to carry a table of
 * canned answers (`agentAnswers`), a default reply (`agentFallback`) and a set
 * of proactive cards (`agentInsights`) that named real-looking figures — 164
 * covers, MAE 4.18, forecast `fc_8812`, coverage 89.4% — none of which the live
 * panel ever read. Every question is answered from the workspace's own data via
 * `POST /api/v1/agent/messages`, and the answering shapes are built in
 * `AgentContext`. A fabricated figure that is merely unreferenced is one import
 * away from being on screen again, so it was removed rather than left behind.
 */

export interface AgentSource {
  label: string;
  detail: string;
}

export interface AgentAnswer {
  id: string;
  /** Lower-case keywords matched against the question. */
  match: string[];
  headline: string;
  body: string[];
  sources: AgentSource[];
  /** Optional in-app destination the answer can hand off to. */
  action?: {label: string;route: string;};
  /** Small numeric readouts rendered as chips under the answer. */
  figures?: {label: string;value: string;}[];
}

/** Prompts offered per screen, so the assistant is relevant where it opens. */
export const agentPrompts: Record<string, string[]> = {
  overview: [
  'What should I watch today?',
  'How much should I prepare tomorrow?',
  'Is any source broken?'],

  forecast: [
  'Why did tomorrow’s forecast move?',
  'How confident are you in this range?',
  'What would the 70th percentile change?'],

  analytics: [
  'Which day of the week costs me the most surplus?',
  'Which location is hardest to forecast?',
  'How has demand trended this month?'],

  ingestion: ['What data is missing?', 'Which source is failing?'],
  quality: ['What data is missing?', 'Which issues matter most?'],
  'admin-models': [
  'How accurate is the production model?',
  'Should I retrain this week?',
  'Is coverage holding?'],

  'admin-accuracy': [
  'Which location is hardest to forecast?',
  'Is the forecast getting worse?'],

  'admin-policies': [
  'How much should I prepare tomorrow?',
  'What does lowering the quantile cost me?'],

  'admin-pipeline': ['Which jobs are failing?', 'Is the data drifting?'],
  default: [
  'How much should I prepare tomorrow?',
  'Why did the forecast move?',
  'How accurate is the model?']

};

export interface AgentInsight {
  id: string;
  tone: 'opportunity' | 'risk' | 'signal';
  title: string;
  detail: string;
  route?: string;
}
