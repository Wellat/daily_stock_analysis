import { useMemo, useState } from 'react';
import { Segmented } from 'antd';
import { EChart } from '../../common/EChart';
import type { EChartOption } from '../../common/EChart';
import type { StrategyLabEquityPoint } from '../../../api/strategyLab';

type Granularity = 'year' | 'month' | 'week';

/** 回报分布：策略/基准/超额的年/月/周分组柱状图，全部从同一 equity 曲线派生。 */
export const ReturnDistribution: React.FC<{ equityCurve?: StrategyLabEquityPoint[]; initialCash: number }> = ({
  equityCurve,
  initialCash,
}) => {
  const [granularity, setGranularity] = useState<Granularity>('year');

  const option = useMemo<EChartOption>(() => {
    const groups = groupReturns(equityCurve ?? [], initialCash, granularity);
    const labels = groups.map((group) => group.label);
    return {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${Number(value).toFixed(2)}%` },
      legend: { top: 0, textStyle: { color: '#8aa0b6' } },
      grid: { left: 56, right: 16, top: 32, bottom: 32 },
      xAxis: { type: 'category', data: labels },
      yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
      series: [
        {
          name: '策略收益',
          type: 'bar' as const,
          data: groups.map((group) => round(group.strategyPct)),
          itemStyle: { color: '#22d3ee' },
        },
        {
          name: '基准收益',
          type: 'bar' as const,
          data: groups.map((group) => (group.benchmarkPct == null ? null : round(group.benchmarkPct))),
          itemStyle: { color: '#34d399' },
        },
        {
          name: '超额收益',
          type: 'bar' as const,
          data: groups.map((group) => (group.benchmarkPct == null ? null : round(group.strategyPct - group.benchmarkPct))),
          itemStyle: { color: '#64748b' },
        },
      ],
    };
  }, [equityCurve, initialCash, granularity]);

  if ((equityCurve?.length ?? 0) < 2) {
    return <div className="text-sm text-secondary-text">区间数据不足，无回报分布</div>;
  }
  return (
    <div>
      <div className="mb-2 flex items-center justify-end">
        <Segmented
          size="small"
          value={granularity}
          onChange={(value) => setGranularity(value as Granularity)}
          options={[
            { label: '年度回报', value: 'year' },
            { label: '月度回报', value: 'month' },
            { label: '周度回报', value: 'week' },
          ]}
        />
      </div>
      <EChart option={option} height={280} aria-label="回报分布" />
    </div>
  );
};

type ReturnGroup = { label: string; strategyPct: number; benchmarkPct: number | null };

function groupReturns(
  curve: StrategyLabEquityPoint[],
  initialCash: number,
  granularity: Granularity,
): ReturnGroup[] {
  const groups = new Map<string, { strategy: number; benchmark: number | null; benchmarkBase: number | null }>();
  let previousStrategy = initialCash;
  let previousBenchmark: number | null = initialCash;
  for (const point of curve) {
    const key = groupKey(point.trade_date, granularity);
    const bucket = groups.get(key) ?? { strategy: 1, benchmark: 1, benchmarkBase: previousBenchmark };
    if (!groups.has(key)) {
      bucket.strategy = previousStrategy;
      bucket.benchmark = previousBenchmark;
      bucket.benchmarkBase = previousBenchmark;
    }
    bucket.strategy = bucket.strategy * (1 + (point.daily_return_pct ?? 0) / 100);
    if (point.benchmark_equity != null && previousBenchmark != null) {
      const benchmarkDaily = point.benchmark_equity / previousBenchmark - 1;
      bucket.benchmark = (bucket.benchmark ?? 1) * (1 + benchmarkDaily);
    }
    previousStrategy = point.equity;
    previousBenchmark = point.benchmark_equity ?? previousBenchmark;
    groups.set(key, bucket);
  }
  return [...groups.entries()].map(([label, bucket]) => ({
    label,
    strategyPct: (bucket.strategy / initialCash - 1) * 100,
    benchmarkPct:
      bucket.benchmark != null && bucket.benchmarkBase != null && bucket.benchmarkBase > 0
        ? (bucket.benchmark / bucket.benchmarkBase - 1) * 100
        : null,
  }));
}

function groupKey(tradeDate: string, granularity: Granularity): string {
  if (granularity === 'year') return `${tradeDate.slice(0, 4)}年`;
  if (granularity === 'month') return tradeDate.slice(0, 7);
  return isoWeekKey(tradeDate);
}

function isoWeekKey(tradeDate: string): string {
  const date = new Date(`${tradeDate}T00:00:00Z`);
  const day = date.getUTCDay() || 7;
  const thursday = new Date(date);
  thursday.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(thursday.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((thursday.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${thursday.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

function round(value: number): number {
  return Number(value.toFixed(4));
}
