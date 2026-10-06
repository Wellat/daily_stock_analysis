import { useMemo, useState } from 'react';
import { Segmented } from 'antd';
import { EChart } from '../../common/EChart';
import type { EChartOption } from '../../common/EChart';
import type { StrategyLabRunItem } from '../../../api/strategyLab';

type AxisScale = 'linear' | 'log';

/** 双轴走势图：左轴累计收益率（策略/基准/超额），右轴回撤率。 */
export const BacktestTrendChart: React.FC<{ run: StrategyLabRunItem }> = ({ run }) => {
  const [axisScale, setAxisScale] = useState<AxisScale>('linear');
  const [showExcess, setShowExcess] = useState(true);

  const option = useMemo<EChartOption>(() => {
    const curve = run.equity_curve ?? [];
    const initial = run.initial_cash || 1;
    const dates = curve.map((point) => point.trade_date);
    const strategyReturns = curve.map((point) => (point.equity / initial - 1) * 100);
    const benchmarkReturns = curve.map((point) =>
      point.benchmark_equity != null ? (point.benchmark_equity / initial - 1) * 100 : null,
    );
    const excessReturns = curve.map((point, index) =>
      point.benchmark_equity != null ? strategyReturns[index] - benchmarkReturns[index]! : null,
    );
    const drawdowns = curve.map((point) => (point.drawdown_pct != null ? point.drawdown_pct : null));
    const averageDrawdown = (() => {
      const values = drawdowns.filter((value): value is number => value != null);
      if (!values.length) return null;
      return values.reduce((sum, value) => sum + value, 0) / values.length;
    })();

    const series: NonNullable<EChartOption['series']> = [
      {
        name: '累计收益',
        type: 'line' as const,
        smooth: true,
        showSymbol: false,
        data: strategyReturns.map((value) => Number(value.toFixed(4))),
        lineStyle: { width: 2, color: '#22d3ee' },
      },
      {
        name: '基准收益',
        type: 'line' as const,
        smooth: true,
        showSymbol: false,
        data: benchmarkReturns.map((value) => (value == null ? null : Number(value.toFixed(4)))),
        lineStyle: { width: 1.5, color: '#34d399', type: 'dashed' as const },
      },
      {
        name: '相对超额',
        type: 'line' as const,
        smooth: true,
        showSymbol: false,
        data: showExcess ? excessReturns.map((value) => (value == null ? null : Number(value.toFixed(4)))) : [],
        lineStyle: { width: 1.5, color: '#f59e0b', type: 'dotted' as const },
      },
      {
        name: '回撤率',
        type: 'line' as const,
        yAxisIndex: 1,
        smooth: true,
        showSymbol: false,
        data: drawdowns.map((value) => (value == null ? null : Number(value.toFixed(4)))),
        lineStyle: { width: 1, color: 'rgba(96, 165, 250, 0.9)' },
        areaStyle: { color: 'rgba(96, 165, 250, 0.15)' },
      },
    ];
    if (averageDrawdown != null) {
      series.push({
        name: '平均回撤',
        type: 'line' as const,
        yAxisIndex: 1,
        showSymbol: false,
        data: dates.map(() => Number(averageDrawdown.toFixed(4))),
        lineStyle: { width: 1, color: '#f472b6', type: 'dashed' as const },
        tooltip: { show: false },
      });
    }

    return {
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${Number(value).toFixed(2)}%` },
      legend: { top: 0, textStyle: { color: '#8aa0b6' } },
      grid: { left: 56, right: 56, top: 32, bottom: 32 },
      xAxis: { type: 'category', data: dates },
      yAxis: [
        {
          type: axisScale === 'log' ? 'log' : 'value',
          scale: true,
          axisLabel: { formatter: '{value}%' },
        },
        {
          type: 'value',
          scale: true,
          axisLabel: { formatter: '{value}%' },
          splitLine: { show: false },
        },
      ],
      series,
    };
  }, [run, showExcess, axisScale]);

  const hasCurve = (run.equity_curve?.length ?? 0) > 1;
  if (!hasCurve) {
    return <div className="text-sm text-secondary-text">区间数据不足，无回测走势</div>;
  }
  return (
    <div>
      <div className="mb-2 flex items-center justify-end gap-3">
        <Segmented
          size="small"
          value={showExcess ? 'on' : 'off'}
          onChange={(value) => setShowExcess(value === 'on')}
          options={[{ label: '超额', value: 'on' }, { label: '隐藏', value: 'off' }]}
        />
        <Segmented
          size="small"
          value={axisScale}
          onChange={(value) => setAxisScale(value as AxisScale)}
          options={[{ label: '线性', value: 'linear' }, { label: '对数', value: 'log' }]}
        />
      </div>
      <EChart option={option} height={340} aria-label="回测走势图" />
    </div>
  );
};
