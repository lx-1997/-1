/**
 * Normalize publisher names for user-facing copy.
 * Keep the upstream value unchanged; this is display-only branding.
 */
export function normalizePublisherName(value?: string | null): string {
  return String(value || '')
    .replace(/DAO\s*财经/gi, '稻草财经')
    .replace(/DAO/gi, '稻草财经')
    .trim();
}
