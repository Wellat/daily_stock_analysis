import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  QmtPositionListResponse,
  TradingOrderItem,
  TradingOrderListQuery,
  TradingOrderListResponse,
} from '../types/trading';

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
};
