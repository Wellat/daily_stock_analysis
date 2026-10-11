import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { tradingApi, type CbHoldingItem, type CbHoldingsResponse } from '../../api/trading';
import { ApiErrorAlert, Card, EmptyState, Loading } from '../common';
import { countdownTag, formatPct, formatPrice3, proximityDateCell, yearsToMaturity } from '../common/cbIndicators';

const formatAmount = (value?: number | null): string =>
  value == null ? '--' : value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const formatSigned = (value?: number | null, digits = 2): string =>
  value == null ? '--' : `${value > 0 ? '+' : ''}${value.toFixed(digits)}`;

const pnlCell = (item: CbHoldingItem) => {
  if (item.pnl == null) return <span className="text-secondary-text">--</span>;
  const cls = item.pnl >= 0 ? 'text-red-600' : 'text-green-600';
  const pct = item.pnlPct != null ? `（${formatSigned(item.pnlPct)}%）` : '';
  return <span className={cls}>{formatSigned(item.pnl)}{pct}</span>;
};

const summaryStat = (label: string, value: React.ReactNode, cls?: string) => (
  <div className="min-w-28">
    <div className="text-xs text-secondary-text">{label}</div>
    <div className={`text-base font-medium text-foreground ${cls ?? ''}`}>{value}</div>
  </div>
);

const buildColumns = (): ColumnsType<CbHoldingItem> => [
  {
    title: '转债',
    dataIndex: 'symbol',
    render: (_, item) => (
      <div>
        <div className="font-medium text-foreground">{item.bondName ? `${item.bondName}（${item.symbol}）` : item.symbol}</div>
        {item.stockName ? <div className="text-xs text-secondary-text">正股 {item.stockName}</div> : null}
      </div>
    ),
  },
  {
    title: '现价',
    dataIndex: 'latestClose',
    width: 90,
    align: 'right',
    sorter: (a, b) => (a.latestClose ?? -Infinity) - (b.latestClose ?? -Infinity),
    render: (v: number | null) => formatPrice3(v),
  },
  {
    title: '持仓（张）',
    dataIndex: 'volume',
    width: 90,
    align: 'right',
    render: (v: number) => (v % 1 === 0 ? v.toFixed(0) : v),
  },
  {
    title: '成本价',
    dataIndex: 'openPrice',
    width: 90,
    align: 'right',
    render: (v: number | null) => formatPrice3(v),
  },
  {
    title: '占比',
    dataIndex: 'weightPct',
    width: 80,
    align: 'right',
    render: (v: number | null) => formatPct(v),
  },
  {
    title: '浮动盈亏',
    key: 'pnl',
    width: 150,
    align: 'right',
    sorter: (a, b) => (a.pnl ?? -Infinity) - (b.pnl ?? -Infinity),
    render: (_, item) => pnlCell(item),
  },
  {
    title: '转股溢价率',
    dataIndex: 'latestPremiumRate',
    width: 100,
    align: 'right',
    sorter: (a, b) => (a.latestPremiumRate ?? Infinity) - (b.latestPremiumRate ?? Infinity),
    render: (v: number | null) => (
      <span style={v != null && v < 0 ? { color: '#4ade80' } : undefined}>{formatPct(v)}</span>
    ),
  },
  {
    title: '剩余年限',
    key: 'remainingYears',
    width: 90,
    align: 'right',
    sorter: (a, b) => (yearsToMaturity(a.maturityDate) ?? Infinity) - (yearsToMaturity(b.maturityDate) ?? Infinity),
    render: (_, item) => {
      const years = yearsToMaturity(item.maturityDate);
      if (years == null) return <span className="text-secondary-text">--</span>;
      return <span className={years < 0.5 ? 'text-orange-500' : undefined}>{years.toFixed(1)} 年</span>;
    },
  },
  {
    title: '最后交易日',
    key: 'lastTradingDate',
    width: 170,
    sorter: (a, b) => (a.remainingTradingDays ?? 9999) - (b.remainingTradingDays ?? 9999),
    render: (_, item) => proximityDateCell(item.lastTradingDate, item.remainingTradingDays, item.maturityDate),
  },
  {
    title: '强赎',
    dataIndex: 'forceRedeemCountdown',
    width: 100,
    render: (v: string | null) => countdownTag(v),
  },
  {
    title: '下修',
    dataIndex: 'downReviseCountdown',
    width: 90,
    render: (v: string | null) => countdownTag(v),
  },
  {
    title: '回售',
    dataIndex: 'putCountdown',
    width: 90,
    render: (v: string | null) => countdownTag(v),
  },
  {
    title: '评级',
    dataIndex: 'bondRating',
    width: 70,
    render: (v: string | null) => v ?? <span className="text-secondary-text">--</span>,
  },
];

export const PositionsTab: React.FC = () => {
  const [data, setData] = useState<CbHoldingsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const loadHoldings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await tradingApi.listCbHoldings());
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadHoldings();
  }, [loadHoldings]);

  const summary = data?.summary;
  const pnlCls = (summary?.totalPnl ?? 0) >= 0 ? 'text-red-600' : 'text-green-600';

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-6">
            {summaryStat('资金账户', data?.account ?? '--')}
            {summaryStat('转债持仓', `${summary?.count ?? '--'} 只`)}
            {summaryStat('合计市值', summary?.totalMarketValue != null ? formatAmount(summary.totalMarketValue) : '--')}
            {summaryStat('浮动盈亏', summary?.totalPnl != null ? `${formatSigned(summary.totalPnl)}（${formatSigned(summary.totalPnlPct)}%）` : '--', summary?.totalPnl != null ? pnlCls : undefined)}
            {summaryStat('数据时点', [
              data?.asOfTradeDate ? `行情 ${data.asOfTradeDate}` : null,
              data?.positionsUpdatedAt ? `持仓 ${data.positionsUpdatedAt}` : null,
            ].filter(Boolean).join(' · ') || '--')}
          </div>
          <button type="button" className="btn-primary" onClick={() => void loadHoldings()}>
            <RefreshCw className="h-4 w-4" />
            刷新
          </button>
        </div>
        {data && data.excludedCount > 0 ? (
          <div className="mt-2 text-xs text-secondary-text">
            仅展示资金账户 {data.account} 的可转债持仓，另有 {data.excludedCount} 条非可转债持仓未展示。
          </div>
        ) : null}
      </Card>

      {error ? (
        <ApiErrorAlert error={error} actionLabel="重试" onAction={() => void loadHoldings()} />
      ) : null}

      {loading ? (
        <Loading />
      ) : !data || data.items.length === 0 ? (
        <EmptyState
          title="暂无可转债持仓"
          description="QMT 每日收盘后上报持仓，当前账户暂无可转债数据。"
        />
      ) : (
        <Card padding="none">
          <Table<CbHoldingItem>
            size="small"
            rowKey="symbol"
            columns={buildColumns()}
            dataSource={data.items}
            pagination={false}
            scroll={{ x: 1300 }}
          />
        </Card>
      )}
    </div>
  );
};
