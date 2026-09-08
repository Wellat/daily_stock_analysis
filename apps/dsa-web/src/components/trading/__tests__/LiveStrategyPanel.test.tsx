import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LiveStrategyPanel } from '../LiveStrategyPanel';

// mock 底层 axios 实例，让 liveStrategyApi 的键名转换逻辑真实参与测试
const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock('../../../api/index', () => ({ default: client }));

// 后端原始载荷：parameters 键为 snake_case，与策略元数据定义一致
const backendConfig = {
  id: 1,
  strategy_id: 'double-low',
  strategy_version: 'v1',
  qmt_account: 'testS',
  enabled: true,
  symbols: ['113001'],
  parameters: { max_positions: 3, per_position_cash: 25000 },
  rebalance_frequency_days: 7,
  event_check_enabled: true,
  data_sync_before_run: true,
  next_rebalance_date: '2024-01-10',
};

const strategiesBackend = {
  items: [
    {
      strategy_id: 'double-low',
      name: '双低轮动',
      parameters: [
        { key: 'max_positions', label: '最大持仓数', type: 'integer', default: 2 },
        { key: 'per_position_cash', label: '单债目标资金', type: 'number', default: 10000 },
      ],
    },
  ],
};

describe('LiveStrategyPanel 策略配置', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.get.mockImplementation(async (url: string) => {
      if (url.endsWith('/config')) return { data: backendConfig };
      if (url.endsWith('/strategies')) return { data: strategiesBackend };
      if (url.endsWith('/runs') || url.endsWith('/batches')) return { data: { items: [] } };
      return { data: { trade_date: '2024-01-02' } };
    });
    client.put.mockResolvedValue({ data: backendConfig });
  });

  it('参数按后端存储值回显，而不是元数据默认值（回环不丢 parameters 键）', async () => {
    render(<LiveStrategyPanel />);
    await waitFor(() => expect(screen.getByDisplayValue('3')).toBeTruthy());
    // 单债目标资金 25000 而非默认 10000；调仓频率与自选池同样回显
    expect(screen.getByDisplayValue('25000')).toBeTruthy();
    expect(screen.getByDisplayValue('7')).toBeTruthy();
    expect(screen.getByDisplayValue('113001')).toBeTruthy();
  });

  it('保存配置携带调仓频率与 snake 键参数', async () => {
    render(<LiveStrategyPanel />);
    await waitFor(() => expect(screen.getByDisplayValue('7')).toBeTruthy());

    fireEvent.change(screen.getByDisplayValue('7'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));

    await waitFor(() => expect(client.put).toHaveBeenCalled());
    const body = client.put.mock.calls[0][1] as Record<string, unknown>;
    expect(body).toEqual(expect.objectContaining({
      rebalance_frequency_days: 5,
      parameters: { max_positions: 3, per_position_cash: 25000 },
      symbols: ['113001'],
    }));
  });
});

describe('LiveStrategyPanel 运行记录与调仓批次', () => {
  const backendRun = {
    id: 7, run_uid: 'run-uid-1', trade_date: '2024-01-02', status: 'completed',
    mode: 'rebalance', strategy_id: 'double-low', strategy_version: 'v1', qmt_account: 'testS',
    decision_count: 2, order_count: 2, skip_reason: null,
    data_snapshot_at: '2024-01-02 14:35:01', completed_at: '2024-01-02 14:35:02',
    target: { '113001': { symbol: '113001', symbol_name: '低溢价', price: 100.0, quantity: 100 } },
    current: { '113002': 100 }, rebalance: [], risk: { passed: true }, error_message: null,
  };
  const backendBatch = {
    id: 3, batch_uid: 'batch-uid-1234567890', run_id: 7, qmt_account: 'testS', status: 'pending',
    summary: { count: 2 }, created_at: '2024-01-02 14:35:02', trade_date: '2024-01-02', mode: 'rebalance',
    orders: { total: 2, pending: 1, submitted: 0, filled: 1, rejected: 0, cancelled: 0 },
  };
  const backendDecisions = { items: [
    { id: 1, action: 'buy', symbol: '113001', symbol_name: '低溢价', suggested_quantity: 100,
      reason: 'lowest_premium', decision_data: { premium_rate: 5.0, rank: 1 } },
  ] };
  const backendOrders = { items: [
    { id: 11, order_uid: 'qmt_1', symbol: '113001', side: 'buy', quantity: 100, status: 'filled',
      qmt_order_id: 'QMT-9', filled_price: 100.2, filled_quantity: 100, submitted_at: null,
      completed_at: '2024-01-02 14:35:05', error_message: null, decision_id: 1 },
  ] };

  beforeEach(() => {
    vi.clearAllMocks();
    client.get.mockImplementation(async (url: string) => {
      if (url.endsWith('/config')) return { data: backendConfig };
      if (url.endsWith('/strategies')) return { data: strategiesBackend };
      if (url.endsWith('/batches')) return { data: { items: [backendBatch] } };
      if (url.includes('/decisions')) return { data: backendDecisions };
      if (url.includes('/orders')) return { data: backendOrders };
      if (url.endsWith('/runs')) return { data: { items: [backendRun] } };
      return { data: { trade_date: '2024-01-02' } };
    });
    client.put.mockResolvedValue({ data: backendConfig });
  });

  it('运行记录展示模式/状态/决策数/订单数（回归：字段不再被响应模型过滤）', async () => {
    render(<LiveStrategyPanel />);
    fireEvent.click(screen.getByRole('tab', { name: '运行记录' }));
    expect(await screen.findByText('调仓')).toBeTruthy();
    expect(screen.getByText('已完成')).toBeTruthy();
    expect(screen.getAllByText('2').length).toBe(2); // 决策数 + 订单数
    expect(screen.getByText('2024-01-02 14:35:02')).toBeTruthy();
  });

  it('点击运行行展示策略产出详情（决策/订单/目标组合/批次）', async () => {
    render(<LiveStrategyPanel />);
    fireEvent.click(screen.getByRole('tab', { name: '运行记录' }));
    await screen.findByText('调仓');

    fireEvent.click(screen.getByText('2024-01-02'));
    expect(await screen.findByText('策略决策（1）')).toBeTruthy();
    expect(screen.getAllByText('低溢价（113001）').length).toBeGreaterThanOrEqual(2); // 目标组合+决策 标的列合并为 名称（代码）
    expect(screen.getAllByText('买入').length).toBeGreaterThanOrEqual(2); // 决策动作 + 订单方向
    expect(screen.getByText('5.00%')).toBeTruthy();
    expect(screen.getByText('#1')).toBeTruthy();
    expect(screen.getByText('已成交')).toBeTruthy();
    expect(screen.getByText('100.2')).toBeTruthy();
    expect(screen.getByText(/batch-uid/)).toBeTruthy();
  });

  it('批次表展示关联运行与订单进度，点击跳转到运行详情', async () => {
    render(<LiveStrategyPanel />);
    fireEvent.click(screen.getByRole('tab', { name: '调仓批次' }));
    expect(await screen.findByText('1/2 成交')).toBeTruthy();
    expect(screen.getByText('进行中')).toBeTruthy();

    fireEvent.click(screen.getByText('1/2 成交'));
    // 跳到运行记录 tab 并展示关联 run 的详情
    expect(await screen.findByText('策略决策（1）')).toBeTruthy();
  });
});
