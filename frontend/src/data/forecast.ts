/**
 * The shape the forecast charts render.
 *
 * **Type only; no series.** This module used to carry placeholder series
 * (`forecastSeries`, `forecastRows`, `xAxisTicks`) and marketing-flavoured
 * insights (`aiInsights`) full of invented figures — dates in Jun 2025,
 * `42,150` predicted, "+14.6% over 30 days". The chart reads its points from the
 * live forecast API; only this type was ever imported. Those fixtures were
 * removed rather than left behind, because an unreferenced fabricated figure is
 * one import away from being rendered.
 */

export interface ForecastPoint {
  label: string;
  historical: number | null;
  forecast: number | null;
  band: [number, number] | null;
}
