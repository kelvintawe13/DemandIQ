export interface LegalSection {
  heading: string;
  body: string[];
}

export interface LegalDocument {
  id: 'privacy';
  label: string;
  title: string;
  updated: string;
  summary: string;
  sections: LegalSection[];
}

export const legalDocuments: LegalDocument[] = [
{
  id: 'privacy',
  label: 'Privacy Policy and Terms of Use',
  title: 'DemandIQ Privacy Policy and Terms of Use',
  updated: 'October 29, 2026',
  summary:
  'DemandIQ is a research prototype built by Kelvin Tawe as a capstone project at African Leadership University, Kigali, Rwanda. It helps small food businesses forecast daily demand. Questions about this page: k.tawe@alustudent.com.',
  sections: [
  {
    heading: 'What data DemandIQ handles',
    body: [
    'Demand records. The date, the number of customers or sales, the location name, and the channel the record came from (manual entry, file, photo, till device or connector). These are business figures, not details about your customers.',
    'Files and photos you upload. Spreadsheets and photos of paper logs. A photo can show handwriting, so please do not upload pages that contain customer names or phone numbers.',
    'Device and connector keys. Each till device and each connector has its own key, so DemandIQ knows which location a record belongs to.',
    'Account details. Your name, email and workspace, so you can sign in and see only your own data.',
    'From tills and connectors, DemandIQ keeps only the date, the quantity and the channel. Any other fields they send are dropped.']

  },
  {
    heading: 'Why we use it',
    body: [
    'We use your records to show a forecast of next-day demand, a range around that forecast, a comparison of preparation choices, and an explanation of what drove each forecast. We do not use your data for advertising, to rate your staff, or to sell to anyone.']

  },
  {
    heading: 'How photo reading works',
    body: [
    'Photos are read on our own server by default. If your workspace turns on the optional outside vision service, the photo is sent to that service to be read, and it may be processed outside Rwanda. The service is off unless you switch it on, and DemandIQ tells you before it sends anything.',
    'Every row read from a photo waits for your review. Nothing is saved until you confirm it. DemandIQ never invents a row: if it cannot read something, it returns nothing and says so.']

  },
  {
    heading: 'Your control',
    body: [
    'You can edit any row before saving it. You can undo an import, which removes the rows it created. You can download your demand records. You can ask us to delete your account and data.']

  },
  {
    heading: 'Who can see your data',
    body: [
    'Only people in your workspace. We do not sell data or share it between workspaces. We share data only with the hosting provider that runs DemandIQ and, if you switch it on, the outside vision service.']

  },
  {
    heading: 'How long we keep it',
    body: [
    'We keep your records while your account is active. Photos are deleted when you delete the import they belong to, and in any case 30 days after upload. If you ask us to delete your data, we do it within 30 days.']

  },
  {
    heading: 'Your rights',
    body: [
    'Under Rwanda\'s Law No. 058/2021 of 13 October 2021 on the protection of personal data and privacy, you may ask to see your data, correct it, delete it, restrict or object to its use, receive it in a portable form, withdraw your consent, and not be subject to a decision made only by automated means. Email us to use any of these rights, and we will reply within 30 days. You may also complain to the National Cyber Security Authority.']

  },
  {
    heading: 'Security and children',
    body: [
    'Each device has its own key. All records are written through one checked path that refuses future dates and impossible quantities. DemandIQ is for business use and is not meant for children under 16.']

  },
  {
    heading: 'Terms of use',
    body: [
    'Forecasts are estimates. DemandIQ is trained and tested on public restaurant data from Japan. It can be wrong, especially on very busy days and for businesses unlike those restaurants. Use your own judgment before you buy or prepare food.',
    'Upload only what you may share. Do not upload other people\'s personal information without their permission.',
    'No warranty. DemandIQ is a student prototype, provided as it is, without guarantees of accuracy or availability.',
    'Changes. If this page changes, the date above changes and the app shows a notice.']

  }]

}];
