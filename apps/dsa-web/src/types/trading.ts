export type TradingOrderSide = 'buy' | 'sell';
export type TradingOrderType = 'limit' | 'market';
export type TradingOrderStatus = 'pending' | 'submitted' | 'filled' | 'rejected' | 'cancelled';

export interface TradingOrderItem {
  id: number;
  orderUid: string;
  symbol: string;
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
