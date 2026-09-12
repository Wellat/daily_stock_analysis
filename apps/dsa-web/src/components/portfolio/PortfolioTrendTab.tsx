import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Segmented } from 'antd';
import { RefreshCw } from 'lucide-react';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { portfolioApi } from '../../api/portfolio';
import { ApiErrorAlert, EChart, EmptyState, InlineAlert, Loading } from '../common';
import type { EChartOption } from '../common';
import type { PortfolioCostMethod, PortfolioTrendResponse } from '../../types/portfolio';

const INPUT_CLASS = 'input-surface input-focus-glow h-10 w-full rounded-xl border bg-transparent px-3 text-sm';

type RangeMode = '7d' | '30d' | '90d' | 'custom';

function toDateStr(daysAgo: number): string {
  return new Date(Date.now() - daysAgo * 86400000).toISOString().slice(0, 10);
}

interface PortfolioTrendTabProps {
  /** 账户视角：undefined = 全部活跃账户汇总 */
  accountId?: number;
  costMethod: PortfolioCostMethod;
}

/** 持仓趋势：每日快照驱动的总市值与总收益（已实现+浮动）曲线。 */
export const PortfolioTrendTab: React.FC<PortfolioTrendTabProps> = ({ accountId, costMethod }) => {
  const [rangeMode, setRangeMode] = useState<RangeMode>('90d');
  const [startDate, setStartDate] = useState(() => toDateStr(89));
  const [endDate, setEndDate] = useState(() => toDateStr(0));
  const [trend, setTrend] = useState<PortfolioTrendResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const loadTrend = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await portfolioApi.getTrend({
        accountId,
        start: startDate || undefined,
        end: endDate || undefined,
        costMethod,
      });
      setTrend(result);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [accountId, costMethod, startDate, endDate]);

  useEffect(() => {
    void loadTrend();
  }, [loadTrend]);

  const applyRangeMode = (mode: RangeMode) => {
    setRangeMode(mode);
    if (mode === '7d') {
      setStartDate(toDateStr(6));
      setEndDate(toDateStr(0));
    } else if (mode === '30d') {
      setStartDate(toDateStr(29));
      setEndDate(toDateStr(0));
    } else if (mode === '90d') {
      setStartDate(toDateStr(89));
      setEndDate(toDateStr(0));
    }
  };

  const items = useMemo(() => trend?.items ?? [], [trend]);

  const marketValueOption: EChartOption = useMemo(() => {
    if (items.length === 0) return {};
    return {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis' },
      grid: { left: 72, right: 16, top: 24, bottom: 32 },
      xAxis: { type: 'category', data: items.map((point) => point.date) },
      yAxis: { type: 'value', scale: true },
      series: [
        {
          name: '总市值',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: items.map((point) => Number(point.totalMarketValue.toFixed(2))),
          lineStyle: { width: 2, color: '#22d3ee' },
          areaStyle: { color: 'rgba(34, 211, 238, 0.12)' },
        },
      ],
    };
  }, [items]);

  const pnlOption: EChartOption = useMemo(() => {
    if (items.length === 0) return {};
    return {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis' },
      legend: { textStyle: { color: '#94a3b8' }, top: 0 },
      grid: { left: 72, right: 16, top: 32, bottom: 32 },
      xAxis: { type: 'category', data: items.map((point) => point.date) },
      yAxis: { type: 'value', scale: true },
      series: [
        {
          name: '总收益',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: items.map((point) => Number(point.totalPnl.toFixed(2))),
          lineStyle: { width: 2, color: '#22d3ee' },
          areaStyle: { color: 'rgba(34, 211, 238, 0.12)' },
        },
        {
          name: '已实现',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: items.map((point) => Number(point.realizedPnl.toFixed(2))),
          lineStyle: { width: 1.5, color: '#4ade80' },
        },
        {
          name: '浮动',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: items.map((point) => Number(point.unrealizedPnl.toFixed(2))),
          lineStyle: { width: 1.5, color: '#f59e0b' },
        },
      ],
    };
  }, [items]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            value={rangeMode}
            onChange={(value) => applyRangeMode(value as RangeMode)}
            options={[
              { label: '近7天', value: '7d' },
              { label: '近30天', value: '30d' },
              { label: '近90天', value: '90d' },
              { label: '自定义', value: 'custom' },
            ]}
          />
          <input
            aria-label="开始日期"
            className={`${INPUT_CLASS} w-40`}
            type="date"
            value={startDate}
            onChange={(event) => {
              setRangeMode('custom');
              setStartDate(event.target.value);
            }}
          />
          <input
            aria-label="结束日期"
            className={`${INPUT_CLASS} w-40`}
            type="date"
            value={endDate}
            onChange={(event) => {
              setRangeMode('custom');
              setEndDate(event.target.value);
            }}
          />
          <button
            type="button"
            className="btn-primary"
            disabled={loading}
            onClick={() => void loadTrend()}
          >
            <RefreshCw className="h-4 w-4" />
            刷新
          </button>
        </div>
        <p className="text-xs text-secondary-text">
          每个交易日一份快照；总收益 = 已实现 + 浮动，跟随页头的账户与成本法选择。
        </p>
      </div>

      {error ? (
        <ApiErrorAlert error={error} actionLabel="重试" onAction={() => void loadTrend()} />
      ) : null}

      {loading ? (
        <Loading />
      ) : items.length > 0 ? (
        <>
          {trend?.truncated ? (
            <InlineAlert
              variant="warning"
              message="时间范围较大，本次仅回补最近的部分缺失日期；可缩小范围或稍后再次查询继续回补。"
            />
          ) : null}
          {trend != null && trend.backfilled > 0 ? (
            <p className="text-xs text-secondary-text">
              首次查询自动回补缺失交易日的快照（本次回补 {trend.backfilled} 天），之后即查即得。
            </p>
          ) : null}

          <div className="glass-panel px-4 py-4">
            <h3 className="text-sm font-semibold text-foreground mb-2">总市值趋势</h3>
            <EChart option={marketValueOption} height={300} aria-label="总市值趋势图" />
          </div>

          <div className="glass-panel px-4 py-4">
            <h3 className="text-sm font-semibold text-foreground mb-2">总收益趋势</h3>
            <EChart option={pnlOption} height={300} aria-label="总收益趋势图" />
          </div>
        </>
      ) : !error ? (
        <EmptyState
          title="暂无趋势数据"
          description="该时间范围内没有可展示的每日快照；录入交易并等待盘后快照生成，或扩大时间范围。"
        />
      ) : null}
    </div>
  );
};
