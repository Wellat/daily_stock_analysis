import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Segmented, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { RefreshCw } from 'lucide-react';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { tradingApi } from '../../api/trading';
import { ApiErrorAlert, EChart, EmptyState, InlineAlert, Loading, StatCard } from '../common';
import type { EChartOption } from '../common';
import type { TradingDashboardResponse, TradingDashboardSymbolItem } from '../../types/trading';

const INPUT_CLASS = 'input-surface input-focus-glow h-10 w-full rounded-xl border bg-transparent px-3 text-sm';

type RangeMode = '7d' | '30d' | 'all' | 'custom';

function toDateStr(daysAgo: number): string {
  return new Date(Date.now() - daysAgo * 86400000).toISOString().slice(0, 10);
}

function formatAmount(value?: number | null): string {
  return value == null ? '--' : value.toFixed(2);
}

function pnlText(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}`;
}

function pnlClassName(value: number): string {
  if (value > 0.000001) return 'text-success';
  if (value < -0.000001) return 'text-danger';
  return 'text-secondary-text';
}

export const StrategyDashboardTab: React.FC = () => {
  const [rangeMode, setRangeMode] = useState<RangeMode>('all');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [dashboard, setDashboard] = useState<TradingDashboardResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await tradingApi.getDashboard({
        start: startDate || undefined,
        end: endDate || undefined,
      });
      setDashboard(result);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate]);

  useEffect(() => {
    void loadDashboard();
  }, [loadDashboard]);

  const applyRangeMode = (mode: RangeMode) => {
    setRangeMode(mode);
    if (mode === '7d') {
      setStartDate(toDateStr(6));
      setEndDate(toDateStr(0));
    } else if (mode === '30d') {
      setStartDate(toDateStr(29));
      setEndDate(toDateStr(0));
    } else if (mode === 'all') {
      setStartDate('');
      setEndDate('');
    }
  };

  const summary = dashboard?.summary;

  const curveOption: EChartOption = useMemo(() => {
    const curve = dashboard?.curve ?? [];
    if (curve.length === 0) return {};
    return {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis' },
      legend: { textStyle: { color: '#94a3b8' }, top: 0 },
      grid: { left: 64, right: 16, top: 32, bottom: 32 },
      xAxis: { type: 'category', data: curve.map((point) => point.date) },
      yAxis: { type: 'value', scale: true },
      series: [
        {
          name: '每日盈亏',
          type: 'bar',
          data: curve.map((point) => ({
            value: Number(point.dailyPnl.toFixed(2)),
            itemStyle: { color: point.dailyPnl >= 0 ? 'rgba(74, 222, 128, 0.45)' : 'rgba(248, 113, 113, 0.45)' },
          })),
        },
        {
          name: '累计盈亏',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: curve.map((point) => Number(point.cumulativePnl.toFixed(2))),
          lineStyle: { width: 2, color: '#22d3ee' },
          areaStyle: { color: 'rgba(34, 211, 238, 0.12)' },
        },
      ],
    };
  }, [dashboard]);

  const symbolColumns: ColumnsType<TradingDashboardSymbolItem> = [
    {
      title: '标的',
      dataIndex: 'symbol',
      width: 180,
      render: (_, record) => (record.symbolName ? `${record.symbolName}（${record.symbol}）` : record.symbol),
    },
    {
      title: '买入',
      width: 150,
      render: (_, record) => `${record.buyCount} 笔 · ${formatAmount(record.buyQuantity)}`,
    },
    {
      title: '买入金额',
      dataIndex: 'buyAmount',
      width: 110,
      align: 'right',
      render: (value: number) => formatAmount(value),
    },
    {
      title: '卖出',
      width: 150,
      render: (_, record) => `${record.sellCount} 笔 · ${formatAmount(record.sellQuantity)}`,
    },
    {
      title: '卖出金额',
      dataIndex: 'sellAmount',
      width: 110,
      align: 'right',
      render: (value: number) => formatAmount(value),
    },
    {
      title: '未平仓',
      width: 170,
      render: (_, record) => (record.openQuantity > 0
        ? `${formatAmount(record.openQuantity)} 张 · 成本 ${formatAmount(record.openCost)}`
        : '—'),
    },
    {
      title: '已实现盈亏',
      dataIndex: 'realizedPnl',
      width: 120,
      align: 'right',
      render: (value: number) => (
        <span className={pnlClassName(value)}>{pnlText(value)}</span>
      ),
    },
    {
      title: '无配对卖出',
      dataIndex: 'unmatchedSellQuantity',
      width: 100,
      align: 'right',
      render: (value: number) => (value > 0 ? <Tag color="warning">{formatAmount(value)}</Tag> : '—'),
    },
  ];

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
              { label: '全部', value: 'all' },
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
            onClick={() => void loadDashboard()}
          >
            <RefreshCw className="h-4 w-4" />
            刷新
          </button>
        </div>
        <p className="text-xs text-secondary-text">
          盈亏为已实现口径（卖出按 FIFO 与买入配对），未平仓部分不计浮盈。
        </p>
      </div>

      {error ? (
        <ApiErrorAlert error={error} actionLabel="重试" onAction={() => void loadDashboard()} />
      ) : null}

      {loading ? (
        <Loading />
      ) : dashboard && summary ? (
        <>
          {summary.unmatchedSellQuantity > 0 ? (
            <InlineAlert
              variant="warning"
              message={`存在 ${formatAmount(summary.unmatchedSellQuantity)} 张无买入记录的卖出（初始持仓直接卖出），未计入盈亏。`}
            />
          ) : null}

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <StatCard label="成交笔数" value={summary.totalCount} hint={`买入 ${summary.buyCount} 笔 · 卖出 ${summary.sellCount} 笔`} />
            <StatCard label="买入金额" value={formatAmount(summary.buyAmount)} hint={`共 ${summary.buyCount} 笔`} />
            <StatCard label="卖出金额" value={formatAmount(summary.sellAmount)} hint={`共 ${summary.sellCount} 笔`} />
            <StatCard
              label="已实现盈亏"
              value={<span className={pnlClassName(summary.realizedPnl)}>{pnlText(summary.realizedPnl)}</span>}
              tone={summary.realizedPnl > 0 ? 'success' : summary.realizedPnl < 0 ? 'danger' : 'default'}
            />
            <StatCard label="盈利 / 亏损笔数" value={`${summary.winCount} / ${summary.lossCount}`} />
            <StatCard
              label="胜率"
              value={summary.winRate == null ? '--' : `${(summary.winRate * 100).toFixed(1)}%`}
              hint="按有配对的卖出笔数统计"
            />
          </div>

          <div className="glass-panel px-4 py-4">
            <h3 className="text-sm font-semibold text-foreground mb-2">累计收益曲线</h3>
            {dashboard.curve.length > 0 ? (
              <EChart option={curveOption} height={300} aria-label="累计收益曲线" />
            ) : (
              <EmptyState title="暂无已实现盈亏" description="该时间范围内没有完成配对的卖出成交，调整时间范围或等待策略调仓。" />
            )}
          </div>

          <div className="glass-panel px-4 py-4">
            <h3 className="text-sm font-semibold text-foreground mb-2">分标的盈亏明细</h3>
            <Table<TradingDashboardSymbolItem>
              size="small"
              rowKey="symbol"
              columns={symbolColumns}
              dataSource={dashboard.symbols}
              pagination={false}
              scroll={{ x: 980 }}
            />
          </div>
        </>
      ) : !error ? (
        <EmptyState title="暂无成交数据" description="还没有已成交的交易指令。" />
      ) : null}
    </div>
  );
};
