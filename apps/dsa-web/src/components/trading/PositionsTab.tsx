import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { tradingApi } from '../../api/trading';
import {
  ApiErrorAlert,
  Card,
  EmptyState,
  Loading,
} from '../common';
import type { QmtPositionItem } from '../../types/trading';
import { formatDateTime } from '../../utils/format';

function formatNumber(value?: number | null): string {
  return value == null ? '—' : String(value);
}

export const PositionsTab: React.FC = () => {
  const [positions, setPositions] = useState<QmtPositionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const loadPositions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await tradingApi.listPositions();
      setPositions(result.items);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPositions();
  }, [loadPositions]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm text-secondary-text">共 {positions.length} 条</span>
        <button type="button" className="btn-primary" onClick={() => void loadPositions()}>
          <RefreshCw className="h-4 w-4" />
          刷新
        </button>
      </div>

      {error ? (
        <ApiErrorAlert error={error} actionLabel="重试" onAction={() => void loadPositions()} />
      ) : null}

      {loading ? (
        <Loading />
      ) : positions.length === 0 ? (
        <EmptyState
          title="暂无持仓"
          description="QMT 每日收盘后上报持仓，暂无数据。"
        />
      ) : (
        <Card padding="none">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/60 text-left text-xs uppercase tracking-wide text-secondary-text">
                  <th className="px-4 py-3">资金账号</th>
                  <th className="px-4 py-3">代码</th>
                  <th className="px-4 py-3">名称</th>
                  <th className="px-4 py-3">持仓数量</th>
                  <th className="px-4 py-3">可用数量</th>
                  <th className="px-4 py-3">成本价</th>
                  <th className="px-4 py-3">浮动盈亏</th>
                  <th className="px-4 py-3">更新时间</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((position) => (
                  <tr key={position.id} className="border-b border-border/40 hover:bg-hover">
                    <td className="px-4 py-3">{position.account}</td>
                    <td className="px-4 py-3 font-medium text-foreground">{position.symbol}</td>
                    <td className="px-4 py-3">{position.name ?? '—'}</td>
                    <td className="px-4 py-3">{formatNumber(position.volume)}</td>
                    <td className="px-4 py-3">{formatNumber(position.canUseVolume)}</td>
                    <td className="px-4 py-3">{formatNumber(position.openPrice)}</td>
                    <td className="px-4 py-3">{formatNumber(position.floatProfit)}</td>
                    <td className="px-4 py-3">{formatDateTime(position.updatedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
};
