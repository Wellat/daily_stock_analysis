import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Segmented, Select, Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { RefreshCw } from 'lucide-react';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { strategyLabApi } from '../../api/strategyLab';
import type { StrategyLabPremiumTrackBond } from '../../api/strategyLab';
import { ApiErrorAlert, EChart, EmptyState, Loading, StatCard } from '../common';
import type { EChartOption } from '../common';

const INPUT_CLASS = 'input-surface input-focus-glow h-10 w-full rounded-xl border bg-transparent px-3 text-sm';

type RangeMode = '7d' | '30d' | '90d' | 'custom';

const TOP_N_OPTIONS = [5, 10, 20].map((n) => ({ value: n, label: `前 ${n} 只` }));

function toDateStr(daysAgo: number): string {
  return new Date(Date.now() - daysAgo * 86400000).toISOString().slice(0, 10);
}

function bondLabel(bond: StrategyLabPremiumTrackBond): string {
  return bond.bond_name ? `${bond.bond_name}（${bond.bond_code}）` : bond.bond_code;
}

export const LowPremiumTrackPanel: React.FC = () => {
  const [rangeMode, setRangeMode] = useState<RangeMode>('90d');
  const [startDate, setStartDate] = useState(toDateStr(89));
  const [endDate, setEndDate] = useState(toDateStr(0));
  const [topN, setTopN] = useState(10);
  const [track, setTrack] = useState<Awaited<ReturnType<typeof strategyLabApi.getPremiumTrack>> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const loadTrack = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await strategyLabApi.getPremiumTrack({
        start: startDate || undefined,
        end: endDate || undefined,
        top_n: topN,
      });
      setTrack(result);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate, topN]);

  useEffect(() => {
    void loadTrack();
  }, [loadTrack]);

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

  // 在榜分布散点图：y 轴按在榜天数降序（最稳定的在顶部），x 轴为交易日
  const membershipOption: EChartOption = useMemo(() => {
    if (!track || track.bonds.length === 0 || track.dates.length === 0) return {};
    const yLabels = track.bonds.map(bondLabel);
    const scatterData = track.bonds.flatMap((bond, bondIdx) =>
      bond.day_indexes.map((dateIdx, i) => [dateIdx, bondIdx, bond.premiums[i]]),
    );
    return {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        formatter: (params) => {
          const value = (params as unknown as { value: [number, number, number] }).value;
          const [dateIdx, bondIdx, premium] = value;
          return `${yLabels[bondIdx]}<br/>${track.dates[dateIdx]}：溢价率 ${premium.toFixed(2)}%`;
        },
      },
      grid: { left: 150, right: 24, top: 16, bottom: 40 },
      xAxis: { type: 'category', data: track.dates, axisLabel: { color: '#94a3b8', rotate: 45 } },
      yAxis: { type: 'category', data: yLabels, axisLabel: { color: '#94a3b8' } },
      dataZoom: [{ type: 'inside', xAxisIndex: 0 }],
      series: [
        {
          name: '在榜',
          type: 'scatter',
          symbolSize: 9,
          itemStyle: { color: 'rgba(34, 211, 238, 0.85)' },
          data: scatterData,
        },
      ],
    };
  }, [track]);

  const turnoverOption: EChartOption = useMemo(() => {
    if (!track || track.turnover.length === 0) return {};
    const dates = track.turnover.map((item) => item.date);
    return {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis' },
      legend: { textStyle: { color: '#94a3b8' }, top: 0 },
      grid: { left: 48, right: 56, top: 32, bottom: 40 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#94a3b8', rotate: 45 } },
      yAxis: [
        { type: 'value', name: '只数', min: 0, max: track.top_n },
        { type: 'value', name: '门槛%', scale: true, splitLine: { show: false } },
      ],
      series: [
        {
          name: '与前日重叠',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: track.turnover.map((item) => item.overlap ?? null),
          lineStyle: { width: 2, color: '#22d3ee' },
        },
        {
          name: '新进',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: track.turnover.map((item) => item.entered ?? null),
          lineStyle: { width: 2, color: '#4ade80' },
        },
        {
          name: '退出',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: track.turnover.map((item) => item.exited ?? null),
          lineStyle: { width: 2, color: '#f87171' },
        },
        {
          name: `第${track.top_n}名门槛溢价率`,
          type: 'line',
          smooth: true,
          showSymbol: false,
          yAxisIndex: 1,
          data: track.turnover.map((item) => item.threshold ?? null),
          lineStyle: { width: 2, color: '#f59e0b' },
        },
      ],
    };
  }, [track]);

  const bondColumns: ColumnsType<StrategyLabPremiumTrackBond> = [
    {
      title: '标的',
      dataIndex: 'bond_code',
      width: 190,
      render: (_, record) => bondLabel(record),
    },
    { title: '在榜天数', dataIndex: 'days_count', width: 100, align: 'right' },
    {
      title: '占比',
      dataIndex: 'ratio',
      width: 90,
      align: 'right',
      render: (value: number) => `${(value * 100).toFixed(1)}%`,
    },
    { title: '首次上榜', dataIndex: 'first_date', width: 110, render: (v) => v ?? '-' },
    { title: '最近上榜', dataIndex: 'last_date', width: 110, render: (v) => v ?? '-' },
    {
      title: '平均溢价率',
      dataIndex: 'avg_premium',
      width: 110,
      align: 'right',
      render: (v) => (v == null ? '--' : `${v.toFixed(2)}%`),
    },
    { title: '最好名次', dataIndex: 'best_rank', width: 90, align: 'right' },
  ];

  const stats = track?.stats;
  const hasData = !!track && track.dates.length > 0 && track.bonds.length > 0;

  return (
    <div className="space-y-4">
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
        <Select
          aria-label="榜单规模"
          className="w-28"
          value={topN}
          options={TOP_N_OPTIONS}
          onChange={(value) => setTopN(value)}
        />
        <button type="button" className="btn-primary" disabled={loading} onClick={() => void loadTrack()}>
          <RefreshCw className="h-4 w-4" />
          刷新
        </button>
        <p className="text-xs text-secondary-text">
          每个交易日取转股溢价率最低的 {topN} 只，观察榜单成员的稳定性与换手。
        </p>
      </div>

      {error ? (
        <ApiErrorAlert error={error} actionLabel="重试" onAction={() => void loadTrack()} />
      ) : null}

      {loading ? (
        <Loading />
      ) : hasData && track && stats ? (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <StatCard label="窗口交易日" value={stats.window_days} hint={`${track.start} ~ ${track.end}`} />
            <StatCard label="上榜转债数" value={stats.distinct_bonds} hint={`日均新进 ${stats.avg_entered ?? '--'} 只`} />
            <StatCard
              label="日均与前日重叠"
              value={stats.avg_overlap == null ? '--' : `${stats.avg_overlap} / ${track.top_n}`}
              hint="越高说明榜单越稳定"
            />
            <StatCard
              label={`第${track.top_n}名门槛溢价率`}
              value={
                track.turnover.length > 0 && track.turnover[track.turnover.length - 1].threshold != null
                  ? `${track.turnover[track.turnover.length - 1].threshold!.toFixed(2)}%`
                  : '--'
              }
              hint="最近一个交易日的入榜门槛"
            />
          </div>

          <div className="glass-panel px-4 py-4">
            <h3 className="text-sm font-semibold text-foreground mb-2">在榜分布（越靠上越稳定）</h3>
            <EChart
              option={membershipOption}
              height={Math.max(280, Math.min(640, 90 + track.bonds.length * 18))}
              aria-label="低溢价在榜分布图"
            />
          </div>

          <div className="glass-panel px-4 py-4">
            <h3 className="text-sm font-semibold text-foreground mb-2">稳定性曲线</h3>
            <EChart option={turnoverOption} height={300} aria-label="低溢价稳定性曲线" />
          </div>

          <div className="glass-panel px-4 py-4">
            <h3 className="text-sm font-semibold text-foreground mb-2">上榜统计</h3>
            <Table<StrategyLabPremiumTrackBond>
              size="small"
              rowKey="bond_code"
              columns={bondColumns}
              dataSource={track.bonds}
              pagination={{ pageSize: 10, hideOnSinglePage: true }}
            />
          </div>
        </>
      ) : !error ? (
        <EmptyState title="暂无榜单数据" description="所选范围内没有已同步的转股溢价率数据，先在行情数据页完成可转债因子同步。" />
      ) : null}
    </div>
  );
};
