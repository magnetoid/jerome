export function money(value: string): string {
  const n = Number(value);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : `$${value}`;
}

export function relativeTime(iso: string, now: number = Date.now()): string {
  const secs = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} d ago`;
  return new Date(iso).toLocaleDateString();
}

export const VARIANTS = [
  { value: "en-US", label: "English (US)" },
  { value: "en-GB", label: "English (GB)" },
  { value: "de-DE", label: "German (DE)" },
  { value: "de-AT", label: "German (AT)" },
  { value: "de-CH", label: "German (CH)" },
] as const;

export function variantLabel(v: string): string {
  return VARIANTS.find((x) => x.value === v)?.label ?? v;
}

export const DEFAULT_QUOTES: Record<string, string> = {
  "en-US": "“”‘’",
  "en-GB": "‘’“”",
  "de-DE": "„“‚‘",
  "de-AT": "„“‚‘",
  "de-CH": "«»‹›",
};

export const QUOTE_PRESETS = [
  { value: "“”‘’", label: "English double “”‘’" },
  { value: "‘’“”", label: "English single ‘’“”" },
  { value: "„“‚‘", label: "German „“‚‘" },
  { value: "«»‹›", label: "Guillemets «»‹›" },
  { value: "»«›‹", label: "Reversed guillemets »«›‹" },
];

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
