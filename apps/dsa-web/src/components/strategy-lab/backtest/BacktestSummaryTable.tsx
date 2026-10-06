import { Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo } from 'react';
import type { StrategyLabRunItem } from '../../../api/strategyLab';
import { formatNumber, formatPct } from '../utils';

type MetricSource = {
  total_return_pct?: number | null;
  annualized_return_pct?: number | null;
  max_drawdown_pct?: number | null;
  sharpe_ratio?: number | null;
  sortino_ratio?: number | null;
  calmar_ratio?: number | null;
  period_count?: number | null;
  profit_periods?: number | null;
  loss_periods?: number | null;
};

type SummaryRow = MetricSource & {
  key: string;
  label: string;
  finalEquity?: number | null;
  turnoverAvgPct?: number | null;
};

const BENCHMARK_MODE_LABELS: Record<string, string> = {
  equal_weight_pool: '标的池等权',
  'index:000300': '沪深300',
};

/** 负值绿色、正值红色（A 股惯例）。 */
function pctColor(value?: number | null): string {
  if (value == null) return 'text-secondary-text';
  if (value > 0) return 'text-red-500';
  if (value < 0) return 'text-green-600';
  return 'text-foreground';
}

function numColor(value?: number | null): string {
  if (value == null) return 'text-secondary-text';
  if (value > 0) return 'text-red-500';
  if (value < 0) return 'text-green-600';
  return 'text-foreground';
}

export const BacktestSummaryTable: React.FC<{ run: StrategyLabRunItem }> = ({ run }) => {
  const rows = useMemo<SummaryRow[]>(() => {
    const metrics = run.metrics;
    const benchmark = metrics?.benchmark_metrics;
    const excess = benchmark?.relative_excess;
    const mode = benchmark?.mode;
    const benchmarkLabel = mode ? (BENCHMARK_MODE_LABELS[mode] ?? mode) : null;
    const result: SummaryRow[] = [
      {
        key: 'strategy',
        label: '当前策略',
        ...pickMetrics(metrics),
        finalEquity: run.final_equity,
        turnoverAvgPct: metrics?.turnover_avg_pct,
      },
    ];
    if (benchmark && benchmarkLabel) {
      result.push({
        key: 'benchmark',
        label: `基准策略（${benchmarkLabel}）`,
        ...pickMetrics(benchmark),
        finalEquity: deriveBenchmarkEquity(run),
      });
    }
    if (excess) {
      result.push({ key: 'excess', label: '相对超额', ...pickMetrics(excess) });
    }
    return result;
  }, [run]);

  const columns: ColumnsType<SummaryRow> = [
    { title: '策略组合', dataIndex: 'label', width: 190, fixed: 'left' },
    {
      title: '总收益率',
      dataIndex: 'total_return_pct',
      width: 110,
      align: 'right',
      render: (value: number | null) => <span className={pctColor(value)}>{formatPct(value)}</span>,
    },
    {
      title: '累计资产',
      dataIndex: 'finalEquity',
      width: 110,
      align: 'right',
      render: (value: number | null) => (value == null ? '--' : formatNumber(value, 0)),
    },
    {
      title: '年化收益率',
      dataIndex: 'annualized_return_pct',
      width: 110,
      align: 'right',
      render: (value: number | null) => <span className={pctColor(value)}>{formatPct(value)}</span>,
    },
    {
      title: '最大回撤',
      dataIndex: 'max_drawdown_pct',
      width: 100,
      align: 'right',
      render: (value: number | null) => <span className={numColor(value)}>{formatPct(value)}</span>,
    },
    {
      title: '夏普比',
      dataIndex: 'sharpe_ratio',
      width: 90,
      align: 'right',
      render: (value: number | null) => formatNumber(value),
    },
    {
      title: '索提诺比',
      dataIndex: 'sortino_ratio',
      width: 90,
      align: 'right',
      render: (value: number | null) => formatNumber(value),
    },
    {
      title: '卡玛比',
      dataIndex: 'calmar_ratio',
      width: 90,
      align: 'right',
      render: (value: number | null) => formatNumber(value),
    },
    {
      title: '日均换手',
      dataIndex: 'turnoverAvgPct',
      width: 90,
      align: 'right',
      render: (value: number | null, record) => (record.key === 'strategy' ? formatPct(value) : '/'),
    },
    {
      title: '交易周期',
      dataIndex: 'period_count',
      width: 90,
      align: 'right',
      render: (value: number | null) => (value == null ? '--' : String(value)),
    },
    {
      title: '盈利周期',
      dataIndex: 'profit_periods',
      width: 90,
      align: 'right',
      render: (value: number | null) => (value == null ? '--' : String(value)),
    },
    {
      title: '亏损周期',
      dataIndex: 'loss_periods',
      width: 90,
      align: 'right',
      render: (value: number | null) => (value == null ? '--' : String(value)),
    },
  ];

  return (
    <Table
      rowKey="key"
      size="small"
      columns={columns}
      dataSource={rows}
      pagination={false}
      scroll={{ x: 1180 }}
    />
  );
};

function pickMetrics(source?: MetricSource | null): MetricSource {
  return {
    total_return_pct: source?.total_return_pct ?? null,
    annualized_return_pct: source?.annualized_return_pct ?? null,
    max_drawdown_pct: source?.max_drawdown_pct ?? null,
    sharpe_ratio: source?.sharpe_ratio ?? null,
    sortino_ratio: source?.sortino_ratio ?? null,
    calmar_ratio: source?.calmar_ratio ?? null,
    period_count: source?.period_count ?? null,
    profit_periods: source?.profit_periods ?? null,
    loss_periods: source?.loss_periods ?? null,
  };
}

function deriveBenchmarkEquity(run: StrategyLabRunItem): number | null {
  const curve = run.equity_curve ?? [];
  for (let index = curve.length - 1; index >= 0; index -= 1) {
    const value = curve[index].benchmark_equity;
    if (value != null) return value;
  }
  return null;
}
