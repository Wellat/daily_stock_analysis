import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StrategyDashboardTab } from '../StrategyDashboardTab';

// mock 底层 axios 实例，让 tradingApi 的 camelCase 键名转换真实参与测试
const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock('../../../api/index', () => ({ default: client }));

// EChart 依赖 echarts.init，mock 掉渲染副作用
vi.mock('echarts', () => ({ init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })) }));

const dashboardPayload = {
  start: null,
  end: null,
  summary: {
    total_count: 3,
    buy_count: 2,
    sell_count: 1,
    buy_amount: 2100.0,
    sell_amount: 1800.0,
    realized_pnl: 250.0,
    win_count: 1,
    loss_count: 1,
    win_rate: 0.5,
    unmatched_sell_quantity: 20.0,
  },
  curve: [
    { date: '2026-01-06', daily_pnl: -25.0, cumulative_pnl: -25.0 },
    { date: '2026-01-08', daily_pnl: 275.0, cumulative_pnl: 250.0 },
  ],
  symbols: [
    {
      symbol: '113001',
      symbol_name: '样例转债',
      buy_count: 2,
      buy_quantity: 20,
      buy_amount: 2100.0,
      sell_count: 1,
      sell_quantity: 15,
      sell_amount: 1800.0,
      realized_pnl: 250.0,
      open_quantity: 5,
      open_cost: 550.0,
      unmatched_sell_quantity: 20.0,
    },
  ],
};

describe('StrategyDashboardTab 策略看板', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.get.mockImplementation(async (url: string) => {
      if (url === '/api/v1/trading/dashboard') return { data: dashboardPayload };
      return { data: { items: [] } };
    });
  });

  it('渲染 KPI、无配对提示与分标的明细（snake_case 载荷经键名转换）', async () => {
    render(<StrategyDashboardTab />);

    expect((await screen.findAllByText('+250.00')).length).toBeGreaterThan(0);
    expect(screen.getByText('50.0%')).toBeTruthy();
    expect(screen.getByText('样例转债（113001）')).toBeTruthy();
    expect(screen.getByText('5.00 张 · 成本 550.00')).toBeTruthy();
    expect(screen.getByText(/无买入记录的卖出/)).toBeTruthy();
    expect(screen.getByText('累计收益曲线')).toBeTruthy();
  }, 15000);

  it('快捷范围切换把日期参数透传给 dashboard 接口', async () => {
    render(<StrategyDashboardTab />);
    await screen.findAllByText('+250.00');

    fireEvent.click(screen.getByText('近30天'));

    const expectedStart = new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10);
    const expectedEnd = new Date().toISOString().slice(0, 10);
    await waitFor(() => {
      const call = client.get.mock.calls
        .map(([url, config]) => ({ url, params: (config as { params?: Record<string, string> })?.params }))
        .filter((item) => item.url === '/api/v1/trading/dashboard')
        .pop();
      expect(call?.params).toEqual({ start: expectedStart, end: expectedEnd });
    });
  }, 15000);

  it('自定义日期变更后按输入范围查询', async () => {
    render(<StrategyDashboardTab />);
    await screen.findAllByText('+250.00');

    fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2026-01-01' } });
    fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2026-01-31' } });

    await waitFor(() => {
      const call = client.get.mock.calls
        .map(([url, config]) => ({ url, params: (config as { params?: Record<string, string> })?.params }))
        .filter((item) => item.url === '/api/v1/trading/dashboard')
        .pop();
      expect(call?.params).toEqual({ start: '2026-01-01', end: '2026-01-31' });
    });
  }, 15000);
});
