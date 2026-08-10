import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

export type EChartOption = echarts.EChartsOption;

type EChartProps = {
  option: EChartOption;
  height?: number | string;
  className?: string;
  'aria-label'?: string;
};

/** Lightweight ECharts wrapper with resize handling and lifecycle cleanup. */
export const EChart: React.FC<EChartProps> = ({ option, height = 320, className, ...rest }) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const chart = echarts.init(containerRef.current);
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true });
  }, [option]);

  return (
    <div
      ref={containerRef}
      className={className}
      role="img"
      aria-label={rest['aria-label']}
      style={{ height, width: '100%' }}
    />
  );
};
