import { useCallback, useEffect, useRef, useState } from 'react';
import { Descriptions, Empty, Input, Segmented, Skeleton, Switch, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { TablePaginationConfig } from 'antd/es/table';
import type { FilterValue, SorterResult } from 'antd/es/table/interface';
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
  type StrategyLabStockBarItem,
} from '../../api/strategyLab';

const PAGE_SIZE = 50;
type SortField = 'premium_rate' | 'double_low' | 'last_trading_date';
type SortOrder = 'asc' | 'desc';
const DEFAULT_SORT: { field: SortField; order: SortOrder } = { field: 'premium_rate', order: 'asc' };

const formatNumber = (value?: number | null, digits = 2): string => (value == null ? '--' : value.toFixed(digits));
const formatPct = (value?: number | null): string => (value == null ? '--' : `${value.toFixed(2)}%`);
const formatPrice = (value?: number | null): string => (value == null ? '--' : value.toFixed(2));

const eventTypeTag = (eventType: string) => {
  const map: Record<string, string> = {
    strong_redeem: 'error',
    force_redemption: 'error',
    down_revise: 'warning',
    no_revise: 'warning',
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

const statusLabel = (status?: string | null) => status === 'delisted' ? '已退市' : status === 'active' ? '未退市' : (status ?? '--');

// 剩余年限（年，1 位小数由调用方格式化）；已到期返回负数，缺失返回 null
const yearsToMaturity = (maturityDate?: string | null): number | null => {
  if (!maturityDate) return null;
  const target = new Date(`${maturityDate}T00:00:00`);
  if (Number.isNaN(target.getTime())) return null;
  return (target.getTime() - Date.now()) / (365 * 24 * 3600 * 1000);
};

// 条款计数（集思录原样字符串，如「15/30」「已公告强赎」），已公告状态红色突出
const countdownTag = (value?: string | null) => {
  if (!value) return <span className="text-secondary-text">--</span>;
  return <Tag color={value.includes('已公告') ? 'error' : 'default'}>{value}</Tag>;
};

const termText = (terms: Record<string, unknown> | undefined, key: string): string | undefined => {
  const value = terms?.[key];
  return typeof value === 'string' && value ? value : undefined;
};

// 最后交易日缺失时用到期时间兜底展示（弱色区分，避免误读为已公告强赎）
const exitDateCell = (item: StrategyLabInstrumentItem) => {
  if (item.last_trading_date) return item.last_trading_date;
  if (item.maturity_date) return <span className="text-secondary-text">{item.maturity_date}</span>;
  return <span className="text-secondary-text">--</span>;
};

export const ConvertibleBondTab: React.FC = () => {
  const [instruments, setInstruments] = useState<StrategyLabInstrumentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState('');
  const [sort, setSort] = useState<{ field: SortField; order: SortOrder }>(DEFAULT_SORT);
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [selected, setSelected] = useState<StrategyLabInstrumentItem | null>(null);
  const [detail, setDetail] = useState<StrategyLabInstrumentDetail | null>(null);
  const [bars, setBars] = useState<StrategyLabBarItem[]>([]);
  const [stockBars, setStockBars] = useState<StrategyLabStockBarItem[]>([]);
  const [stockLoading, setStockLoading] = useState(false);
  const [events, setEvents] = useState<StrategyLabEventItem[]>([]);
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'delisted'>('active');
  const [heldOnly, setHeldOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const searchTimer = useRef<number | null>(null);

  const loadInstruments = useCallback(async (
    nextKeyword: string,
    nextPage: number,
    nextSort: { field: SortField; order: SortOrder },
  ) => {
    setLoading(true);
    try {
      const payload = await strategyLabApi.listInstruments({
        market: 'cn',
        keyword: nextKeyword || undefined,
        status: statusFilter === 'all' ? undefined : statusFilter,
        held_only: heldOnly || undefined,
        page: nextPage,
        limit: PAGE_SIZE,
        sort_by: nextSort.field,
        sort_order: nextSort.order,
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

  useEffect(() => {
    setPage(1);
    setExpandedKeys([]);
    void loadInstruments(keyword, 1, sort);
  }, [loadInstruments]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSearch = (value: string) => {
    setKeyword(value);
    if (searchTimer.current != null) window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => {
      setPage(1);
      setExpandedKeys([]);
      void loadInstruments(value, 1, sort);
    }, 300);
  };

  const handleTableChange = (
    pagination: TablePaginationConfig,
    _filters: Record<string, FilterValue | null>,
    sorter: SorterResult<StrategyLabInstrumentItem> | SorterResult<StrategyLabInstrumentItem>[],
    extra: { action: 'paginate' | 'sort' | 'filter' },
  ) => {
    if (extra.action === 'sort') {
      // 第三态（取消排序）回到默认：转股溢价率升序；翻页/排序均回到第 1 页。
      // 双低/最后交易日列只有 key 没有 dataIndex，取 columnKey 优先。
      const sorterOne = Array.isArray(sorter) ? sorter[0] : sorter;
      const field = sorterOne?.columnKey ?? sorterOne?.field;
      const nextSort = sorterOne?.order && field
        ? { field: field as SortField, order: sorterOne.order === 'descend' ? 'desc' as const : 'asc' as const }
        : DEFAULT_SORT;
      setSort(nextSort);
      setPage(1);
      setExpandedKeys([]);
      void loadInstruments(keyword, 1, nextSort);
    } else if (extra.action === 'paginate' && pagination.current) {
      setPage(pagination.current);
      setExpandedKeys([]);
      void loadInstruments(keyword, pagination.current, sort);
    }
  };

  const loadDetail = useCallback(async (item: StrategyLabInstrumentItem) => {
    setSelected(item);
    setDetailLoading(true);
    setStockLoading(true);
    setError(null);
    try {
      const [detailPayload, barsPayload, eventsPayload] = await Promise.all([
        strategyLabApi.getInstrumentDetail(item.bond_code),
        strategyLabApi.listInstrumentBars(item.bond_code, { limit: 500 }),
        strategyLabApi.listInstrumentEvents(item.bond_code),
      ]);
      setDetail(detailPayload);
      setBars(barsPayload.items);
      setEvents(eventsPayload.items);
      // 正股 K 线按因子日期窗口实时拉取，失败降级为空（不影响详情主体加载）
      try {
        const stockPayload = await strategyLabApi.listInstrumentStockBars(item.bond_code, {
          start_date: barsPayload.items[0]?.trade_date,
          end_date: barsPayload.items[barsPayload.items.length - 1]?.trade_date,
        });
        setStockBars(stockPayload.items);
      } catch {
        setStockBars([]);
      }
    } catch (exc) {
      setError(getParsedApiError(exc));
      setStockBars([]);
    } finally {
      setDetailLoading(false);
      setStockLoading(false);
    }
  }, []);

  // 点击行展开 / 再点收起；同一时间只展开一行，展开即拉取详情
  const toggleRow = useCallback((item: StrategyLabInstrumentItem) => {
    if (expandedKeys.includes(item.bond_code)) {
      setExpandedKeys([]);
      return;
    }
    setExpandedKeys([item.bond_code]);
    void loadDetail(item);
  }, [expandedKeys, loadDetail]);

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

  const stockCloseByDate = new Map(stockBars.map((bar) => [bar.trade_date, bar.close ?? null]));
  const stockChartOption: EChartOption =
    bars.length && stockBars.length
      ? {
          backgroundColor: 'transparent',
          tooltip: { trigger: 'axis' },
          legend: { data: ['正股收盘价', '转股溢价率'], textStyle: { color: '#94a3b8' }, top: 0 },
          grid: { left: 56, right: 56, top: 32, bottom: 32 },
          xAxis: { type: 'category', data: bars.map((bar) => bar.trade_date) },
          yAxis: [
            { type: 'value', name: '正股价格', scale: true },
            { type: 'value', name: '溢价率', scale: true, splitLine: { show: false } },
          ],
          series: [
            {
              name: '正股收盘价',
              type: 'line',
              smooth: true,
              showSymbol: false,
              // 按交易日对齐：正股缺失的日期断线（腾讯接口单次最多约 800 根）
              data: bars.map((bar) => stockCloseByDate.get(bar.trade_date) ?? null),
              connectNulls: false,
              lineStyle: { width: 2, color: '#4ade80' },
              areaStyle: { color: 'rgba(74, 222, 128, 0.10)' },
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

  const sortOrderOf = (field: SortField): 'ascend' | 'descend' | undefined =>
    sort.field === field ? (sort.order === 'asc' ? 'ascend' : 'descend') : undefined;

  const columns: ColumnsType<StrategyLabInstrumentItem> = [
    {
      title: '代码 / 名称',
      dataIndex: 'bond_code',
      width: 150,
      fixed: 'left',
      render: (_, item) => (
        <div className="flex items-start gap-1.5">
          <span
            className="mt-0.5 inline-block text-[10px] text-secondary-text transition-transform"
            style={{ transform: expandedKeys.includes(item.bond_code) ? 'rotate(90deg)' : 'none' }}
          >
            ▶
          </span>
          <div>
            <div className="font-mono text-sm text-foreground">{item.bond_code}</div>
            <div className="max-w-[110px] truncate text-xs text-secondary-text" title={item.bond_name}>{item.bond_name}</div>
          </div>
        </div>
      ),
    },
    { title: '最新价', dataIndex: 'latest_close', width: 80, align: 'right', render: (v: number | null) => formatPrice(v) },
    {
      title: '转股溢价率',
      dataIndex: 'latest_premium_rate',
      key: 'premium_rate',
      width: 95,
      align: 'right',
      sorter: true,
      sortOrder: sortOrderOf('premium_rate'),
      render: (v: number | null) => (
        <span style={v != null && v < 0 ? { color: '#4ade80' } : undefined}>{formatPct(v)}</span>
      ),
    },
    {
      title: '双低',
      key: 'double_low',
      width: 75,
      align: 'right',
      sorter: true,
      sortOrder: sortOrderOf('double_low'),
      render: (_, item) => (
        item.latest_close != null && item.latest_premium_rate != null
          ? formatNumber(item.latest_close + item.latest_premium_rate)
          : '--'
      ),
    },
    { title: '强赎计数', dataIndex: 'force_redeem_countdown', width: 110, render: (v: string | null) => countdownTag(v) },
    { title: '下修计数', dataIndex: 'down_revise_countdown', width: 100, render: (v: string | null) => v ?? '--' },
    { title: '回售计数', dataIndex: 'put_countdown', width: 100, render: (v: string | null) => v ?? '--' },
    { title: '剩余规模(亿)', dataIndex: 'remaining_size', width: 100, align: 'right', render: (v: number | null) => formatNumber(v, 1) },
    {
      title: '剩余年限',
      dataIndex: 'maturity_date',
      width: 85,
      align: 'right',
      render: (v: string | null) => {
        const years = yearsToMaturity(v);
        return years == null ? '--' : formatNumber(years, 1);
      },
    },
    { title: '到期时间', dataIndex: 'maturity_date', width: 100, render: (v: string | null) => v ?? '--' },
    {
      title: '最后交易日',
      key: 'last_trading_date',
      width: 100,
      sorter: true,
      sortOrder: sortOrderOf('last_trading_date'),
      render: (_, item) => exitDateCell(item),
    },
    { title: '评级', dataIndex: 'bond_rating', width: 80, render: (v: string | null) => v ?? '--' },
  ];

  const eventColumns: ColumnsType<StrategyLabEventItem> = [
    { title: '日期', dataIndex: 'event_date', width: 110 },
    { title: '类型', dataIndex: 'event_type', width: 140, render: eventTypeTag },
    { title: '详情', dataIndex: 'event_detail', ellipsis: true },
    { title: '来源', dataIndex: 'source', width: 100 },
  ];

  const stockCode = detail?.stock_code || selected?.stock_code || '--';
  const stockName = detail?.stock_name || selected?.stock_name || '--';
  const industry = detail?.industry || termText(detail?.terms, 'industry') || '--';
  const currentPremiumRate = detail?.current_premium_rate ?? selected?.current_premium_rate ?? detail?.latest_premium_rate ?? selected?.latest_premium_rate;
  const latestClose = detail?.latest_close ?? selected?.latest_close;
  const detailYears = yearsToMaturity(detail?.maturity_date);
  // 转股价值 = 正股最新收盘 × 100 ÷ 转股价（复用第二张图已拉取的正股 K 线，零额外请求）
  const convertValue = detail?.convert_price && stockBars.length
    ? (stockBars[stockBars.length - 1].close ?? 0) * 100 / detail.convert_price
    : null;

  const detailPanels = (
    <>
      <div className="glass-panel px-4 py-4">
        {detailLoading || detail == null ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : (
          <>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-base font-semibold text-foreground">
                {detail.bond_code} {detail.bond_name}
                <span className="ml-2 text-sm font-normal text-secondary-text">
                  {stockName} · {stockCode}
                </span>
                <Tag className="ml-2" color={detail.status === 'delisted' ? 'error' : 'success'}>{statusLabel(detail.status)}</Tag>
              </h2>
              <span className="font-mono text-lg font-semibold text-cyan">{formatPct(currentPremiumRate)}</span>
            </div>
            <Descriptions size="small" column={{ xs: 2, md: 3, xl: 4 }} className="mt-3">
              <Descriptions.Item label="转股价">{formatNumber(detail.convert_price)}</Descriptions.Item>
              <Descriptions.Item label="当前溢价率">{formatPct(currentPremiumRate)}</Descriptions.Item>
              <Descriptions.Item label="最新价">{formatPrice(latestClose)}</Descriptions.Item>
              <Descriptions.Item label="转股价值">{convertValue != null ? formatNumber(convertValue) : '--'}</Descriptions.Item>
              <Descriptions.Item label="剩余规模">{formatNumber(detail.remaining_size, 1)} 亿</Descriptions.Item>
              <Descriptions.Item label="剩余年限">{detailYears == null ? '--' : formatNumber(detailYears, 1)}</Descriptions.Item>
              <Descriptions.Item label="上市日期">{detail.list_date ?? '--'}</Descriptions.Item>
              <Descriptions.Item label="到期日期">{detail.maturity_date ?? '--'}</Descriptions.Item>
              <Descriptions.Item label="强赎计数">{termText(detail.terms, 'force_redeem_countdown') ?? '--'}</Descriptions.Item>
              <Descriptions.Item label="下修计数">{termText(detail.terms, 'down_revise_countdown') ?? '--'}</Descriptions.Item>
              <Descriptions.Item label="回售计数">{termText(detail.terms, 'put_countdown') ?? '--'}</Descriptions.Item>
              <Descriptions.Item label="最后交易日">{termText(detail.terms, 'last_trading_date') ?? '--'}</Descriptions.Item>
              <Descriptions.Item label="评级">{termText(detail.terms, 'bond_rating') ?? '--'}</Descriptions.Item>
              <Descriptions.Item label="行业">{industry}</Descriptions.Item>
              <Descriptions.Item label="日线条数">{detail.bar_count}</Descriptions.Item>
              <Descriptions.Item label="事件数量">{detail.event_count}</Descriptions.Item>
            </Descriptions>
          </>
        )}
      </div>

      <div className="glass-panel px-4 py-4">
        <h3 className="mb-3 text-sm font-semibold text-foreground">价格与溢价率</h3>
        {detailLoading ? <Skeleton active paragraph={{ rows: 5 }} /> : <EChart option={chartOption} height={320} aria-label="价格与溢价率图表" />}
      </div>

      <div className="glass-panel px-4 py-4">
        <h3 className="mb-3 text-sm font-semibold text-foreground">
          正股与溢价率
          {stockName !== '--' ? <span className="ml-2 text-xs font-normal text-secondary-text">{stockName} · {stockCode}</span> : null}
        </h3>
        {stockLoading ? (
          <Skeleton active paragraph={{ rows: 5 }} />
        ) : stockBars.length ? (
          <EChart option={stockChartOption} height={320} aria-label="正股与溢价率图表" />
        ) : (
          <Empty description="正股行情暂不可用（实时拉取腾讯日 K 失败或无正股代码）" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
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
  );

  return (
    <div className="space-y-4">
      {error ? <ApiErrorAlert error={error} /> : null}

      <div className="glass-panel px-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Input.Search
            aria-label="搜索标的"
            placeholder="代码 / 名称 / 正股代码"
            allowClear
            onChange={(event) => handleSearch(event.target.value)}
            className="h-10 max-w-xs"
          />
          <div className="flex items-center gap-3">
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
            <span className="text-xs text-secondary-text">共 {total} 只标的</span>
          </div>
        </div>
        <Table
          className="mt-3"
          rowKey="bond_code"
          size="small"
          columns={columns}
          dataSource={instruments}
          loading={loading}
          scroll={{ x: 1180 }}
          onChange={handleTableChange}
          onRow={(item) => ({ onClick: () => toggleRow(item), style: { cursor: 'pointer' } })}
          rowClassName={(item) => (expandedKeys.includes(item.bond_code) ? 'bg-cyan/10' : '')}
          expandable={{
            expandedRowKeys: expandedKeys,
            showExpandColumn: false,
            // rc-table 折叠后保留展开行缓存，按当前展开态条件渲染，
            // 保证收起即卸载详情（含 echarts 实例）
            expandedRowRender: (record) =>
              expandedKeys.includes(record.bond_code)
                ? <div className="space-y-4 py-2">{detailPanels}</div>
                : null,
          }}
          locale={{ emptyText: <Empty description="暂无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total,
            showSizeChanger: false,
            showTotal: (count) => `共 ${count} 只`,
          }}
        />
      </div>
    </div>
  );
};
