export interface LandingFeature {
  id: string;
  title: string;
  description: string;
}

export const landingFeatures: LandingFeature[] = [
{
  id: 'forecast',
  title: 'Explainable daily forecasts',
  description:
  "Gradient-boosted models predict tomorrow's customers per location, and show you which past days, weekdays and events drove the number."
},
{
  id: 'prep',
  title: 'Prep decisions, not just predictions',
  description:
  'Every forecast carries a likely range that turns into a concrete prep quantity, so you can choose how much shortfall risk you are willing to carry.'
},
{
  id: 'ingest',
  title: 'Works with the records you keep',
  description:
  'Mobile money statements, POS exports, spreadsheets or a photo of the notebook all land in the same daily-demand record.'
}];


export interface LandingStep {
  id: string;
  title: string;
  description: string;
}

export const landingSteps: LandingStep[] = [
{
  id: 'connect',
  title: 'Bring your history',
  description:
  "Connect MoMo or POS, upload a spreadsheet, or photograph handwritten records. Two weeks of history is enough to begin."
},
{
  id: 'forecast',
  title: 'Get a forecast with a range',
  description:
  "Each morning you get tomorrow's expected demand, the likely range around it, and the factors behind it."
},
{
  id: 'act',
  title: 'Choose your prep policy',
  description:
  'Decide how much shortfall risk to carry. The platform converts the forecast into a prep quantity and tracks how it performed.'
}];


export const heroForecast = {
  location: 'Sample Kitchen',
  date: 'Tomorrow · Demo',
  point: 120,
  lower: 95,
  upper: 145,
  prep: 130,
  coverage: 90,
  drivers: [
  { label: 'Weekend pattern', impact: '+15' },
  { label: 'Weather data', impact: '+5' },
  { label: 'Historical trend', impact: '+8' }],

  /** Relative heights for the 7-day sparkline behind the number. */
  trend: [45, 55, 50, 65, 60, 75, 70]
};

export interface EvidencePoint {
  id: string;
  value: string;
  label: string;
  source: string;
}

/** Published figures that motivate the product. Sourced, not self-reported.
 *  Every value and its wording come from the UNEP Food Waste Index Report 2024
 *  (food waste in 2022: 1.05bn tonnes; food service 28% of it; ~19% of all food
 *  available to consumers wasted; over US$1 trillion a year by value). The
 *  earlier figures here were from the 2021 edition and the "Research" tile was
 *  an unsourced placeholder dressed up as a statistic. */
export const evidencePoints: EvidencePoint[] = [
{
  id: 'global',
  value: '1.05bn',
  label: 'tonnes of food wasted in 2022, worldwide',
  source: 'UNEP Food Waste Index Report 2024'
},
{
  id: 'foodservice',
  value: '28%',
  label: 'of that waste comes from the food service sector',
  source: 'UNEP Food Waste Index Report 2024'
},
{
  id: 'systemic',
  value: '19%',
  label: 'of all food available to consumers is wasted',
  source: 'UNEP Food Waste Index Report 2024'
},
{
  id: 'economic',
  value: '$1T+',
  label: 'of food wasted worldwide each year, by value',
  source: 'UNEP Food Waste Index Report 2024'
}];