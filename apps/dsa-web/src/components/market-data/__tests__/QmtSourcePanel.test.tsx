import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QmtSourcePanel } from '../QmtSourcePanel';

const { getStatus, getSubscription, updateSettings, updateManualSymbols, syncSubscription, getQuoteData, getDailyData } = vi.hoisted(() => ({
  getStatus: vi.fn(),
  getSubscription: vi.fn(),
  updateSettings: vi.fn(),
  updateManualSymbols: vi.fn(),
  syncSubscription: vi.fn(),
  getQuoteData: vi.fn(),
  getDailyData: vi.fn(),
}));

vi.mock('../../../api/qmtQuotes', () => ({
  qmtQuotesApi: { getStatus, getSubscription, updateSettings, updateManualSymbols, syncSubscription, getQuoteData, getDailyData },
}));

describe('QmtSourcePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getStatus.mockResolvedValue({
      enabled: true,
      redisUrl: 'redis://localhost:6379/0',
      redisAvailable: true,
      alive: true,
      heartbeat: '2026-11-01 22:43:21|cb_quotes_bridge',
      subscribedCount: 3,
      autoIncludeHoldings: true,
      includeAlertSymbols: false,
      manualSymbols: ['113050.SH'],
      holdingsCount: 2,
      alertCount: 1,
    });
    getSubscription.mockResolvedValue({
      symbols: [
        { symbol: '600519.SH', name: '贵州茅台', origins: ['holdings', 'alert'] },
        { symbol: '113050.SH', name: '皓元转债', origins: ['manual'] },
        { symbol: '588000.SH', name: '', origins: ['stale'] },
      ],
      holdings: ['600519.SH'],
      alertSymbols: ['600519.SH'],
      manualSymbols: ['113050.SH'],
    });
    updateSettings.mockResolvedValue(getStatus());
    updateManualSymbols.mockResolvedValue({ symbols: ['113050.SH'], invalid: [] });
    syncSubscription.mockResolvedValue({ changed: true, added: ['113050.SH'], removed: [], total: 3 });
    getQuoteData.mockResolvedValue({
      symbol: '600519.SH',
      alive: true,
      found: true,
      data: {
        code: '600519.SH',
        last: 1392.05,
        lastClose: 1354.0,
        pvolume: 5992729,
        timetag: '20261030 15:00:01',
        written_at: '2026-10-30 15:00:21',
      },
    });
    getDailyData.mockResolvedValue({
      symbol: '600519.SH',
      alive: true,
      found: true,
      data: {
        code: '600519.SH',
        dividend_type: 'none',
        updated_at: '2026-10-30 15:05:02',
        bars: [
          { date: '20261029', open: 1349.99, high: 1356.0, low: 1330.01, close: 1354.0, volume: 42992, amount: 5797980000.0 },
          { date: '20261030', open: 1363.0, high: 1393.37, low: 1352.98, close: 1392.05, volume: 59927, amount: 8257381800.0 },
        ],
      },
    });
  });

  it('renders heartbeat status, counts and origin tags', async () => {
    render(<QmtSourcePanel />);

    expect(await screen.findByText('本地行情源（QMT 行情桥）')).toBeInTheDocument();
    expect(screen.getByText('心跳正常')).toBeInTheDocument();
    expect(screen.getByText('redis://localhost:6379/0')).toBeInTheDocument();
    expect(screen.getByText(/订阅 3/)).toBeInTheDocument();
    // 订阅表：名称 + 代码 + 来源标签（无名称的标的显示 --）
    expect(await screen.findByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('600519.SH')).toBeInTheDocument();
    expect(screen.getByText('皓元转债')).toBeInTheDocument();
    expect(screen.getByText('--')).toBeInTheDocument();
    expect(screen.getAllByText('持仓').length).toBeGreaterThan(0);
    expect(screen.getByText('告警')).toBeInTheDocument();
    expect(screen.getByText('手动')).toBeInTheDocument();
    expect(screen.getByText('残留')).toBeInTheDocument();
  });

  it('toggles call settings api with boolean payload', async () => {
    render(<QmtSourcePanel />);

    const toggle = await screen.findByRole('switch', { name: '自动纳入告警标的' });
    // 初始状态：includeAlertSymbols=false → 未勾选
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);

    await waitFor(() =>
      expect(updateSettings).toHaveBeenCalledWith({ includeAlertSymbols: true }),
    );
  });

  it('manual sync button shows the diff summary', async () => {
    render(<QmtSourcePanel />);

    fireEvent.click(await screen.findByText('立即同步订阅'));
    await waitFor(() => expect(syncSubscription).toHaveBeenCalled());
    expect(await screen.findByText(/新增 1 个、移除 0 个，共 3 个标的/)).toBeInTheDocument();
  });

  it('saves manual symbols via the manual api', async () => {
    render(<QmtSourcePanel />);

    fireEvent.click(await screen.findByText('保存手动订阅'));
    await waitFor(() => expect(updateManualSymbols).toHaveBeenCalledWith(['113050.SH']));
  });

  it('opens the raw quote viewer from the actions column', async () => {
    render(<QmtSourcePanel />);

    fireEvent.click(await screen.findByLabelText('查看实时数据 600519.SH'));
    expect(await screen.findByText('实时快照 · 600519.SH')).toBeInTheDocument();
    expect(screen.getByText('最新价（元）')).toBeInTheDocument();
    expect(screen.getByText('1392.05')).toBeInTheDocument();
    expect(screen.getByText('快照时刻')).toBeInTheDocument();
    expect(screen.getByText('20261030 15:00:01')).toBeInTheDocument();
  });

  it('opens the raw daily bars viewer from the actions column', async () => {
    render(<QmtSourcePanel />);

    fireEvent.click(await screen.findByLabelText('查看K线数据 600519.SH'));
    expect(await screen.findByText('日K数据 · 600519.SH')).toBeInTheDocument();
    // 元信息行 + bars 表格（原始 YYYYMMDD 日期；antd 表头有隐藏测量行会重复，用 getAllByText）
    expect(await screen.findByText('20261030')).toBeInTheDocument();
    expect(screen.getAllByText('成交量（手）').length).toBeGreaterThan(0);
  });

  it('shows a hint when the bridge is offline in the viewer', async () => {
    getQuoteData.mockResolvedValue({ symbol: '600519.SH', alive: false, found: false, data: null });
    render(<QmtSourcePanel />);

    fireEvent.click(await screen.findByLabelText('查看实时数据 600519.SH'));
    expect(await screen.findByText(/心跳离线/)).toBeInTheDocument();
  });
});
