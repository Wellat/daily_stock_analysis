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
