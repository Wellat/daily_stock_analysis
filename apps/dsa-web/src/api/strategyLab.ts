import apiClient from './index';

export type StrategyLabStrategyItem = {
  strategy_id: string;
  name: string;
  instrument_types: string[];
  markets: string[];
  description?: string | null;
};

export type StrategyLabMetric = {
  total_return_pct?: number | null;
  annualized_return_pct?: number | null;
  max_drawdown_pct?: number | null;
  sharpe_ratio?: number | null;
  win_rate_pct?: number | null;
  trade_count: number;
  diagnostics?: Record<string, unknown>;
};

export type StrategyLabRunItem = {
  id: number;
  run_uid: string;
  strategy_id: string;
  strategy_name: string;
  engine_name: string;
  status: string;
  market: string;
  instrument_type: string;
  start_date: string;
  end_date: string;
  initial_cash: number;
  final_equity?: number | null;
  benchmark_symbol?: string | null;
  benchmark_return_pct?: number | null;
  portfolio_account_id?: number | null;
  error_message?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
  parameters?: Record<string, unknown>;
  symbols?: string[];
  metrics?: StrategyLabMetric | null;
  equity_curve?: Array<{ trade_date: string; equity: number; cash: number; positions_value: number }>;
};

export type StrategyLabTradeItem = {
  id: number;
  run_id: number;
  trade_date: string;
  symbol: string;
  side: 'buy' | 'sell';
  quantity: number;
  price: number;
  amount: number;
  fee: number;
  reason?: string | null;
};

export type StrategyLabBatchItem = {
  id: number;
  batch_uid: string;
  strategy_id: string;
  strategy_name: string;
  market: string;
  instrument_type: string;
  status: string;
  total_tasks: number;
  completed_tasks: number;
  success_tasks: number;
  failed_tasks: number;
  created_at?: string | null;
  completed_at?: string | null;
  parameters_grid?: Array<Record<string, unknown>>;
  summary?: Record<string, number>;
  items?: Array<{ id: number; run_id?: number | null; parameters: Record<string, unknown>; status: string; error_message?: string | null }>;
};

export type StrategyLabSignalItem = {
  id: number;
  run_id: number;
  portfolio_account_id?: number | null;
  symbol: string;
  market: string;
  suggested_action: string;
  confidence?: number | null;
  reason?: string | null;
  status: string;
  portfolio_trade_id?: number | null;
  created_at?: string | null;
};

export type StrategyLabSyncRunItem = {
  id: number;
  sync_type: string;
  market: string;
  status: string;
  result: Record<string, number>;
  error_message?: string | null;
  created_at?: string | null;
};

export type StrategyLabEventStudyResponse = {
  market: string;
  event_type?: string | null;
  offsets: number[];
  total: number;
  summary: Record<string, { count: number; average_return_pct?: number | null }>;
  items: Array<{ bond_code: string; bond_name: string; event_date: string; event_type: string; returns_pct: Record<string, number | null> }>;
};

export type StrategyLabInstrumentItem = {
  bond_code: string;
  bond_name: string;
  stock_code: string;
  stock_name?: string | null;
  market: string;
  list_date?: string | null;
  maturity_date?: string | null;
  status?: string | null;
  remaining_size?: number | null;
  current_premium_rate?: number | null;
  convert_price?: number | null;
  latest_close?: number | null;
  latest_premium_rate?: number | null;
  event_count: number;
  source?: string | null;
  updated_at?: string | null;
};

export type StrategyLabInstrumentDetail = StrategyLabInstrumentItem & {
  industry?: string | null;
  terms: Record<string, unknown>;
  redeem_clause?: string | null;
  down_revise_clause?: string | null;
  put_clause?: string | null;
  redeem_trigger_price?: number | null;
  down_revise_trigger_price?: number | null;
  put_trigger_price?: number | null;
  bar_count: number;
};

export type StrategyLabBarItem = {
  trade_date: string;
  close?: number | null;
  premium_rate?: number | null;
  remaining_size?: number | null;
  redeem_alert: boolean;
  down_revise_alert: boolean;
  put_alert: boolean;
  source?: string | null;
};

export type StrategyLabEventItem = {
  event_date: string;
  event_type: string;
  event_detail?: string | null;
  source?: string | null;
  created_at?: string | null;
};

export type StrategyLabRunRequest = {
  strategy_id: string;
  market: string;
  instrument_type: string;
  start_date: string;
  end_date: string;
  initial_cash: number;
  benchmark_symbol?: string;
  symbols?: string[];
  parameters?: Record<string, unknown>;
};

export type StrategyLabBatchRequest = {
  strategy_id: string;
  market: string;
  instrument_type: string;
  base_config: Record<string, unknown>;
  parameter_grid: Record<string, unknown[]>;
  run_async?: boolean;
};

export const strategyLabApi = {
  async listStrategies() {
    const { data } = await apiClient.get<{ items: StrategyLabStrategyItem[] }>('/api/v1/strategy-lab/strategies');
    return data.items;
  },
  async listRuns() {
    const { data } = await apiClient.get<{ items: StrategyLabRunItem[] }>('/api/v1/strategy-lab/runs');
    return data.items;
  },
  async getRun(runId: number) {
    const { data } = await apiClient.get<StrategyLabRunItem>(`/api/v1/strategy-lab/runs/${runId}`);
    return data;
  },
  async listRunTrades(runId: number) {
    const { data } = await apiClient.get<{ items: StrategyLabTradeItem[] }>(`/api/v1/strategy-lab/runs/${runId}/trades`);
    return data.items;
  },
  async createRun(payload: StrategyLabRunRequest) {
    const { data } = await apiClient.post<StrategyLabRunItem>('/api/v1/strategy-lab/runs', payload);
    return data;
  },
  async listBatches() {
    const { data } = await apiClient.get<{ items: StrategyLabBatchItem[] }>('/api/v1/strategy-lab/batches');
    return data.items;
  },
  async getBatch(batchId: number) {
    const { data } = await apiClient.get<StrategyLabBatchItem>(`/api/v1/strategy-lab/batches/${batchId}`);
    return data;
  },
  async createBatch(payload: StrategyLabBatchRequest) {
    const { data } = await apiClient.post<StrategyLabBatchItem>('/api/v1/strategy-lab/batches', payload);
    return data;
  },
  async retryBatch(batchId: number) {
    const { data } = await apiClient.post<StrategyLabBatchItem>(`/api/v1/strategy-lab/batches/${batchId}/retry`);
    return data;
  },
  async resumeBatch(batchId: number) {
    const { data } = await apiClient.post<StrategyLabBatchItem>(`/api/v1/strategy-lab/batches/${batchId}/resume`);
    return data;
  },
  async deleteBatch(batchId: number) {
    await apiClient.delete(`/api/v1/strategy-lab/batches/${batchId}`);
  },
  getBatchStreamUrl(batchId: number) {
    const baseUrl = apiClient.defaults.baseURL || '';
    return `${baseUrl}/api/v1/strategy-lab/batches/${batchId}/stream`;
  },
  async listSignals() {
    const { data } = await apiClient.get<{ items: StrategyLabSignalItem[] }>('/api/v1/strategy-lab/signals');
    return data.items;
  },
  async createSignal(payload: { run_id: number; portfolio_account_id?: number; suggested_action: string; confidence?: number; reason?: string }) {
    const { data } = await apiClient.post<StrategyLabSignalItem>('/api/v1/strategy-lab/signals', payload);
    return data;
  },
  async confirmSignal(signalId: number, payload: { portfolio_account_id: number; trade_date: string; quantity: number; price: number; side: 'buy' | 'sell'; fee?: number; tax?: number }) {
    const { data } = await apiClient.post<StrategyLabSignalItem>(`/api/v1/strategy-lab/signals/${signalId}/confirm`, payload);
    return data;
  },
  async listSyncRuns(params: { page?: number; limit?: number } = {}) {
    const { data } = await apiClient.get<{ items: StrategyLabSyncRunItem[]; total: number }>('/api/v1/strategy-lab/data-sync/runs', { params });
    return data;
  },
  async syncData(payload: {
    market: string;
    source: string;
    sync_type?: string;
    include_delisted?: boolean;
    start_date?: string;
    end_date?: string;
    symbols?: string[];
  }) {
    const { data } = await apiClient.post('/api/v1/strategy-lab/data-sync', payload);
    return data;
  },
  async studyEvents(payload: { market: string; event_type?: string; offsets: number[]; symbols?: string[] }) {
    const { data } = await apiClient.post<StrategyLabEventStudyResponse>('/api/v1/strategy-lab/studies/events', payload);
    return data;
  },
  async listInstruments(params: { market?: string; keyword?: string; status?: 'active' | 'delisted'; held_only?: boolean; page?: number; limit?: number } = {}) {
    const { data } = await apiClient.get<{ market: string; total: number; page: number; limit: number; items: StrategyLabInstrumentItem[] }>('/api/v1/strategy-lab/instruments', { params });
    return data;
  },
  async getInstrumentDetail(bondCode: string, market = 'cn') {
    const { data } = await apiClient.get<StrategyLabInstrumentDetail>(`/api/v1/strategy-lab/instruments/${encodeURIComponent(bondCode)}`, { params: { market } });
    return data;
  },
  async listInstrumentBars(bondCode: string, params: { market?: string; start_date?: string; end_date?: string; limit?: number } = {}) {
    const { data } = await apiClient.get<{ bond_code: string; total: number; items: StrategyLabBarItem[] }>(`/api/v1/strategy-lab/instruments/${encodeURIComponent(bondCode)}/bars`, { params });
    return data;
  },
  async listInstrumentEvents(bondCode: string, params: { market?: string; event_type?: string; limit?: number } = {}) {
    const { data } = await apiClient.get<{ bond_code: string; total: number; items: StrategyLabEventItem[] }>(`/api/v1/strategy-lab/instruments/${encodeURIComponent(bondCode)}/events`, { params });
    return data;
  },
};
