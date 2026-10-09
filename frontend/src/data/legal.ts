export interface LegalSection {
  heading: string;
  body: string[];
}

export interface LegalDocument {
  id: 'terms' | 'privacy' | 'cookies';
  label: string;
  title: string;
  updated: string;
  summary: string;
  sections: LegalSection[];
}

export const legalDocuments: LegalDocument[] = [
{
  id: 'terms',
  label: 'Terms of Service',
  title: 'Terms of Service',
  updated: '18 September 2026',
  summary:
  'What you can expect from DemandIQ, and what we expect from you when you use it.',
  sections: [
  {
    heading: '1. What DemandIQ is',
    body: [
    'DemandIQ is a decision-support service. It forecasts daily demand for your locations, expresses the uncertainty around each forecast, and compares preparation policies so you can decide how much to prepare or purchase.',
    'DemandIQ does not make operational decisions for you. Every forecast, interval and policy suggestion is advisory, and the final preparation, purchasing and staffing decision remains yours.']

  },
  {
    heading: '2. Your account',
    body: [
    'You must provide accurate account information and keep your credentials confidential. You are responsible for activity carried out under your account, including by team members you invite.',
    'Workspace administrators can change roles, revoke access and view the audit log. If you believe an account has been compromised, revoke its sessions in Settings → Security and contact us immediately.']

  },
  {
    heading: '3. Your data',
    body: [
    'You retain ownership of the demand records, transactions, uploads and business information you send to DemandIQ. You grant us the limited licence needed to process that data in order to operate the service for you.',
    'We do not sell your data, and we do not use one customer’s operational data to serve another customer without aggregation and de-identification.']

  },
  {
    heading: '4. Acceptable use',
    body: [
    'Do not upload data you have no right to share, attempt to reverse engineer the models, resell forecasts as your own service, or use the API in a way that degrades the service for others.',
    'API keys are scoped. Using a key beyond its scope, or sharing it outside your organisation, is a breach of these terms.']

  },
  {
    heading: '5. Accuracy and limitation of liability',
    body: [
    'Forecasts are statistical estimates. Even a well-calibrated 90% interval is expected to miss roughly one day in ten, and surplus figures shown in the product are simulated exposure, not measured food waste.',
    'To the maximum extent permitted by law, DemandIQ is not liable for indirect or consequential losses, including lost profit or spoiled stock, arising from decisions made using the service.']

  },
  {
    heading: '6. Plans, billing and cancellation',
    body: [
    'Paid plans are billed monthly or annually in advance. You can change or cancel a plan at any time; cancellation takes effect at the end of the current billing period and no partial refunds are issued.',
    'We will give at least 30 days’ notice before any price change affecting your workspace.']

  },
  {
    heading: '7. Ending your use of the service',
    body: [
    'You may close your workspace at any time. We may suspend an account for non-payment or a serious breach of these terms, with notice where practical.',
    'On closure you can export your demand history for 30 days, after which it is deleted from production systems and purged from backups within 90 days.']

  }]

},
{
  id: 'privacy',
  label: 'Privacy Policy',
  title: 'Privacy Policy',
  updated: '18 September 2026',
  summary:
  'What we collect, why we collect it, how long we keep it, and the choices you have.',
  sections: [
  {
    heading: 'What we collect',
    body: [
    'Account data: your name, work email, role, workspace, and authentication details including two-factor enrolment.',
    'Operational data: the demand records you ingest (dates, covers or transaction counts, locations, and any notes you add). Connector data from POS and mobile money sources is limited to transaction totals and timestamps.',
    'Usage data: pages visited, features used, approximate location derived from IP, device and browser type. This is used to keep the service secure and to prioritise what we build.']

  },
  {
    heading: 'What we deliberately do not collect',
    body: [
    'We do not collect your customers’ names, phone numbers or payment instruments. Mobile money and POS connectors read aggregate transaction counts, never individual payer identities.',
    'We do not record audio or video, and we do not use uploaded photographs of handwritten records for anything other than extracting the dates and counts written on them.']

  },
  {
    heading: 'Why we process it',
    body: [
    'To provide the service: producing forecasts, intervals, policy comparisons and reports for your workspace.',
    'To improve models: training and evaluating forecasting models. Cross-customer model work uses aggregated, de-identified data only.',
    'To keep accounts secure: detecting abuse, enforcing rate limits, and maintaining the audit log administrators rely on.']

  },
  {
    heading: 'Research datasets',
    body: [
    'The published research datasets used to develop our methodology (the Recruit restaurant visitor dataset and the URBAN-WASTE food tracking dataset) are third-party datasets used under their own access conditions. They contain no customer data, and they are never joined to your operational records.']

  },
  {
    heading: 'Retention',
    body: [
    'Demand history is kept for as long as your workspace is active, because forecasting requires history. Audit logs are kept for 12 months. Usage analytics are kept for 14 months.',
    'On workspace closure, production data is deleted within 30 days and purged from backups within 90 days.']

  },
  {
    heading: 'Your rights',
    body: [
    'You can access, correct, export or delete your personal data from Settings, or by contacting privacy@demandiq.app. We respond to requests within 30 days.',
    'Where processing relies on consent (such as non-essential analytics cookies), you can withdraw that consent at any time without affecting your use of the service.']

  },
  {
    heading: 'Where data is held',
    body: [
    'Data is hosted in the region selected for your workspace. Sub-processors are limited to infrastructure, email delivery and error monitoring providers, each bound by data-processing agreements.']

  }]

},
{
  id: 'cookies',
  label: 'Cookie Policy',
  title: 'Cookie Policy',
  updated: '18 September 2026',
  summary:
  'The small number of cookies DemandIQ sets, and which of them you can turn off.',
  sections: [
  {
    heading: 'Strictly necessary',
    body: [
    'Session cookie: keeps you signed in and ties requests to your workspace. Expires when you sign out, or after 30 days on a trusted device.',
    'Security cookie: supports bot protection and cross-site request forgery defences on sign-in and sign-up.',
    'These cannot be turned off, because the service cannot function without them.']

  },
  {
    heading: 'Preferences',
    body: [
    'Stores your theme (light or dark), interface language, sidebar state, and the locations or date range you last had selected, so the product opens the way you left it.',
    'Turning these off will not break anything; your choices simply will not persist between visits.']

  },
  {
    heading: 'Analytics',
    body: [
    'Aggregated, first-party product analytics: which screens are used, where flows are abandoned, and how long forecasts take to load. Used only to improve the product.',
    'These are optional and are off until you accept them.']

  },
  {
    heading: 'What we do not use',
    body: [
    'No advertising cookies, no cross-site tracking pixels, and no third-party data brokers. We do not build advertising profiles of you or your business.']

  },
  {
    heading: 'Changing your mind',
    body: [
    'Your cookie choice is stored on this device and can be changed at any time from Settings → Preferences, or from the Cookie Policy link in the footer.']

  }]

}];