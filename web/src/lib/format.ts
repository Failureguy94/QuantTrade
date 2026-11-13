/**
 * Display formatting. Amounts arrive from the API as exact decimal strings; they
 * are converted to numbers only here, for display.
 */

const DASH = "—";

export function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatMoney(
  value: string | number | null | undefined,
  currency = "USD",
  options: { compact?: boolean; sign?: boolean } = {},
): string {
  const amount = toNumber(value);
  if (amount === null) return DASH;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    notation: options.compact ? "compact" : "standard",
    maximumFractionDigits: 2,
    signDisplay: options.sign ? "exceptZero" : "auto",
  }).format(amount);
}

export function formatNumber(
  value: string | number | null | undefined,
  maximumFractionDigits = 2,
): string {
  const amount = toNumber(value);
  if (amount === null) return DASH;
  return new Intl.NumberFormat("en-US", { maximumFractionDigits }).format(amount);
}

/** A fraction (0.1234) as a percentage ("12.34%"). */
export function formatPercent(
  value: number | null | undefined,
  options: { digits?: number; sign?: boolean } = {},
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return new Intl.NumberFormat("en-US", {
    style: "percent",
    minimumFractionDigits: options.digits ?? 2,
    maximumFractionDigits: options.digits ?? 2,
    signDisplay: options.sign ? "exceptZero" : "auto",
  }).format(value);
}

export function formatRatio(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return value.toFixed(digits);
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return DASH;
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(value));
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return DASH;
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** "sma_crossover" or "SMA_CROSSOVER" to "Sma crossover". */
export function humanize(value: string): string {
  const words = value.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const STRATEGY_LABELS: Record<string, string> = {
  BUY_AND_HOLD: "Buy and hold",
  SMA_CROSSOVER: "SMA crossover",
  MOMENTUM: "Momentum",
  MEAN_REVERSION: "Mean reversion",
};

/** Short display name of a strategy type; unknown types fall back to their code. */
export function strategyLabel(type: string): string {
  return STRATEGY_LABELS[type.toUpperCase()] ?? humanize(type);
}

/** Tailwind text colour for a gain or a loss. */
export function signClass(value: string | number | null | undefined): string {
  const amount = toNumber(value);
  if (amount === null || amount === 0) return "";
  // Shades chosen to meet WCAG AA contrast on both the light and dark backgrounds.
  return amount > 0 ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400";
}

/** Drawdown series (fractions <= 0) from an equity series. */
export function drawdowns(equity: number[]): number[] {
  let peak = -Infinity;
  return equity.map((value) => {
    peak = Math.max(peak, value);
    return peak > 0 ? value / peak - 1 : 0;
  });
}
