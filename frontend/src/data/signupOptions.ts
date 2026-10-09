export const businessTypes = [
{ value: 'restaurant', label: 'Restaurant' },
{ value: 'cafeteria', label: 'Cafeteria / institutional kitchen' },
{ value: 'catering', label: 'Catering service' },
{ value: 'bakery', label: 'Bakery' },
{ value: 'vendor', label: 'Fresh-food vendor / market stall' },
{ value: 'other', label: 'Other food business' }];


export const locationCounts = [
{ value: '1', label: '1 location' },
{ value: '2-5', label: '2–5 locations' },
{ value: '6-20', label: '6–20 locations' },
{ value: '20+', label: 'More than 20' }];


export const coverBands = [
{ value: '<50', label: 'Under 50 customers' },
{ value: '50-150', label: '50–150 customers' },
{ value: '150-400', label: '150–400 customers' },
{ value: '400+', label: 'Over 400 customers' }];


export const dataSources = [
{ id: 'pos', label: 'POS system' },
{ id: 'momo', label: 'Mobile money' },
{ id: 'spreadsheet', label: 'Spreadsheet' },
{ id: 'handwritten', label: 'Handwritten records' },
{ id: 'none', label: 'Nothing structured yet' }];


export const roles = [
{ value: 'owner', label: 'Owner / founder' },
{ value: 'operations', label: 'Operations manager' },
{ value: 'chef', label: 'Head chef / kitchen lead' },
{ value: 'analyst', label: 'Analyst / data' },
{ value: 'other', label: 'Other' }];


export function passwordScore(value: string): {score: 0 | 1 | 2 | 3;label: string;} {
  let hits = 0;
  if (value.length >= 8) hits += 1;
  if (/[A-Z]/.test(value) && /[a-z]/.test(value)) hits += 1;
  if (/\d/.test(value) || /[^\w\s]/.test(value)) hits += 1;
  if (value.length === 0) return { score: 0, label: '' };
  if (hits <= 1) return { score: 1, label: 'Weak' };
  if (hits === 2) return { score: 2, label: 'Fair' };
  return { score: 3, label: 'Strong' };
}