import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  QmtDailyData,
  QmtDailyDataResponse,
  QmtManualSymbolsResponse,
  QmtRawDataResponse,
  QmtSettingsUpdateRequest,
  QmtStatus,
  QmtSubscriptionDetail,
  QmtSyncResponse,
} from '../types/qmtQuotes';

export const qmtQuotesApi = {
  async getStatus(): Promise<QmtStatus> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/qmt-quotes/status');
    return toCamelCase<QmtStatus>(response.data);
  },

  async getSubscription(): Promise<QmtSubscriptionDetail> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/qmt-quotes/subscription');
    return toCamelCase<QmtSubscriptionDetail>(response.data);
  },

  async updateManualSymbols(symbols: string[]): Promise<QmtManualSymbolsResponse> {
    const response = await apiClient.put<Record<string, unknown>>('/api/v1/qmt-quotes/subscription/manual', {
      symbols,
    });
    return toCamelCase<QmtManualSymbolsResponse>(response.data);
  },

  async syncSubscription(): Promise<QmtSyncResponse> {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/qmt-quotes/subscription/sync');
    return toCamelCase<QmtSyncResponse>(response.data);
  },

  async updateSettings(payload: QmtSettingsUpdateRequest): Promise<QmtStatus> {
    // 后端 schema 为 snake_case 字段（与其他模块一致），此处显式映射
    const body: Record<string, boolean> = {};
    if (payload.enabled !== undefined) body.enabled = payload.enabled;
    if (payload.autoIncludeHoldings !== undefined) body.auto_include_holdings = payload.autoIncludeHoldings;
    if (payload.includeAlertSymbols !== undefined) body.include_alert_symbols = payload.includeAlertSymbols;
    const response = await apiClient.put<Record<string, unknown>>('/api/v1/qmt-quotes/settings', body);
    return toCamelCase<QmtStatus>(response.data);
  },

  async getQuoteData(symbol: string): Promise<QmtRawDataResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/qmt-quotes/data/quote', {
      params: { symbol },
    });
    // 排障视图：envelope 手动取值，data 保留 Redis 原始字段名（不走 toCamelCase）
    return {
      symbol: String(response.data.symbol ?? ''),
      alive: Boolean(response.data.alive),
      found: Boolean(response.data.found),
      data: (response.data.data as Record<string, unknown> | null) ?? null,
    };
  },

  async getDailyData(symbol: string): Promise<QmtDailyDataResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/qmt-quotes/data/daily', {
      params: { symbol },
    });
    return {
      symbol: String(response.data.symbol ?? ''),
      alive: Boolean(response.data.alive),
      found: Boolean(response.data.found),
      data: (response.data.data as QmtDailyData | null) ?? null,
    };
  },
};
