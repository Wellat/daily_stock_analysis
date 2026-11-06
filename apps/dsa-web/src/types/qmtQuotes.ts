/** QMT 本地行情桥（Redis）状态与订阅类型 */

/** 订阅标的来源标签：holdings=持仓 / alert=告警规则 / manual=手动 / stale=残留（来源已移除） */
export type QmtSubscriptionOrigin = 'holdings' | 'alert' | 'manual' | 'stale';

export interface QmtStatus {
  enabled: boolean;
  redisUrl: string;
  redisAvailable: boolean;
  alive: boolean;
  heartbeat: string | null;
  subscribedCount: number;
  autoIncludeHoldings: boolean;
  includeAlertSymbols: boolean;
  manualSymbols: string[];
  holdingsCount: number;
  alertCount: number;
}

export interface QmtSubscriptionSymbol {
  symbol: string;
  /** 标的名称（本地表 best-effort 解析，可能为空） */
  name?: string;
  origins: QmtSubscriptionOrigin[];
}

export interface QmtSubscriptionDetail {
  symbols: QmtSubscriptionSymbol[];
  holdings: string[];
  alertSymbols: string[];
  manualSymbols: string[];
}

export interface QmtManualSymbolsResponse {
  symbols: string[];
  invalid: string[];
}

export interface QmtSyncResponse {
  changed: boolean;
  added: string[];
  removed: string[];
  total: number;
}

export interface QmtSettingsUpdateRequest {
  enabled?: boolean;
  autoIncludeHoldings?: boolean;
  includeAlertSymbols?: boolean;
}

/** Redis 原始快照/日K 排障视图（原样返回，不换单位） */
export interface QmtRawDataResponse {
  symbol: string;
  alive: boolean;
  found: boolean;
  data: Record<string, unknown> | null;
}

export interface QmtDailyBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
}

export interface QmtDailyDataResponse {
  symbol: string;
  alive: boolean;
  found: boolean;
  data: QmtDailyData | null;
}

export interface QmtDailyData {
  code?: string;
  dividend_type?: string;
  updated_at?: string;
  bars?: QmtDailyBar[];
}
