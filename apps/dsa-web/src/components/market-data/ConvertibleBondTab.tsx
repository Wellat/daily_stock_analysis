import { useCallback, useEffect, useRef, useState } from 'react';
import { Descriptions, Empty, Input, Segmented, Skeleton, Switch, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiErrorAlert } from '../common';
import { EChart } from '../common/EChart';
import type { EChartOption } from '../common/EChart';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import {
  strategyLabApi,
  type StrategyLabBarItem,
  type StrategyLabEventItem,
  type StrategyLabInstrumentDetail,
  type StrategyLabInstrumentItem,
} from '../../api/strategyLab';

const formatNumber = (value?: number | null, digits = 2): string => (value == null ? '--' : value.toFixed(digits));
const formatPct = (value?: number | null): string => (value == null ? '--' : `${value.toFixed(2)}%`);

const eventTypeTag = (eventType: string) => {
  const map: Record<string, string> = {
    strong_redeem: 'error',
    down_revise: 'warning',
    put: 'processing',
    new_issue: 'success',
    listing: 'success',
  };
  return <Tag color={map[eventType] ?? 'default'}>{eventType}</Tag>;
};

const statusFilterOptions = [
  { label: '全部', value: 'all' },
  { label: '未退市', value: 'active' },
  { label: '已退市', value: 'delisted' },
];

export const ConvertibleBondTab: React.FC = () => {
  const [instruments, setInstruments] = useState<StrategyLabInstrumentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<StrategyLabInstrumentItem | null>(null);
  const [detail, setDetail] = useState<StrategyLabInstrumentDetail | null>(null);
  const [bars, setBars] = useState<StrategyLabBarItem[]>([]);
  const [events, setEvents] = useState<StrategyLabEventItem[]>([]);
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'delisted'>('active');
  const [heldOnly, setHeldOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const searchTimer = useRef<number | null>(null);

  const loadInstruments = useCallback(async (nextKeyword: string) => {
    setLoading(true);
    try {
      const payload = await strategyLabApi.listInstruments({
        market: 'cn',
        keyword: nextKeyword || undefined,
        status: statusFilter === 'all' ? undefined : statusFilter,
        held_only: heldOnly || undefined,
        page: 1,
        limit: 50,
      });
      setInstruments(payload.items);
      setTotal(payload.total);
      setError(null);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setLoading(false);
    }
  }, [statusFilter, heldOnly]);

  useEffect(() => { void loadInstruments(''); }, [loadInstruments]);

  const handleSearch = (value: string) => {
    if (searchTimer.current != null) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => {
      void loadInstruments(value);
    }, 300);
  };

  const selectInstrument = useCallback(async (item: StrategyLabInstrumentItem) => {
    setSelected(item);
    setDetailLoading(true);
    setError(null);
    try {
      const [detailPayload, barsPayload, eventsPayload] = await Promise.all([
        strategyLabApi.getInstrumentDetail(item.bond_code),
        strategyLabApi.listInstrumentBars(item.bond_code, { limit: 1000 }),
        strategyLabApi.listInstrumentEvents(item.bond_code),
      ]);
      setDetail(detailPayload);
      setBars(barsPayload.items);
      setEvents(eventsPayload.items);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const chartOption: EChartOption = bars.length
    ? {
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis' },
        legend: { data: ['收盘价', '转股溢价率'], textStyle: { color: '#94a3b8' }, top: 0 },
        grid: { left: 56, right: 56, top: 32, bottom: 32 },
        xAxis: { type: 'category', data: bars.map((bar) => bar.trade_date) },
        yAxis: [
          { type: 'value', name: '价格', scale: true },
          { type: 'value', name: '溢价率', scale: true, splitLine: { show: false } },
        ],
        series: [
          {
            name: '收盘价',
            type: 'line',
            smooth: true,
            showSymbol: false,
            data: bars.map((bar) => bar.close),
            lineStyle: { width: 2, color: '#22d3ee' },
            areaStyle: { color: 'rgba(34, 211, 238, 0.12)' },
          },
          {
            name: '转股溢价率',
            type: 'line',
            yAxisIndex: 1,
            smooth: true,
            showSymbol: false,
            data: bars.map((bar) => bar.premium_rate),
            lineStyle: { width: 2, color: '#f59e0b' },
          },
        ],
      }
    : {};

  const eventColumns: ColumnsType<StrategyLabEventItem> = [
    { title: '日期', dataIndex: 'event_date', width: 110 },
    { title: '类型', dataIndex: 'event_type', width: 140, render: eventTypeTag },
    { title: '详情', dataIndex: 'event_detail', ellipsis: true },
    { title: '来源', dataIndex: 'source', width: 100 },
  ];

  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      <div className="glass-panel px-4 py-4">
        <Input.Search
          aria-label="搜索标的"
          placeholder="代码 / 名称 / 正股代码"
          allowClear
          onChange={(event) => handleSearch(event.target.value)}
          className="h-10"
        />
        <div className="mt-3 flex items-center justify-between gap-2">
          <Segmented
            aria-label="状态筛选"
            size="small"
            options={statusFilterOptions}
            value={statusFilter}
            onChange={(value) => setStatusFilter(value as 'all' | 'active' | 'delisted')}
          />
          <label className="flex items-center gap-1.5 text-xs text-secondary-text">
            <Switch size="small" checked={heldOnly} onChange={setHeldOnly} />
            仅持仓
          </label>
        </div>
        <div className="mt-3 text-xs text-secondary-text">共 {total} 只标的</div>
        <div className="mt-2 max-h-[58vh] space-y-1.5 overflow-auto pr-1">
          {loading ? <Skeleton active paragraph={{ rows: 6 }} /> : null}
          {!loading && instruments.length === 0 ? <Empty description="暂无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : null}
          {instruments.map((item) => (
            <button
              key={item.bond_code}
              type="button"
              className={`w-full rounded-xl border px-3 py-2.5 text-left transition-colors ${
                selected?.bond_code === item.bond_code
                  ? 'border-cyan/40 bg-cyan/10'
                  : 'border-border/50 bg-card/40 hover:bg-card/80'
              }`}
              onClick={() => void selectInstrument(item)}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-sm text-foreground">{item.bond_code}</span>
                <span className="text-xs text-secondary-text">{formatPct(item.latest_premium_rate)}</span>
              </div>
              <div className="mt-0.5 flex items-center justify-between gap-2 text-xs text-secondary-text">
                <span className="truncate">{item.bond_name}</span>
                <span className={item.status === '已退市' ? 'text-danger' : ''}>
                  {item.status ?? '--'} · {item.latest_close == null ? '--' : item.latest_close}
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-4">
        {error ? <ApiErrorAlert error={error} /> : null}
        {selected == null ? (
          <div className="glass-panel flex min-h-[24rem] items-center justify-center px-4 py-4">
            <Empty description="从左侧选择一只可转债查看详情" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        ) : (
          <>
            <div className="glass-panel px-4 py-4">
              {detailLoading || detail == null ? (
                <Skeleton active paragraph={{ rows: 4 }} />
              ) : (
                <>
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <h2 className="text-base font-semibold text-foreground">
                      {detail.bond_code} {detail.bond_name}
                      <span className="ml-2 text-sm font-normal text-secondary-text">{detail.stock_name || detail.stock_code}</span>
                      <Tag className="ml-2" color={detail.status === '已退市' ? 'error' : 'success'}>{detail.status ?? '--'}</Tag>
                    </h2>
                    <span className="font-mono text-lg font-semibold text-cyan">{formatNumber(detail.current_premium_rate)}%</span>
                  </div>
                  <Descriptions size="small" column={{ xs: 2, md: 3, xl: 4 }} className="mt-3">
                    <Descriptions.Item label="转股价">{formatNumber(detail.convert_price)}</Descriptions.Item>
                    <Descriptions.Item label="当前溢价率">{formatPct(detail.current_premium_rate)}</Descriptions.Item>
                    <Descriptions.Item label="剩余规模">{formatNumber(detail.remaining_size, 1)} 亿</Descriptions.Item>
                    <Descriptions.Item label="最新收盘">{formatNumber(detail.latest_close)}</Descriptions.Item>
                    <Descriptions.Item label="上市日期">{detail.list_date ?? '--'}</Descriptions.Item>
                    <Descriptions.Item label="到期日期">{detail.maturity_date ?? '--'}</Descriptions.Item>
                    <Descriptions.Item label="日线条数">{detail.bar_count}</Descriptions.Item>
                    <Descriptions.Item label="事件数量">{detail.event_count}</Descriptions.Item>
                  </Descriptions>
                  <div className="mt-3 grid gap-2 text-xs text-secondary-text lg:grid-cols-3">
                    <div><span className="text-muted-text">强赎条款：</span>{detail.redeem_clause || '--'}</div>
                    <div><span className="text-muted-text">下修条款：</span>{detail.down_revise_clause || '--'}</div>
                    <div><span className="text-muted-text">回售条款：</span>{detail.put_clause || '--'}</div>
                  </div>
                </>
              )}
            </div>

            <div className="glass-panel px-4 py-4">
              <h3 className="mb-3 text-sm font-semibold text-foreground">价格与溢价率</h3>
              {detailLoading ? <Skeleton active paragraph={{ rows: 5 }} /> : <EChart option={chartOption} height={320} aria-label="价格与溢价率图表" />}
            </div>

            <div className="glass-panel px-4 py-4">
              <h3 className="mb-3 text-sm font-semibold text-foreground">事件记录</h3>
              <Table
                rowKey={(event) => `${event.event_date}-${event.event_type}`}
                size="small"
                columns={eventColumns}
                dataSource={events}
                pagination={false}
                loading={detailLoading}
                locale={{ emptyText: '暂无事件记录' }}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
};
