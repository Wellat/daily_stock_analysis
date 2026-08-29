import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { tradingApi } from '../../api/trading';
import {
  ApiErrorAlert,
  Badge,
  Card,
  EmptyState,
  Loading,
  Pagination,
  Select,
} from '../common';
import type { TradingOrderItem, TradingOrderStatus } from '../../types/trading';
import { formatDateTime } from '../../utils/format';

const PAGE_SIZE = 20;

type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info';

const STATUS_LABELS: Record<TradingOrderStatus, string> = {
  pending: '待执行',
  submitted: '已提交',
  filled: '已成交',
  rejected: '已失败',
  cancelled: '已取消',
};

const STATUS_BADGE_VARIANTS: Record<TradingOrderStatus, BadgeVariant> = {
  pending: 'warning',
  submitted: 'info',
  filled: 'success',
  rejected: 'danger',
  cancelled: 'default',
};

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'pending', label: '待执行' },
  { value: 'submitted', label: '已提交' },
  { value: 'filled', label: '已成交' },
  { value: 'rejected', label: '已失败' },
  { value: 'cancelled', label: '已取消' },
];

function sideLabel(side: string): string {
  if (side === 'buy') return '买入';
  if (side === 'sell') return '卖出';
  return side;
}

function orderTypeLabel(orderType: string): string {
  if (orderType === 'limit') return '限价';
  if (orderType === 'market') return '市价';
  return orderType;
}

export const TradingOrdersTab: React.FC = () => {
  const [orders, setOrders] = useState<TradingOrderItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const loadOrders = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await tradingApi.listOrders({
        status: (statusFilter || undefined) as TradingOrderStatus | undefined,
        page,
        limit: PAGE_SIZE,
      });
      setOrders(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter]);

  useEffect(() => {
    void loadOrders();
  }, [loadOrders]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="w-48">
          <Select
            value={statusFilter}
            onChange={(value) => {
              setPage(1);
              setStatusFilter(value);
            }}
            options={STATUS_OPTIONS}
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-secondary-text">共 {total} 条</span>
          <button type="button" className="btn-primary" onClick={() => void loadOrders()}>
            <RefreshCw className="h-4 w-4" />
            刷新
          </button>
        </div>
      </div>

      {error ? (
        <ApiErrorAlert error={error} actionLabel="重试" onAction={() => void loadOrders()} />
      ) : null}

      {loading ? (
        <Loading />
      ) : orders.length === 0 ? (
        <EmptyState
          title="暂无交易指令"
          description="还没有可转债交易指令，可通过 API 手动录入。"
        />
      ) : (
        <Card padding="none">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/60 text-left text-xs uppercase tracking-wide text-secondary-text">
                  <th className="px-4 py-3">ID</th>
                  <th className="px-4 py-3">代码</th>
                  <th className="px-4 py-3">方向</th>
                  <th className="px-4 py-3">数量</th>
                  <th className="px-4 py-3">类型</th>
                  <th className="px-4 py-3">限价</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">成交数量</th>
                  <th className="px-4 py-3">成交价</th>
                  <th className="px-4 py-3">QMT 订单号</th>
                  <th className="px-4 py-3">创建时间</th>
                  <th className="px-4 py-3">失败原因</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr key={order.id} className="border-b border-border/40 hover:bg-hover">
                    <td className="px-4 py-3 text-muted-text">{order.id}</td>
                    <td className="px-4 py-3 font-medium text-foreground">{order.symbol}</td>
                    <td className="px-4 py-3">{sideLabel(order.side)}</td>
                    <td className="px-4 py-3">{order.quantity}</td>
                    <td className="px-4 py-3">{orderTypeLabel(order.orderType)}</td>
                    <td className="px-4 py-3">{order.limitPrice ?? '—'}</td>
                    <td className="px-4 py-3">
                      <Badge variant={STATUS_BADGE_VARIANTS[order.status]}>
                        {STATUS_LABELS[order.status]}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">{order.filledQuantity ?? '—'}</td>
                    <td className="px-4 py-3">{order.filledPrice ?? '—'}</td>
                    <td className="px-4 py-3">{order.qmtOrderId ?? '—'}</td>
                    <td className="px-4 py-3">{formatDateTime(order.createdAt)}</td>
                    <td className="px-4 py-3 text-danger">{order.errorMessage ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {totalPages > 1 ? (
        <Pagination currentPage={page} totalPages={totalPages} onPageChange={setPage} />
      ) : null}
    </div>
  );
};
