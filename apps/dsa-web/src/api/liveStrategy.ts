import apiClient from './index';
import { toCamelCase } from './utils';

/**实盘下单账户白名单（与后端 LIVE_ACCOUNTS 一致）；如需扩展先改后端。*/
export const LIVE_ACCOUNT_OPTIONS = [
  { value: 'testS', label: 'testS（模拟）' },
  { value: '135129739', label: '135129739' },
];

/**策略参数键与元数据定义一致（snake_case），不做深度驼峰转换，否则存取对不上。*/
function toConfigPayload(data: unknown): LiveStrategyConfig {
  const { parameters, ...rest } = (data ?? {}) as Record<string, unknown>;
  return { ...toCamelCase<Omit<LiveStrategyConfig, 'parameters'>>(rest), parameters: (parameters ?? {}) as Record<string, unknown> };
}

export type LiveStrategyInstanceLastRun = { id: number; tradeDate: string; status: string; mode?: string; completedAt?: string | null };
export type LiveStrategyConfig = { id?: number; name?: string; description?: string | null; strategyId: string; strategyVersion: string; qmtAccount: string; enabled: boolean; symbols: string[]; parameters: Record<string, unknown>; rebalanceFrequencyDays?: number; eventCheckEnabled?: boolean; dataSyncBeforeRun?: boolean; lastTradingDayExitEnabled?: boolean; lastTradingDayExitBufferDays?: number; scheduleTime?: string; nextRebalanceDate?: string | null; lastRun?: LiveStrategyInstanceLastRun | null };
export type LiveStrategyInstanceRequest = { name: string; description?: string | null; strategyId: string; qmtAccount?: string | null; enabled: boolean; symbols: string[]; parameters: Record<string, unknown>; scheduleTime?: string | null; rebalanceFrequencyDays?: number | null; eventCheckEnabled?: boolean | null; dataSyncBeforeRun?: boolean | null; lastTradingDayExitEnabled?: boolean | null; lastTradingDayExitBufferDays?: number | null };
export type LiveStrategyRunMode = 'auto' | 'rebalance' | 'event_check';
/** live_mode：order=实盘交易下单；notify=只产信号通知；缺省=仅回测 */
export type LiveStrategyDefinition = { strategyId: string; strategyVersion?: string; name: string; description: string; instrumentTypes?: string[]; markets?: string[]; liveMode?: 'order' | 'notify' | null; parameters?: Array<{ key: string; label: string; type: string; default?: unknown; min?: number; max?: number; options?: string[] }> };
export type LiveStrategyDiagnostics = { skipped?: Array<{ symbol?: string; reason: string }>; riskChecks?: Array<{ name: string; passed: boolean; detail?: string }> };
export type LiveStrategyPreview = { tradeDate: string; mode?: string; target: Record<string, { symbol: string; price: number; quantity: number }>; current: Record<string, number>; rebalance: Array<{ symbol: string; symbolName?: string | null; premiumRate?: number | null; side: string; quantity: number; reason: string }>; strategyVersion: string; diagnostics?: LiveStrategyDiagnostics; skipReason?: string | null };
export type LiveStrategyRun = { id: number; tradeDate: string; runUid: string; status: string; mode?: string; strategyId?: string; strategyVersion?: string; qmtAccount?: string; decisionCount?: number; orderCount?: number; skipReason?: string | null; dataSnapshotAt?: string | null; completedAt?: string | null; target?: Record<string, { symbol: string; symbolName?: string; price?: number | null; quantity: number }>; current?: Record<string, number>; rebalance?: LiveStrategyPreview['rebalance']; risk?: { passed?: boolean; skipped?: Array<{ symbol?: string; reason: string }>; riskChecks?: Array<{ name: string; passed: boolean; detail?: string }> }; errorMessage?: string | null };
export type LiveStrategyDecision = { id: number; decisionUid?: string; mode?: string; action: string; symbol: string; symbolName?: string | null; targetAmount?: number | null; suggestedQuantity?: number | null; reason?: string | null; riskStatus?: string | null; decisionData?: Record<string, unknown> };
export type LiveStrategyOrder = { id: number; orderUid: string; symbol: string; symbolName?: string | null; side: string; quantity: number; status: string; decisionId?: number | null; qmtOrderId?: string | null; filledQuantity?: number | null; filledPrice?: number | null; submittedAt?: string | null; completedAt?: string | null; errorMessage?: string | null };
export type LiveStrategyBatch = { id: number; batchUid: string; runId: number; qmtAccount: string; status: string; summary?: { count?: number }; createdAt?: string | null; tradeDate?: string | null; mode?: string | null; orders?: { total: number; pending: number; submitted: number; filled: number; rejected: number; cancelled: number } };
export type LiveStrategySyncStatus = { tradeDate: string; intraday?: { status?: string; qualityStatus?: string; completedAt?: string; errorMessage?: string } | null; afterClose?: { status?: string; qualityStatus?: string; completedAt?: string; errorMessage?: string } | null };
export type NotifySignalItem = { symbol: string; name?: string; price?: number; changePct?: number | null; factors?: Record<string, number | null>; signal?: 'buy' | 'sell' | null; reason?: string; holding?: boolean; status?: string; hfqScaled?: boolean };
export type NotifyRunResult = { runId?: number; tradeDate: string; status?: string; feishuSent?: boolean | null; signals?: NotifySignalItem[]; message?: string | null; errorMessage?: string | null };

export const liveStrategyApi = {
  async getConfig(): Promise<LiveStrategyConfig> { const { data } = await apiClient.get('/api/v1/live-strategy/config'); return toConfigPayload(data); },
  async listConfigs(): Promise<LiveStrategyConfig[]> { const { data } = await apiClient.get('/api/v1/live-strategy/configs'); const items = (((data ?? {}) as { items?: unknown[] }).items ?? []) as unknown[]; return items.map((item) => toConfigPayload(item)); },
  async createInstance(payload: LiveStrategyInstanceRequest): Promise<LiveStrategyConfig> { const { data } = await apiClient.post('/api/v1/live-strategy/configs', this.toInstancePayload(payload)); return toConfigPayload(data); },
  async updateInstance(configId: number, payload: LiveStrategyInstanceRequest): Promise<LiveStrategyConfig> { const { data } = await apiClient.put(`/api/v1/live-strategy/configs/${configId}`, this.toInstancePayload(payload)); return toConfigPayload(data); },
  async deleteInstance(configId: number): Promise<{ deleted: boolean; id: number }> { const { data } = await apiClient.delete(`/api/v1/live-strategy/configs/${configId}`); return data; },
  /** 手动立即运行：order → 实盘交易（真实下单）；notify → 信号通知；返回各自的运行结果 */
  async runInstance(configId: number): Promise<NotifyRunResult | LiveStrategyPreview> { const { data } = await apiClient.post(`/api/v1/live-strategy/configs/${configId}/run`); return toCamelCase(data); },
  /**参数键与元数据一致（snake_case），仅字段名做下划线转换。*/
  toInstancePayload(payload: LiveStrategyInstanceRequest): Record<string, unknown> {
    return {
      name: payload.name, description: payload.description ?? null, strategy_id: payload.strategyId,
      qmt_account: payload.qmtAccount ?? null, enabled: payload.enabled, symbols: payload.symbols,
      parameters: payload.parameters, schedule_time: payload.scheduleTime ?? null,
      rebalance_frequency_days: payload.rebalanceFrequencyDays ?? null,
      event_check_enabled: payload.eventCheckEnabled ?? null,
      data_sync_before_run: payload.dataSyncBeforeRun ?? null,
      last_trading_day_exit_enabled: payload.lastTradingDayExitEnabled ?? null,
      last_trading_day_exit_buffer_days: payload.lastTradingDayExitBufferDays ?? null,
    };
  },
  async listStrategies(): Promise<LiveStrategyDefinition[]> { const { data } = await apiClient.get('/api/v1/live-strategy/strategies'); return toCamelCase<{ items: LiveStrategyDefinition[] }>(data).items; },
  async listRuns(): Promise<LiveStrategyRun[]> { const { data } = await apiClient.get('/api/v1/live-strategy/runs'); return toCamelCase<{ items: LiveStrategyRun[] }>(data).items; },
  async listBatches(): Promise<LiveStrategyBatch[]> { const { data } = await apiClient.get('/api/v1/live-strategy/batches'); return toCamelCase<{ items: LiveStrategyBatch[] }>(data).items; },
  async syncStatus(): Promise<LiveStrategySyncStatus> { const { data } = await apiClient.get('/api/v1/live-strategy/data-sync/status'); return toCamelCase<LiveStrategySyncStatus>(data); },
  async listDecisions(runId: number): Promise<LiveStrategyDecision[]> { const { data } = await apiClient.get(`/api/v1/live-strategy/runs/${runId}/decisions`); return toCamelCase<{ items: LiveStrategyDecision[] }>(data).items; },
  async listOrders(runId: number): Promise<LiveStrategyOrder[]> { const { data } = await apiClient.get(`/api/v1/live-strategy/runs/${runId}/orders`); return toCamelCase<{ items: LiveStrategyOrder[] }>(data).items; },
  async saveConfig(payload: Partial<LiveStrategyConfig>): Promise<LiveStrategyConfig> { const { data } = await apiClient.put('/api/v1/live-strategy/config', { strategy_id: payload.strategyId, strategy_version: payload.strategyVersion, qmt_account: payload.qmtAccount, enabled: payload.enabled, symbols: payload.symbols, parameters: payload.parameters, rebalance_frequency_days: payload.rebalanceFrequencyDays, event_check_enabled: payload.eventCheckEnabled, data_sync_before_run: payload.dataSyncBeforeRun, last_trading_day_exit_enabled: payload.lastTradingDayExitEnabled, last_trading_day_exit_buffer_days: payload.lastTradingDayExitBufferDays, schedule_time: payload.scheduleTime }); return toConfigPayload(data); },
  async preview(tradeDate?: string, mode: LiveStrategyRunMode = 'auto', configId?: number): Promise<LiveStrategyPreview> { const { data } = await apiClient.post('/api/v1/live-strategy/runs/preview', { ...(tradeDate ? { trade_date: tradeDate } : {}), mode, ...(configId ? { config_id: configId } : {}) }); return toCamelCase<LiveStrategyPreview>(data); },
  async run(tradeDate?: string, mode: LiveStrategyRunMode = 'auto', configId?: number): Promise<LiveStrategyPreview & { runId: number; runUid: string; batchUid: string }> { const { data } = await apiClient.post('/api/v1/live-strategy/runs', { ...(tradeDate ? { trade_date: tradeDate } : {}), mode, ...(configId ? { config_id: configId } : {}) }); return toCamelCase(data); },
};
