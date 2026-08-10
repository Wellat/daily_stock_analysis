import { useCallback, useEffect, useRef, useState } from 'react';
import { Empty, Input, Skeleton, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiErrorAlert } from '../common';
import { EChart } from '../common/EChart';
import type { EChartOption } from '../common/EChart';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { marketStocksApi, type StockDailyBarItem, type StockListItem } from '../../api/stocks';

const formatNumber = (value?: number | null, digits = 2): string => (value == null ? '--' : value.toFixed(digits));

const instrumentTag = (instrumentType: string) => {
  const map: Record<string, string> = { stock: 'blue', hk_stock: 'purple', us_stock: 'cyan' };
  return <Tag color={map[instrumentType] ?? 'default'}>{instrumentType}</Tag>;
};

const hasFullOHLC = (bars: StockDailyBarItem[]): boolean =>
  bars.every((bar) => bar.open != null && bar.high != null && bar.low != null && bar.close != null);

export const StockTab: React.FC = () => {
  const [stocks, setStocks] = useState<StockListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<StockListItem | null>(null);
  const [bars, setBars] = useState<StockDailyBarItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [barsLoading, setBarsLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const searchTimer = useRef<number | null>(null);

  const loadStocks = useCallback(async (nextKeyword: string) => {
    setLoading(true);
    try {
      const payload = await marketStocksApi.listStocks({ keyword: nextKeyword || undefined, page: 1, limit: 50 });
      setStocks(payload.items);
      setTotal(payload.total);
      setError(null);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadStocks(''); }, [loadStocks]);

  const handleSearch = (value: string) => {
    if (searchTimer.current != null) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => {
      void loadStocks(value);
    }, 300);
  };

  const selectStock = useCallback(async (item: StockListItem) => {
    setSelected(item);
    setBarsLoading(true);
    setError(null);
    try {
      const payload = await marketStocksApi.getStockBars(item.code, { limit: 1000 });
      setBars(payload.items);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setBarsLoading(false);
    }
  }, []);

  const fullOHLC = hasFullOHLC(bars);
  const chartOption: EChartOption = bars.length
    ? {
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis' },
        grid: { left: 56, right: 16, top: 16, bottom: 64 },
        xAxis: { type: 'category', data: bars.map((bar) => bar.date ?? '') },
        yAxis: { type: 'value', scale: true },
        dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 8 }],
        series: fullOHLC
          ? [{
              name: 'K线',
              type: 'candlestick',
              data: bars.map((bar) => [bar.open ?? 0, bar.close ?? 0, bar.low ?? 0, bar.high ?? 0]),
              itemStyle: {
                color: '#f87171',
                color0: '#4ade80',
                borderColor: '#f87171',
                borderColor0: '#4ade80',
              },
            }]
          : [{
              name: '收盘价',
              type: 'line',
              smooth: true,
              showSymbol: false,
              data: bars.map((bar) => bar.close),
              lineStyle: { width: 2, color: '#22d3ee' },
              areaStyle: { color: 'rgba(34, 211, 238, 0.12)' },
            }],
      }
    : {};

  const barColumns: ColumnsType<StockDailyBarItem> = [
    { title: '日期', dataIndex: 'date', width: 110 },
    { title: '开盘', dataIndex: 'open', align: 'right', render: (v: number | null) => formatNumber(v) },
    { title: '最高', dataIndex: 'high', align: 'right', render: (v: number | null) => formatNumber(v) },
    { title: '最低', dataIndex: 'low', align: 'right', render: (v: number | null) => formatNumber(v) },
    { title: '收盘', dataIndex: 'close', align: 'right', render: (v: number | null) => <span className="font-medium">{formatNumber(v)}</span> },
    { title: '成交量', dataIndex: 'volume', align: 'right', render: (v: number | null) => v == null ? '--' : Math.round(v).toLocaleString() },
    { title: '来源', dataIndex: 'data_source', width: 120, ellipsis: true },
  ];

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      <div className="glass-panel px-4 py-4">
        <Input.Search
          aria-label="搜索股票"
          placeholder="股票代码"
          allowClear
          onChange={(event) => handleSearch(event.target.value)}
          className="h-10"
        />
        <div className="mt-3 text-xs text-secondary-text">共 {total} 只标的</div>
        <div className="mt-2 max-h-[62vh] space-y-1.5 overflow-auto pr-1">
          {loading ? <Skeleton active paragraph={{ rows: 6 }} /> : null}
          {!loading && stocks.length === 0 ? <Empty description="暂无股票日线数据" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : null}
          {stocks.map((item) => (
            <button
              key={item.code}
              type="button"
              className={`w-full rounded-xl border px-3 py-2.5 text-left transition-colors ${
                selected?.code === item.code ? 'border-cyan/40 bg-cyan/10' : 'border-border/50 bg-card/40 hover:bg-card/80'
              }`}
              onClick={() => void selectStock(item)}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-sm text-foreground">{item.code}</span>
                <span className="text-xs text-secondary-text">{formatNumber(item.latest_close)}</span>
              </div>
              <div className="mt-0.5 flex items-center justify-between gap-2 text-xs text-secondary-text">
                <span>{instrumentTag(item.instrument_type)}</span>
                <span>{item.latest_date ?? '--'}</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-4">
        {error ? <ApiErrorAlert error={error} /> : null}
        {selected == null ? (
          <div className="glass-panel flex min-h-[24rem] items-center justify-center px-4 py-4">
            <Empty description="从左侧选择一只股票查看历史行情" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        ) : (
          <>
            <div className="glass-panel px-4 py-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-base font-semibold text-foreground">
                  {selected.code}
                  <span className="ml-2">{instrumentTag(selected.instrument_type)}</span>
                </h2>
                <span className="font-mono text-lg font-semibold text-cyan">{formatNumber(selected.latest_close)}</span>
              </div>
              <div className="mt-1 text-xs text-secondary-text">最新交易日：{selected.latest_date ?? '--'} · 共 {bars.length} 条日线</div>
            </div>

            <div className="glass-panel px-4 py-4">
              <h3 className="mb-3 text-sm font-semibold text-foreground">{fullOHLC ? 'K 线图' : '收盘价走势'}</h3>
              {barsLoading ? <Skeleton active paragraph={{ rows: 5 }} /> : <EChart option={chartOption} height={360} aria-label="股票K线图表" />}
            </div>

            <div className="glass-panel px-4 py-4">
              <h3 className="mb-3 text-sm font-semibold text-foreground">日线明细</h3>
              <Table
                rowKey="date"
                size="small"
                columns={barColumns}
                dataSource={bars}
                pagination={{ pageSize: 10, showSizeChanger: false }}
                loading={barsLoading}
                locale={{ emptyText: '暂无日线数据' }}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
};
