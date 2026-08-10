/** Shared helpers for Strategy Lab panels. */

export const SL_INPUT_CLASS = 'input-surface input-focus-glow h-10 w-full rounded-xl border bg-transparent px-3 text-sm';

export const SL_PANEL_CLASS = 'glass-panel px-4 py-4';

export function parseSymbols(value: string): string[] {
  return value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean);
}

export function parsePositiveNumbers(value: string): number[] {
  return value.split(/[\s,]+/).map((item) => Number(item.trim())).filter((item) => Number.isFinite(item) && item > 0);
}

export function formatPct(value?: number | null): string {
  return value == null ? '--' : `${value.toFixed(2)}%`;
}

export function formatNumber(value?: number | null, digits = 2): string {
  return value == null ? '--' : value.toFixed(digits);
}
