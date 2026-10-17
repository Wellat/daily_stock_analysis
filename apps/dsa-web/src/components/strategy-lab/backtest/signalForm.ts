/** Signal（ETF 时序信号）策略表单状态与后端 parameters 契约的转换。 */

export type SignalRuleRow = { factor: string; op: string; value: string };

export type SignalFormState = {
  maWindow: string;
  rocWindow: string;
  macdFast: string;
  macdSlow: string;
  macdSignal: string;
  entryRules: SignalRuleRow[];
  exitRules: SignalRuleRow[];
  maxPositionPct: string;
  commissionPermille: string;
  lotSize: string;
};

export const DEFAULT_SIGNAL_FORM: SignalFormState = {
  maWindow: '182',
  rocWindow: '20',
  macdFast: '12',
  macdSlow: '26',
  macdSignal: '9',
  entryRules: [{ factor: 'ma_gap_pct', op: '<', value: '0' }],
  exitRules: [{ factor: 'ma_gap_pct', op: '>', value: '7' }],
  maxPositionPct: '100',
  commissionPermille: '0.2',
  lotSize: '100',
};

export const DEFAULT_MACD_FORM: SignalFormState = {
  ...DEFAULT_SIGNAL_FORM,
  entryRules: [{ factor: 'macd_cross', op: '>', value: '0' }],
  exitRules: [{ factor: 'macd_cross', op: '<', value: '0' }],
};

/** 按策略选择默认表单（signal=均线乖离；signal_macd=金叉买入/死叉卖出）。 */
export function defaultSignalForm(strategyId: string | undefined): SignalFormState {
  return strategyId === 'signal_macd' ? DEFAULT_MACD_FORM : DEFAULT_SIGNAL_FORM;
}

const num = (value: string, fallback: number): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

/** 表单状态 → 后端 parameters（空规则行在提交前清理；同时落扁平标量供批量网格寻优）。 */
export function buildSignalParameters(form: SignalFormState, extra: Record<string, unknown> = {}): Record<string, unknown> {
  const entryRules = form.entryRules
    .filter((row) => row.factor && row.op)
    .map((row) => ({ factor: row.factor, op: row.op, value: num(row.value, 0) }));
  const exitRules = form.exitRules
    .filter((row) => row.factor && row.op)
    .map((row) => ({ factor: row.factor, op: row.op, value: num(row.value, 0) }));
  const allRules = [...entryRules, ...exitRules];
  const uses = (match: (factor: string) => boolean) => allRules.some((row) => match(row.factor));

  const parameters: Record<string, unknown> = {
    max_position_pct: Math.min(100, Math.max(1, num(form.maxPositionPct, 100))),
    // ‰ → 小数（0.2‰ = 0.0002）
    commission: num(form.commissionPermille, 0.2) / 1000,
    lot_size: Math.max(1, Math.round(num(form.lotSize, 100))),
    ...extra,
  };
  // 窗口标量按规则实际引用的因子落键（未引用的因子窗口不参与计算，也避免无谓预热）
  if (uses((factor) => factor === 'ma_gap_pct')) {
    parameters.ma_window = Math.max(2, Math.round(num(form.maWindow, 182)));
  }
  if (uses((factor) => factor === 'roc_pct')) {
    parameters.roc_window = Math.max(1, Math.round(num(form.rocWindow, 20)));
  }
  if (uses((factor) => factor.startsWith('macd_'))) {
    parameters.macd_fast = Math.max(2, Math.round(num(form.macdFast, 12)));
    parameters.macd_slow = Math.max(3, Math.round(num(form.macdSlow, 26)));
    parameters.macd_signal = Math.max(2, Math.round(num(form.macdSignal, 9)));
  }
  if (entryRules.length) parameters.entry_rules = entryRules;
  if (exitRules.length) parameters.exit_rules = exitRules;
  // 扁平标量镜像默认规则（网格寻优轴）：从首条 ma_gap_pct / dividend_yield 规则提取
  const entryGap = entryRules.find((row) => row.factor === 'ma_gap_pct');
  if (entryGap) parameters.entry_gap_max = entryGap.value;
  const dividendMin = entryRules.find((row) => row.factor === 'dividend_yield' && row.op === '>');
  if (dividendMin) parameters.dividend_yield_min = dividendMin.value;
  const exitGap = exitRules.find((row) => row.factor === 'ma_gap_pct' && row.op === '>');
  if (exitGap) parameters.exit_gap_min = exitGap.value;
  return parameters;
}

/** 已保存配置 / 历史 run 的 parameters → 表单状态（容忍缺省与未知键）。 */
export function applySignalParameters(parameters: Record<string, unknown> | undefined | null): SignalFormState {
  const form: SignalFormState = { ...DEFAULT_SIGNAL_FORM };
  if (!parameters || typeof parameters !== 'object') return form;
  const read = (key: string): unknown => parameters[key];
  if (read('ma_window') != null) form.maWindow = String(read('ma_window'));
  if (read('roc_window') != null) form.rocWindow = String(read('roc_window'));
  if (read('macd_fast') != null) form.macdFast = String(read('macd_fast'));
  if (read('macd_slow') != null) form.macdSlow = String(read('macd_slow'));
  if (read('macd_signal') != null) form.macdSignal = String(read('macd_signal'));
  const isMacd = String(read('default_rules') ?? '') === 'macd_cross';
  if (Array.isArray(read('entry_rules'))) {
    form.entryRules = (read('entry_rules') as Array<Record<string, unknown>>).map((row) => ({
      factor: String(row.factor ?? ''),
      op: String(row.op ?? '<'),
      value: String(row.value ?? ''),
    }));
  } else if (isMacd) {
    form.entryRules = [{ ...DEFAULT_MACD_FORM.entryRules[0] }];
  } else {
    // 扁平标量回填（批量网格改的是标量键）
    form.entryRules = [{ factor: 'ma_gap_pct', op: '<', value: String(read('entry_gap_max') ?? 0) }];
    if (read('dividend_yield_min') != null && Number(read('dividend_yield_min')) > 0) {
      form.entryRules.push({ factor: 'dividend_yield', op: '>', value: String(read('dividend_yield_min')) });
    }
  }
  if (Array.isArray(read('exit_rules'))) {
    form.exitRules = (read('exit_rules') as Array<Record<string, unknown>>).map((row) => ({
      factor: String(row.factor ?? ''),
      op: String(row.op ?? '>'),
      value: String(row.value ?? ''),
    }));
  } else if (isMacd) {
    form.exitRules = [{ ...DEFAULT_MACD_FORM.exitRules[0] }];
  } else if (read('exit_gap_min') != null) {
    form.exitRules = [{ factor: 'ma_gap_pct', op: '>', value: String(read('exit_gap_min')) }];
  }
  if (read('max_position_pct') != null) form.maxPositionPct = String(read('max_position_pct'));
  if (read('commission') != null) form.commissionPermille = String(Number(read('commission')) * 1000);
  if (read('lot_size') != null) form.lotSize = String(read('lot_size'));
  return form;
}
