export type TradingOrderSide = 'buy' | 'sell';
export type TradingOrderType = 'limit' | 'market';
export type TradingOrderStatus = 'pending' | 'submitted' | 'filled' | 'rejected' | 'cancelled';

export interface TradingOrderItem {
  id: number;
  orderUid: string;
  symbol: string;
  symbolName?: string | null;
  market: string;
  instrumentType: string;
  side: TradingOrderSide;
  quantity: number;
  orderType: TradingOrderType;
  limitPrice?: number | null;
  status: TradingOrderStatus;
  qmtOrderId?: string | null;
  filledQuantity?: number | null;
  filledPrice?: number | null;
  errorMessage?: string | null;
  source: string;
  reason?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  submittedAt?: string | null;
  completedAt?: string | null;
}

export interface TradingOrderListResponse {
  page: number;
  limit: number;
  total: number;
  items: TradingOrderItem[];
}

export type TradingOrderListQuery = {
  status?: TradingOrderStatus;
  page?: number;
  limit?: number;
};

export interface QmtPositionItem {
  id: number;
  account: string;
  symbol: string;
  name?: string | null;
  volume: number;
  canUseVolume: number;
  openPrice?: number | null;
  floatProfit?: number | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface QmtPositionListResponse {
  items: QmtPositionItem[];
}

export interface TradingDashboardSummary {
  totalCount: number;
  buyCount: number;
  sellCount: number;
  buyAmount: number;
  sellAmount: number;
  realizedPnl: number;
  winCount: number;
  lossCount: number;
  winRate?: number | null;
  unmatchedSellQuantity: number;
}

export interface TradingDashboardCurvePoint {
  date: string;
  dailyPnl: number;
  cumulativePnl: number;
}

export interface TradingDashboardSymbolItem {
  symbol: string;
  symbolName?: string | null;
  buyCount: number;
  buyQuantity: number;
  buyAmount: number;
  sellCount: number;
  sellQuantity: number;
  sellAmount: number;
  realizedPnl: number;
  openQuantity: number;
  openCost: number;
  unmatchedSellQuantity: number;
}

export interface TradingDashboardResponse {
  start?: string | null;
  end?: string | null;
  summary: TradingDashboardSummary;
  curve: TradingDashboardCurvePoint[];
  symbols: TradingDashboardSymbolItem[];
}

export interface TradingDashboardQuery {
  start?: string;
  end?: string;
}
