/** Rotation 策略表单状态与后端 parameters 契约的转换。 */

export type ScoreFactorRow = { factor: string; direction: 'asc' | 'desc'; weight: string };
export type ExclusionFactorRow = { factor: string; op: string; value: string };

export type RotationFormState = {
  rebalanceUnit: 'trading_day' | 'week' | 'month';
  rebalanceInterval: string;
  minPositions: string;
  maxPositions: string;
  maxPositionPct: string;
  scorePreset: string;
  scoreFactors: ScoreFactorRow[];
  exclusionFactors: ExclusionFactorRow[];
  scoreMissing: 'skip' | 'neutral';
  excludeNewBondDays: string;
  excludeLastTradingDays: string;
  excludedSymbols: string;
  excludeEventBlocked: boolean;
  rebalanceWeights: boolean;
  commissionPermille: string;
  lotSize: string;
};

export const DEFAULT_ROTATION_FORM: RotationFormState = {
  rebalanceUnit: 'trading_day',
  rebalanceInterval: '1',
  minPositions: '1',
  maxPositions: '5',
  maxPositionPct: '100',
  scorePreset: 'double_low',
  scoreFactors: [
    { factor: 'price', direction: 'asc', weight: '1' },
    { factor: 'premium_rate', direction: 'asc', weight: '1' },
  ],
  exclusionFactors: [],
  scoreMissing: 'skip',
  excludeNewBondDays: '0',
  excludeLastTradingDays: '0',
  excludedSymbols: '',
  excludeEventBlocked: true,
  rebalanceWeights: false,
  commissionPermille: '0.2',
  lotSize: '10',
};

const num = (value: string, fallback: number): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

/** 表单状态 → 后端 parameters（空排除/打分行在提交前清理）。 */
export function buildRotationParameters(form: RotationFormState, extra: Record<string, unknown> = {}): Record<string, unknown> {
  const parameters: Record<string, unknown> = {
    rebalance_unit: form.rebalanceUnit,
    rebalance_interval: Math.max(1, Math.round(num(form.rebalanceInterval, 1))),
    min_positions: Math.max(1, Math.round(num(form.minPositions, 1))),
    max_positions: Math.max(1, Math.round(num(form.maxPositions, 5))),
    max_position_pct: Math.min(100, Math.max(1, num(form.maxPositionPct, 100))),
    score_preset: form.scorePreset,
    score_factors: form.scoreFactors
      .filter((row) => row.factor)
      .map((row) => ({ factor: row.factor, direction: row.direction, weight: num(row.weight, 1) })),
    score_missing: form.scoreMissing,
    exclude_new_bond_days: Math.max(0, Math.round(num(form.excludeNewBondDays, 0))),
    exclude_last_trading_days: Math.max(0, Math.round(num(form.excludeLastTradingDays, 0))),
    excluded_symbols: form.excludedSymbols
      .split(/[\s,，]+/)
      .map((item) => item.trim())
      .filter(Boolean),
    exclude_event_blocked: form.excludeEventBlocked,
    rebalance_weights: form.rebalanceWeights,
    // ‰ → 小数（0.2‰ = 0.0002）
    commission: num(form.commissionPermille, 0.2) / 1000,
    lot_size: Math.max(1, Math.round(num(form.lotSize, 10))),
    ...extra,
  };
  const exclusionFactors = form.exclusionFactors
    .filter((row) => row.factor && row.op)
    .map((row) => ({ factor: row.factor, op: row.op, value: num(row.value, 0) }));
  if (exclusionFactors.length) {
    parameters.exclusion_factors = exclusionFactors;
  }
  return parameters;
}

/** 已保存配置 / 历史 run 的 parameters → 表单状态（容忍缺省与未知键）。 */
export function applyRotationParameters(parameters: Record<string, unknown> | undefined | null): RotationFormState {
  const form: RotationFormState = { ...DEFAULT_ROTATION_FORM };
  if (!parameters || typeof parameters !== 'object') return form;
  const read = (key: string): unknown => parameters[key];
  if (typeof read('rebalance_unit') === 'string') form.rebalanceUnit = read('rebalance_unit') as RotationFormState['rebalanceUnit'];
  if (read('rebalance_interval') != null) form.rebalanceInterval = String(read('rebalance_interval'));
  if (read('min_positions') != null) form.minPositions = String(read('min_positions'));
  if (read('max_positions') != null) form.maxPositions = String(read('max_positions'));
  if (read('max_position_pct') != null) form.maxPositionPct = String(read('max_position_pct'));
  if (typeof read('score_preset') === 'string') form.scorePreset = String(read('score_preset'));
  if (Array.isArray(read('score_factors'))) {
    form.scoreFactors = (read('score_factors') as Array<Record<string, unknown>>).map((row) => ({
      factor: String(row.factor ?? ''),
      direction: row.direction === 'desc' ? 'desc' : 'asc',
      weight: String(row.weight ?? 1),
    }));
  }
  if (Array.isArray(read('exclusion_factors'))) {
    form.exclusionFactors = (read('exclusion_factors') as Array<Record<string, unknown>>).map((row) => ({
      factor: String(row.factor ?? ''),
      op: String(row.op ?? '<='),
      value: String(row.value ?? ''),
    }));
  }
  if (read('score_missing') === 'neutral') form.scoreMissing = 'neutral';
  if (read('exclude_new_bond_days') != null) form.excludeNewBondDays = String(read('exclude_new_bond_days'));
  if (read('exclude_last_trading_days') != null) form.excludeLastTradingDays = String(read('exclude_last_trading_days'));
  if (Array.isArray(read('excluded_symbols'))) form.excludedSymbols = (read('excluded_symbols') as string[]).join(',');
  if (typeof read('exclude_event_blocked') === 'boolean') form.excludeEventBlocked = read('exclude_event_blocked') as boolean;
  if (typeof read('rebalance_weights') === 'boolean') form.rebalanceWeights = read('rebalance_weights') as boolean;
  if (read('commission') != null) form.commissionPermille = String(Number(read('commission')) * 1000);
  if (read('lot_size') != null) form.lotSize = String(read('lot_size'));
  return form;
}
