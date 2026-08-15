import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MarketDataPage from '../MarketDataPage';

const cbApi = vi.hoisted(() => ({
  listInstruments: vi.fn(),
  getInstrumentDetail: vi.fn(),
  listInstrumentBars: vi.fn(),
  listInstrumentEvents: vi.fn(),
  listSyncRuns: vi.fn(),
  syncData: vi.fn(),
}));

const stockApi = vi.hoisted(() => ({
  listStocks: vi.fn(),
  getStockBars: vi.fn(),
}));

vi.mock('echarts', () => ({
  init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
}));
vi.mock('../../api/strategyLab', () => ({ strategyLabApi: cbApi }));
vi.mock('../../api/stocks', () => ({ marketStocksApi: stockApi }));

describe('MarketDataPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    cbApi.listSyncRuns.mockResolvedValue({ items: [], total: 0 });
    cbApi.syncData.mockResolvedValue({ sync_run_id: 1 });
    cbApi.listInstruments.mockResolvedValue({
      market: 'cn',
      total: 1,
      page: 1,
      limit: 50,
      items: [
        {
          bond_code: '123001',
          bond_name: '测试转债',
          stock_code: '600001',
          stock_name: '测试正股',
          market: 'cn',
          status: '正常',
          current_premium_rate: 18.5,
          latest_close: 101.2,
          latest_premium_rate: 17.8,
          event_count: 1,
        },
      ],
    });
    cbApi.getInstrumentDetail.mockResolvedValue({
      bond_code: '123001',
      bond_name: '测试转债',
      stock_code: '600001',
      stock_name: '测试正股',
      market: 'cn',
      status: '正常',
      current_premium_rate: 18.5,
      convert_price: 100,
      latest_close: 101.2,
      latest_premium_rate: 17.8,
      industry: '电子-半导体-分立器件',
      remaining_size: 12.3,
      list_date: '2024-01-01',
      maturity_date: '2028-01-01',
      terms: { strategy: 'double-low' },
      bar_count: 2,
      event_count: 1,
    });
    cbApi.listInstrumentBars.mockResolvedValue({
      bond_code: '123001',
      total: 2,
      items: [
        { trade_date: '2024-01-02', close: 100.0, premium_rate: 19.0, remaining_size: 12.0, redeem_alert: false, down_revise_alert: false, put_alert: false },
        { trade_date: '2024-01-03', close: 101.2, premium_rate: 17.8, remaining_size: 12.0, redeem_alert: false, down_revise_alert: false, put_alert: false },
      ],
    });
    cbApi.listInstrumentEvents.mockResolvedValue({
      bond_code: '123001',
      total: 1,
      items: [{ event_date: '2024-01-04', event_type: 'down_revise', event_detail: '董事会提议下修', source: 'manual' }],
    });

    stockApi.listStocks.mockResolvedValue({
      total: 1,
      page: 1,
      limit: 50,
      items: [{ code: '000725', instrument_type: 'stock', latest_date: '2026-06-22', latest_close: 6.93 }],
    });
    stockApi.getStockBars.mockResolvedValue({
      code: '000725',
      total: 2,
      items: [
        { date: '2026-06-19', open: 6.8, high: 7.0, low: 6.7, close: 6.9, volume: 1000, amount: null, data_source: 'test' },
        { date: '2026-06-22', open: 6.9, high: 7.1, low: 6.8, close: 6.93, volume: 1200, amount: null, data_source: 'test' },
      ],
    });
  });

  it('renders tabs and loads active convertible bonds by default', async () => {
    render(<MarketDataPage />);
    expect(await screen.findByRole('heading', { name: '行情数据' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '可转债' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '股票' })).toBeInTheDocument();
    await waitFor(() => expect(cbApi.listInstruments).toHaveBeenCalledWith({
      market: 'cn', keyword: undefined, status: 'active', held_only: undefined, page: 1, limit: 50,
    }));
    expect(await screen.findByText('123001')).toBeInTheDocument();
  }, 15000);

  it('shows CB detail, chart, and events after selection', async () => {
    render(<MarketDataPage />);
    const instrument = await screen.findByText('123001');
    fireEvent.click(instrument);

    await waitFor(() => expect(cbApi.getInstrumentDetail).toHaveBeenCalledWith('123001'));
    expect(await screen.findByText('测试转债')).toBeInTheDocument();
    expect(screen.getByText('电子-半导体-分立器件')).toBeInTheDocument();
    expect(screen.getByText('101.20')).toBeInTheDocument();
    expect(screen.queryByText('强赎条款：')).not.toBeInTheDocument();
    expect(screen.getByLabelText('价格与溢价率图表')).toBeInTheDocument();
    expect(screen.getByText('董事会提议下修')).toBeInTheDocument();
  }, 15000);

  it('switches to the stock tab and shows stock bars', async () => {
    render(<MarketDataPage />);
    await screen.findByText('123001');
    fireEvent.click(screen.getByRole('tab', { name: '股票' }));

    await waitFor(() => expect(stockApi.listStocks).toHaveBeenCalledWith({ keyword: undefined, page: 1, limit: 50 }));
    const stockItem = await screen.findByText('000725');
    fireEvent.click(stockItem);

    await waitFor(() => expect(stockApi.getStockBars).toHaveBeenCalledWith('000725', { limit: 1000 }));
    expect(screen.getByLabelText('股票K线图表')).toBeInTheDocument();
    expect(screen.getAllByText('6.93').length).toBeGreaterThan(0);
  }, 15000);

  it('switches to the data-sync tab and submits a basic-data sync request', async () => {
    render(<MarketDataPage />);
    await screen.findByText('123001');
    cbApi.syncData.mockResolvedValue({ sync_run_id: 7, status: 'running', cb_basic_upserted: 0, cb_terms_upserted: 0, cb_factor_upserted: 0, cb_event_upserted: 0 });
    cbApi.listSyncRuns.mockResolvedValue({
      total: 1,
      items: [
        { id: 7, run_uid: 'x', sync_type: 'opencli_cb_basic', market: 'cn', status: 'completed', result: {}, created_at: '2026-08-10T00:00:00' },
      ],
    });
    fireEvent.click(screen.getByRole('tab', { name: '数据同步' }));
    fireEvent.click(screen.getByRole('button', { name: '开始同步' }));
    await waitFor(() => expect(cbApi.syncData).toHaveBeenCalledWith({ market: 'cn', source: 'opencli', sync_type: 'cb_basic', include_delisted: false, symbols: [] }));
  }, 15000);

  it('submits an OHLC sync request with the delisted flag and a date range', async () => {
    render(<MarketDataPage />);
    await screen.findByText('123001');
    cbApi.syncData.mockResolvedValue({ sync_run_id: 8, status: 'running', ohlc_bars_upserted: 0, ohlc_skipped: 0 });
    fireEvent.click(screen.getByRole('tab', { name: '数据同步' }));
    fireEvent.change(await screen.findByLabelText('同步来源'), { target: { value: 'cb_ohlc' } });
    fireEvent.click(screen.getByLabelText('包含已退市'));
    fireEvent.change(await screen.findByLabelText('起始日期'), { target: { value: '2026-01-01' } });
    fireEvent.change(await screen.findByLabelText('结束日期'), { target: { value: '2026-08-10' } });
    fireEvent.click(screen.getByRole('button', { name: '开始同步' }));
    await waitFor(() => expect(cbApi.syncData).toHaveBeenCalledWith({
      market: 'cn',
      source: 'opencli',
      sync_type: 'cb_ohlc',
      include_delisted: true,
      start_date: '2026-01-01',
      end_date: '2026-08-10',
      symbols: [],
    }));
  }, 15000);
});
