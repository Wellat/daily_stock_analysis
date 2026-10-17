import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { StrategyManagementPanel } from '../StrategyManagementPanel';

// mock 底层 axios 实例，让 toCamelCase 键名转换真实参与测试
const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock('../../../api/index', () => ({ default: client }));

const strategiesPayload = {
  items: [
    { strategy_id: 'low-premium', name: '低溢价轮动', description: '按转股溢价率升序轮动。', instrument_types: ['convertible_bond'], markets: ['cn'], live_mode: 'order', parameters: [] },
    { strategy_id: 'double-low', name: '双低轮动', description: '按双低值升序轮动。', instrument_types: ['convertible_bond'], markets: ['cn'], live_mode: 'order', parameters: [] },
    { strategy_id: 'signal', name: 'ETF 信号', description: '时序进出场规则。', instrument_types: ['etf'], markets: ['cn'], live_mode: 'notify',
      parameters: [
        { key: 'ma_window', label: '均线窗口(日)', type: 'integer', default: 182 },
        { key: 'entry_gap_max', label: '买入乖离率上限(%)', type: 'number', default: 0 },
      ] },
    { strategy_id: 'rotation', name: '转债轮动', description: '横截面因子轮动。', instrument_types: ['convertible_bond'], markets: ['cn'], live_mode: null, parameters: [] },
  ],
};

// order 交易配置 + 一条未启用的 notify 配置
const configsPayload = {
  items: [
    { id: 9, strategy_id: 'signal', strategy_version: 'v1', qmt_account: '-', enabled: false, symbols: ['510880'],
      parameters: { ma_window: 20 }, rebalance_frequency_days: 1, schedule_time: '14:25' },
    { id: 1, strategy_id: 'low-premium', strategy_version: 'v1', qmt_account: '135129739',
      enabled: true, symbols: [], parameters: { max_positions: 10 },
      rebalance_frequency_days: 10, event_check_enabled: true, data_sync_before_run: true,
      last_trading_day_exit_enabled: true, last_trading_day_exit_buffer_days: 1,
      schedule_time: '14:35', next_rebalance_date: '2026-10-22' },
  ],
};

const renderPanel = () => render(
  <MemoryRouter>
    <StrategyManagementPanel />
  </MemoryRouter>,
);

describe('StrategyManagementPanel 策略管理', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.get.mockImplementation(async (url: string) => {
      if (url === '/api/v1/live-strategy/strategies') return { data: strategiesPayload };
      if (url === '/api/v1/live-strategy/configs') return { data: configsPayload };
      throw new Error(`unexpected GET ${url}`);
    });
    client.put.mockResolvedValue({ data: configsPayload.items[0] });
    client.post.mockResolvedValue({ data: { run_id: 9, trade_date: '2026-10-17', status: 'completed', feishu_sent: true, message: 'ETF 信号通知 · 2026-10-17\n- 红利ETF（510880）：→ 买入信号' } });
  });

  it('展示中文名、品种与实盘能力标识、order/notify 实盘状态与调度', async () => {
    renderPanel();
    expect(await screen.findByText('低溢价轮动')).toBeInTheDocument();
    expect(screen.getByText('ETF 信号')).toBeInTheDocument();
    expect(screen.getByText('转债轮动')).toBeInTheDocument();
    // 品种标识
    expect(screen.getAllByText('可转债').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('ETF')).toBeInTheDocument();
    // 实盘能力三态（order 两行、「仅回测」同时出现在汇总卡与表格 Tag）
    expect(screen.getAllByText('实盘交易').length).toBe(2);
    expect(screen.getAllByText('信号通知').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('仅回测').length).toBeGreaterThanOrEqual(2);
    // order 运行中、notify 已配置未启用
    expect(screen.getByText('实盘运行中')).toBeInTheDocument();
    expect(screen.getByText('通知已暂停')).toBeInTheDocument();
    expect(screen.getByText('14:35 · 交易日')).toBeInTheDocument();
    expect(screen.getByText('14:25 · 交易日')).toBeInTheDocument();
    expect(screen.getByText(/低溢价轮动 · 14:35 · 运行中/)).toBeInTheDocument();
    // 汇总：策略总数 4、信号通知 0/1
    expect(screen.getByText('4')).toBeInTheDocument();
    expect(screen.getByText('0/1')).toBeInTheDocument();
  });

  it('关闭自动实盘开关：提交完整配置 PUT（snake_case 含 schedule_time）', async () => {
    renderPanel();
    await screen.findByText('低溢价轮动');
    fireEvent.click(screen.getByRole('switch', { name: '自动实盘开关' }));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith('/api/v1/live-strategy/config', expect.objectContaining({
      strategy_id: 'low-premium', qmt_account: '135129739', enabled: false, schedule_time: '14:35',
    })));
  });

  it('修改自动运行时刻后保存，非法时刻禁用保存', async () => {
    renderPanel();
    const input = await screen.findByRole('textbox', { name: '自动运行时刻' });
    // 未变化时保存禁用
    expect(screen.getByRole('button', { name: '保存时刻' })).toBeDisabled();
    fireEvent.change(input, { target: { value: '25:99' } });
    expect(screen.getByRole('button', { name: '保存时刻' })).toBeDisabled();
    fireEvent.change(input, { target: { value: '14:25' } });
    fireEvent.click(screen.getByRole('button', { name: '保存时刻' }));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith('/api/v1/live-strategy/config', expect.objectContaining({
      schedule_time: '14:25', enabled: true,
    })));
  });

  it('非实盘策略的操作：order 跳实盘页、仅回测无操作', async () => {
    renderPanel();
    await screen.findByText('低溢价轮动');
    expect(screen.getByText('去实盘页配置')).toBeInTheDocument();
  });

  it('notify 策略：打开信号通知抽屉，保存 PUT /configs/notify，立即运行展示结果', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: '信号通知设置-signal' }));
    // 抽屉：开关/时刻/标的/参数从已有配置回填
    expect(await screen.findByRole('switch', { name: '自动通知开关' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '通知运行时刻' })).toHaveValue('14:25');
    expect(screen.getByRole('textbox', { name: '监测标的' })).toHaveValue('510880');
    expect(screen.getByRole('spinbutton', { name: '通知参数-ma_window' })).toHaveValue(20);
    // 保存（含参数回填）→ PUT /configs/notify snake_case
    fireEvent.click(screen.getByRole('button', { name: '保存信号通知配置' }));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith('/api/v1/live-strategy/configs/notify', expect.objectContaining({
      strategy_id: 'signal', enabled: false, symbols: ['510880'],
      parameters: expect.objectContaining({ ma_window: 20, entry_gap_max: 0 }), schedule_time: '14:25',
    })));
    // 立即运行 → POST 展示信号消息
    fireEvent.click(screen.getByRole('button', { name: '立即运行信号通知' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/api/v1/live-strategy/configs/9/notify/run'));
    expect(await screen.findByText(/买入信号/)).toBeInTheDocument();
    expect(screen.getByText('飞书已发送')).toBeInTheDocument();
  });
});
