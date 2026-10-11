import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PositionsTab } from '../PositionsTab';

// mock 底层 axios 实例，让 toCamelCase 键名转换真实参与测试
const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock('../../../api/index', () => ({ default: client }));

// 后端原始载荷：snake_case（/api/v1/trading/positions/cb）
const backendPayload = {
  account: '135129739',
  as_of_trade_date: '2026-10-08',
  positions_updated_at: '2026-10-08 15:05:00',
  excluded_count: 1,
  summary: { count: 2, total_volume: 100, total_market_value: 11323.34, total_pnl: -106.66, total_pnl_pct: -0.93 },
  items: [
    {
      symbol: '113042', bond_name: '上银转债', stock_name: '上海银行', volume: 60, can_use_volume: 60,
      open_price: 110.5, latest_close: 117.891, latest_premium_rate: 0.24, market_value: 7073.46,
      weight_pct: 62.5, pnl: 443.46, pnl_pct: 6.69, remaining_years: 2.64, maturity_date: '2029-06-01',
      last_trading_date: null, remaining_trading_days: null,
      force_redeem_countdown: '10/15 | 30 2026-08-10 重新计',
      down_revise_countdown: '至少还需15天 ( 30天内 0/15 2025-04-16 重新计)',
      put_countdown: null, bond_rating: 'AAA', remaining_size: 192.0,
      status: 'active', updated_at: '2026-10-08 15:05:00',
    },
    {
      symbol: '118027', bond_name: '宏图转债', stock_name: '*ST航图', volume: 40, can_use_volume: 40,
      open_price: 120.0, latest_close: 106.247, latest_premium_rate: 0.62, market_value: 4249.88,
      weight_pct: 37.5, pnl: -550.12, pnl_pct: -11.46, remaining_years: 0.4, maturity_date: '2027-03-01',
      last_trading_date: '2026-10-15', remaining_trading_days: 5, force_redeem_countdown: '已公告强赎',
      down_revise_countdown: null, put_countdown: null, bond_rating: null, remaining_size: 2.5,
      status: 'active', updated_at: '2026-10-08 15:05:00',
    },
  ],
};

describe('PositionsTab 实盘可转债持仓', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.get.mockResolvedValue({ data: backendPayload });
  });

  it('请求可转债持仓聚合接口（不传账户，由后端回落实盘配置账户）', async () => {
    render(<PositionsTab />);
    await screen.findByText(/上银转债（113042）/);
    expect(client.get).toHaveBeenCalledWith('/api/v1/trading/positions/cb', expect.objectContaining({ params: {} }));
    expect(client.get).not.toHaveBeenCalledWith('/api/v1/trading/positions', expect.anything());
  });

  it('展示 3 位小数价格、正股名称与汇总条', async () => {
    render(<PositionsTab />);
    await screen.findByText(/上银转债（113042）/);
    expect(screen.getByText('117.891')).toBeInTheDocument(); // 现价 3 位小数
    expect(screen.getAllByText('110.500').length).toBeGreaterThan(0); // 成本价 3 位小数
    expect(screen.getByText('正股 上海银行')).toBeInTheDocument();
    expect(screen.getByText('135129739')).toBeInTheDocument();
    expect(screen.getByText('2 只')).toBeInTheDocument(); // 汇总只数（标的数）
    expect(screen.queryByText(/张$/)).toBeNull(); // 概览不再展示张数合计
    expect(screen.getByText(/-106.66（-0.93%）/)).toBeInTheDocument(); // 汇总浮动盈亏
    expect(screen.getByText(/行情 2026-10-08 · 持仓 2026-10-08 15:05:00/)).toBeInTheDocument();
  });

  it('关键日期突显：已公告强赎红标、最后交易日倒计、倒计文本压缩、非转债未展示提示', async () => {
    render(<PositionsTab />);
    expect(await screen.findByText('已公告强赎')).toBeInTheDocument();
    expect(screen.getByText('2026-10-15（剩5日）')).toBeInTheDocument();
    expect(screen.getByText('10/15')).toBeInTheDocument(); // 「10/15 | 30 重新计」压缩（≥60% 橙）
    expect(screen.getByText('0/15')).toBeInTheDocument(); // 「至少还需15天 (30天内 0/15 …)」压缩
    expect(screen.queryByText(/至少还需15天/)).toBeNull();
    expect(screen.getByText(/另有 1 条非可转债持仓未展示/)).toBeInTheDocument();
    await waitFor(() => {
      const critical = screen.getByText('2026-10-15（剩5日）').closest('.ant-tag');
      expect(critical?.className).toContain('ant-tag-error');
      const announced = screen.getByText('已公告强赎').closest('.ant-tag');
      expect(announced?.className).toContain('ant-tag-error');
    });
  });
});
