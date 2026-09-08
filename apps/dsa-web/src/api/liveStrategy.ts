import apiClient from './index';
import { toCamelCase } from './utils';

/**策略参数键与元数据定义一致（snake_case），不做深度驼峰转换，否则存取对不上。*/
function toConfigPayload(data: unknown): LiveStrategyConfig {
  const { parameters, ...rest } = (data ?? {}) as Record<string, unknown>;
  return { ...toCamelCase<Omit<LiveStrategyConfig, 'parameters'>>(rest), parameters: (parameters ?? {}) as Record<string, unknown> };
}

export type LiveStrategyConfig = { id?: number; strategyId: string; strategyVersion: string; qmtAccount: string; enabled: boolean; symbols: string[]; parameters: Record<string, unknown>; rebalanceFrequencyDays?: number; eventCheckEnabled?: boolean; dataSyncBeforeRun?: boolean; nextRebalanceDate?: string | null };
export type LiveStrategyRunMode = 'auto' | 'rebalance' | 'event_check';
export type LiveStrategyDefinition = { strategyId: string; strategyVersion?: string; name: string; description: string; instrumentTypes?: string[]; markets?: string[]; parameters?: Array<{ key: string; label: string; type: string; default?: unknown; min?: number; max?: number }> };
export type LiveStrategyDiagnostics = { skipped?: Array<{ symbol?: string; reason: string }>; riskChecks?: Array<{ name: string; passed: boolean; detail?: string }> };
export type LiveStrategyPreview = { tradeDate: string; target: Record<string, { symbol: string; price: number; quantity: number }>; current: Record<string, number>; rebalance: Array<{ symbol: string; side: string; quantity: number; reason: string }>; strategyVersion: string; diagnostics?: LiveStrategyDiagnostics };
export type LiveStrategyRun = { id: number; tradeDate: string; runUid: string; status: string; mode?: string; strategyId?: string; strategyVersion?: string; qmtAccount?: string; decisionCount?: number; orderCount?: number; skipReason?: string | null; dataSnapshotAt?: string | null; completedAt?: string | null; target?: Record<string, { symbol: string; symbolName?: string; price?: number | null; quantity: number }>; current?: Record<string, number>; rebalance?: LiveStrategyPreview['rebalance']; risk?: { passed?: boolean; skipped?: Array<{ symbol?: string; reason: string }>; riskChecks?: Array<{ name: string; passed: boolean; detail?: string }> }; errorMessage?: string | null };
export type LiveStrategyDecision = { id: number; decisionUid?: string; mode?: string; action: string; symbol: string; symbolName?: string | null; targetAmount?: number | null; suggestedQuantity?: number | null; reason?: string | null; riskStatus?: string | null; decisionData?: Record<string, unknown> };
export type LiveStrategyOrder = { id: number; orderUid: string; symbol: string; symbolName?: string | null; side: string; quantity: number; status: string; decisionId?: number | null; qmtOrderId?: string | null; filledQuantity?: number | null; filledPrice?: number | null; submittedAt?: string | null; completedAt?: string | null; errorMessage?: string | null };
export type LiveStrategyBatch = { id: number; batchUid: string; runId: number; qmtAccount: string; status: string; summary?: { count?: number }; createdAt?: string | null; tradeDate?: string | null; mode?: string | null; orders?: { total: number; pending: number; submitted: number; filled: number; rejected: number; cancelled: number } };
export type LiveStrategySyncStatus = { tradeDate: string; intraday?: { status?: string; qualityStatus?: string; completedAt?: string; errorMessage?: string } | null; afterClose?: { status?: string; qualityStatus?: string; completedAt?: string; errorMessage?: string } | null };

export const liveStrategyApi = {
  async getConfig(): Promise<LiveStrategyConfig> { const { data } = await apiClient.get('/api/v1/live-strategy/config'); return toConfigPayload(data); },
  async listStrategies(): Promise<LiveStrategyDefinition[]> { const { data } = await apiClient.get('/api/v1/live-strategy/strategies'); return toCamelCase<{ items: LiveStrategyDefinition[] }>(data).items; },
  async listRuns(): Promise<LiveStrategyRun[]> { const { data } = await apiClient.get('/api/v1/live-strategy/runs'); return toCamelCase<{ items: LiveStrategyRun[] }>(data).items; },
  async listBatches(): Promise<LiveStrategyBatch[]> { const { data } = await apiClient.get('/api/v1/live-strategy/batches'); return toCamelCase<{ items: LiveStrategyBatch[] }>(data).items; },
  async syncStatus(): Promise<LiveStrategySyncStatus> { const { data } = await apiClient.get('/api/v1/live-strategy/data-sync/status'); return toCamelCase<LiveStrategySyncStatus>(data); },
  async listDecisions(runId: number): Promise<LiveStrategyDecision[]> { const { data } = await apiClient.get(`/api/v1/live-strategy/runs/${runId}/decisions`); return toCamelCase<{ items: LiveStrategyDecision[] }>(data).items; },
  async listOrders(runId: number): Promise<LiveStrategyOrder[]> { const { data } = await apiClient.get(`/api/v1/live-strategy/runs/${runId}/orders`); return toCamelCase<{ items: LiveStrategyOrder[] }>(data).items; },
  async saveConfig(payload: Partial<LiveStrategyConfig>): Promise<LiveStrategyConfig> { const { data } = await apiClient.put('/api/v1/live-strategy/config', { strategy_id: payload.strategyId, strategy_version: payload.strategyVersion, qmt_account: payload.qmtAccount, enabled: payload.enabled, symbols: payload.symbols, parameters: payload.parameters, rebalance_frequency_days: payload.rebalanceFrequencyDays, event_check_enabled: payload.eventCheckEnabled, data_sync_before_run: payload.dataSyncBeforeRun }); return toConfigPayload(data); },
  async preview(tradeDate?: string, mode: LiveStrategyRunMode = 'auto'): Promise<LiveStrategyPreview> { const { data } = await apiClient.post('/api/v1/live-strategy/runs/preview', { ...(tradeDate ? { trade_date: tradeDate } : {}), mode }); return toCamelCase<LiveStrategyPreview>(data); },
  async run(tradeDate?: string, mode: LiveStrategyRunMode = 'auto'): Promise<LiveStrategyPreview & { runId: number; runUid: string; batchUid: string }> { const { data } = await apiClient.post('/api/v1/live-strategy/runs', { ...(tradeDate ? { trade_date: tradeDate } : {}), mode }); return toCamelCase(data); },
};
