import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  QmtPositionListResponse,
  TradingDashboardQuery,
  TradingDashboardResponse,
  TradingOrderItem,
  TradingOrderListQuery,
  TradingOrderListResponse,
} from '../types/trading';

/** 实盘可转债持仓行（snake_case 响应经 toCamelCase 转换） */
export type CbHoldingItem = {
  symbol: string;
  bondName?: string | null;
  stockName?: string | null;
  volume: number;
  canUseVolume: number;
  openPrice?: number | null;
  latestClose?: number | null;
  latestPremiumRate?: number | null;
  marketValue?: number | null;
  weightPct?: number | null;
  pnl?: number | null;
  pnlPct?: number | null;
  remainingYears?: number | null;
  maturityDate?: string | null;
  lastTradingDate?: string | null;
  remainingTradingDays?: number | null;
  forceRedeemCountdown?: string | null;
  downReviseCountdown?: string | null;
  putCountdown?: string | null;
  bondRating?: string | null;
  remainingSize?: number | null;
  status?: string | null;
  updatedAt?: string | null;
};

export type CbHoldingsResponse = {
  account: string;
  asOfTradeDate?: string | null;
  positionsUpdatedAt?: string | null;
  excludedCount: number;
  summary: { count: number; totalVolume?: number | null; totalMarketValue?: number | null; totalPnl?: number | null; totalPnlPct?: number | null };
  items: CbHoldingItem[];
};

function buildListParams(query: TradingOrderListQuery = {}): Record<string, string | number> {
  const params: Record<string, string | number> = {};
  if (query.status) params.status = query.status;
  if (query.page !== undefined) params.page = query.page;
  if (query.limit !== undefined) params.limit = query.limit;
  return params;
}

export const tradingApi = {
  async listOrders(query: TradingOrderListQuery = {}): Promise<TradingOrderListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/trading/orders', {
      params: buildListParams(query),
    });
    return toCamelCase<TradingOrderListResponse>(response.data);
  },

  async cancelOrder(orderId: number): Promise<TradingOrderItem> {
    const response = await apiClient.post<Record<string, unknown>>(`/api/v1/trading/orders/${orderId}/cancel`);
    return toCamelCase<TradingOrderItem>(response.data);
  },

  async listPositions(account?: string): Promise<QmtPositionListResponse> {
    const params: Record<string, string> = {};
    if (account) params.account = account;
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/trading/positions', { params });
    return toCamelCase<QmtPositionListResponse>(response.data);
  },

  /** 实盘可转债持仓（QMT 持仓 × 最新因子 × 条款聚合；缺省账户取实盘策略配置） */
  async listCbHoldings(account?: string): Promise<CbHoldingsResponse> {
    const params: Record<string, string> = {};
    if (account) params.account = account;
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/trading/positions/cb', { params });
    return toCamelCase<CbHoldingsResponse>(response.data);
  },

  async getDashboard(query: TradingDashboardQuery = {}): Promise<TradingDashboardResponse> {
    const params: Record<string, string> = {};
    if (query.start) params.start = query.start;
    if (query.end) params.end = query.end;
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/trading/dashboard', { params });
    return toCamelCase<TradingDashboardResponse>(response.data);
  },
};
