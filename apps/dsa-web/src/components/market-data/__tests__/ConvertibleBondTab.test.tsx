import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConvertibleBondTab } from '../ConvertibleBondTab';

const cbApi = vi.hoisted(() => ({
  listInstruments: vi.fn(),
  getInstrumentDetail: vi.fn(),
  listInstrumentBars: vi.fn(),
  listInstrumentStockBars: vi.fn(),
  listInstrumentEvents: vi.fn(),
}));

const pfApi = vi.hoisted(() => ({ listTrades: vi.fn() }));

// mock echarts 本体（仓库既有模式），从 setOption 捕获图表 option 断言买卖点序列
type ChartOption = { series?: Array<{ name?: string; data?: unknown[] }>; dataZoom?: Array<{ type?: string }> };
const chartOptions = vi.hoisted(() => [] as ChartOption[]);

vi.mock('echarts', () => ({
  connect: vi.fn(),
  init: vi.fn(() => ({
    setOption: vi.fn((option: ChartOption) => chartOptions.push(option)),
    resize: vi.fn(),
    dispose: vi.fn(),
  })),
}));
vi.mock('../../../api/strategyLab', () => ({ strategyLabApi: cbApi }));
vi.mock('../../../api/portfolio', () => ({ portfolioApi: pfApi }));

const bondItem = {
  bond_code: '113042',
  bond_name: '上银转债',
  stock_code: '601229',
  stock_name: '上海银行',
  market: 'cn',
  status: 'active',
  latest_close: 115.9,
  latest_premium_rate: 17.9,
};

const bars = [
  { trade_date: '2026-09-12', close: 118.1, premium_rate: 20.1 },
  { trade_date: '2026-09-18', close: 116.2, premium_rate: 18.2 },
  { trade_date: '2026-09-24', close: 115.9, premium_rate: 17.9 },
];

const trades = {
  total: 2,
  page: 1,
  pageSize: 100,
  items: [
    { id: 1, accountId: 1, symbol: '113042', market: 'cn', currency: 'CNY', tradeDate: '2026-09-12', side: 'buy', quantity: 20, price: 118.1, fee: 0.5, tax: 0 },
    { id: 2, accountId: 1, symbol: '113042', market: 'cn', currency: 'CNY', tradeDate: '2026-09-24', side: 'sell', quantity: 20, price: 115.9, fee: 0.5, tax: 0 },
  ],
};

function setupApis() {
  cbApi.listInstruments.mockResolvedValue({ market: 'cn', total: 1, page: 1, limit: 50, items: [bondItem] });
  cbApi.getInstrumentDetail.mockResolvedValue({ ...bondItem, terms: {}, bar_count: 3, event_count: 0 });
  cbApi.listInstrumentBars.mockResolvedValue({ bond_code: '113042', total: 3, items: bars });
  cbApi.listInstrumentStockBars.mockResolvedValue({ bond_code: '113042', total: 0, items: [] });
  cbApi.listInstrumentEvents.mockResolvedValue({ bond_code: '113042', total: 0, items: [] });
  pfApi.listTrades.mockResolvedValue(trades);
}

describe('ConvertibleBondTab URL 直达与买卖点标记', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    chartOptions.length = 0;
    setupApis();
  });

  it('?symbol= 自动定位标的、展开详情，并在溢价率曲线上标记买卖点', async () => {
    render(
      <MemoryRouter initialEntries={['/market-data?tab=convertible-bond&symbol=113042']}>
        <ConvertibleBondTab />
      </MemoryRouter>,
    );

    // 搜索关键词预填为 symbol，列表按关键词加载
    await waitFor(() => expect(cbApi.listInstruments).toHaveBeenCalled());
    expect(cbApi.listInstruments.mock.calls[0][0].keyword).toBe('113042');

    // 自动展开详情：拉取详情/K线/成交
    await waitFor(() => expect(cbApi.getInstrumentDetail).toHaveBeenCalledWith('113042'));
    await waitFor(() => expect(pfApi.listTrades).toHaveBeenCalledWith({ symbol: '113042', page: 1, pageSize: 100 }));
    expect(await screen.findByText('上银转债')).toBeTruthy();

    // 价格与溢价率图：买卖点散点序列吸附到对应交易日的溢价率值
    const findPriceChart = () => chartOptions.find((o) => o.series?.some((s) => s.name === '收盘价'));
    await waitFor(() => expect(findPriceChart()?.series?.some((s) => s.name === '买入点')).toBe(true));
    const buySeries = findPriceChart()?.series?.find((s) => s.name === '买入点');
    const sellSeries = findPriceChart()?.series?.find((s) => s.name === '卖出点');
    expect(buySeries?.data).toEqual([{ value: [0, 20.1], name: '20 张 @ 118.100' }]);
    expect(sellSeries?.data).toEqual([{ value: [2, 17.9], name: '20 张 @ 115.900' }]);
    // 时间窗缩放：滑窗 + 滚轮两种 dataZoom
    const zoomTypes = findPriceChart()?.dataZoom?.map((z) => (z as { type?: string }).type);
    expect(zoomTypes).toContain('slider');
    expect(zoomTypes).toContain('inside');
  }, 15000);

  it('未退市筛不到目标时放宽为全部再定位（含退市标的）', async () => {
    cbApi.listInstruments.mockImplementation(async (query: { status?: string }) => {
      // 第一次（status=active）不含目标；放宽为全部（status=undefined）后命中
      if (query?.status === 'active') return { market: 'cn', total: 0, page: 1, limit: 50, items: [] };
      return { market: 'cn', total: 1, page: 1, limit: 50, items: [{ ...bondItem, status: 'delisted' }] };
    });

    render(
      <MemoryRouter initialEntries={['/market-data?tab=convertible-bond&symbol=113042']}>
        <ConvertibleBondTab />
      </MemoryRouter>,
    );

    await waitFor(() => expect(cbApi.getInstrumentDetail).toHaveBeenCalledWith('113042'), { timeout: 8000 });
  }, 15000);

  it('无成交记录时不追加买卖点序列', async () => {
    pfApi.listTrades.mockResolvedValue({ total: 0, page: 1, pageSize: 100, items: [] });

    render(
      <MemoryRouter initialEntries={['/market-data?tab=convertible-bond&symbol=113042']}>
        <ConvertibleBondTab />
      </MemoryRouter>,
    );

    await waitFor(() => expect(cbApi.getInstrumentDetail).toHaveBeenCalledWith('113042'));
    await waitFor(() => expect(chartOptions.some((o) => o.series?.some((s) => s.name === '收盘价'))).toBe(true));
    expect(chartOptions.some((o) => o.series?.some((s) => s.name === '买入点'))).toBe(false);
  }, 15000);
});
