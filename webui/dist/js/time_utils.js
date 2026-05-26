// =============================================================================
// time_utils.js — MSK (Europe/Moscow) time formatting for frontend
// =============================================================================

const MSK_OPTIONS = {
  timeZone: 'Europe/Moscow',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
};

const MSK_TIME_ONLY = {
  timeZone: 'Europe/Moscow',
  hour: '2-digit',
  minute: '2-digit',
};

const MSK_DATE_ONLY = {
  timeZone: 'Europe/Moscow',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
};

const _fmtFull = new Intl.DateTimeFormat('ru-RU', MSK_OPTIONS);
const _fmtTime = new Intl.DateTimeFormat('ru-RU', MSK_TIME_ONLY);
const _fmtDate = new Intl.DateTimeFormat('ru-RU', MSK_DATE_ONLY);

/**
 * Format an ISO date string to full MSK datetime (DD.MM.YYYY, HH:mm).
 */
export function formatMSK(isoString) {
  if (!isoString) return '';
  try {
    const d = new Date(isoString);
    if (Number.isNaN(d.getTime())) return isoString;
    return _fmtFull.format(d);
  } catch {
    return isoString;
  }
}

/**
 * Format an ISO date string to MSK time only (HH:mm).
 */
export function formatMSKTime(isoString) {
  if (!isoString) return '';
  try {
    const d = new Date(isoString);
    if (Number.isNaN(d.getTime())) return isoString;
    return _fmtTime.format(d);
  } catch {
    return isoString;
  }
}

/**
 * Format an ISO date string to MSK date only (DD.MM.YYYY).
 */
export function formatMSKDate(isoString) {
  if (!isoString) return '';
  try {
    const d = new Date(isoString);
    if (Number.isNaN(d.getTime())) return isoString;
    return _fmtDate.format(d);
  } catch {
    return isoString;
  }
}
