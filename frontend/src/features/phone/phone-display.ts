import { parsePhoneNumberFromString } from 'libphonenumber-js/min';

export type PhoneDisplay = {
  raw: string;
  country: string;
  countryName: string;
  national: string;
};

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] ?? char));
}

function countryName(country: string): string {
  if (!country) return '';
  try {
    const displayNames = new Intl.DisplayNames(['es'], { type: 'region' });
    return displayNames.of(country.toUpperCase()) ?? country.toUpperCase();
  } catch {
    return country.toUpperCase();
  }
}

export function parsePhoneDisplay(value: string): PhoneDisplay {
  const raw = value.trim();
  if (!raw) return { raw: '', country: '', countryName: '', national: '' };
  try {
    const parsed = parsePhoneNumberFromString(raw);
    const country = parsed?.country?.toLowerCase() ?? '';
    return {
      raw,
      country,
      countryName: countryName(country),
      national: parsed?.formatNational() || raw,
    };
  } catch {
    return { raw, country: '', countryName: '', national: raw };
  }
}

export function phoneDisplayMarkup(value: string): string {
  const display = parsePhoneDisplay(value);
  if (!display.raw) return '';
  if (!display.country) return `<span class="phone-display phone-display--plain">${escapeHtml(display.raw)}</span>`;
  const label = display.countryName ? `${display.countryName}: ${display.national}` : display.national;
  return `<span class="phone-display"><span class="phone-display__flag fi fi-${escapeHtml(display.country)}" role="img" aria-label="${escapeHtml(display.countryName || display.country.toUpperCase())}"></span><span>${escapeHtml(display.national)}</span><span class="sr-only">${escapeHtml(label)}</span></span>`;
}
