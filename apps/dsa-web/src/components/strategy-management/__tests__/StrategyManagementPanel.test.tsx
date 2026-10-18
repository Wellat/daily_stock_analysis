import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { StrategyManagementPanel } from '../StrategyManagementPanel';

// mock 底层 axios 实例，让 toCamelCase 键名转换真实参与测试
const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('../../../api/index', () => ({ default: client }));

const strategiesPayload = {
  items: [
    { strategy_id: 'low-premium', name: '低溢价轮动', description: '按转股溢价率升序轮动。', instrument_types: ['convertible_bond'], markets: ['cn'], live_mode: 'order',
      parameters: [{ key: 'max_positions', label: '最大持仓', type: 'integer', default: 10 }] },
    { strategy_id: 'double-low', name: '双低轮动', description: '按双低值升序轮动。', instrument_types: ['convertible_bond'], markets: ['cn'], live_mode: 'order', parameters: [] },
    { strategy_id: 'signal', name: 'ETF 信号', description: '时序进出场规则。', instrument_types: ['etf'], markets: ['cn'], live_mode: 'notify',
      parameters: [
        { key: 'ma_window', label: '均线窗口(日)', type: 'integer', default: 182 },
        { key: 'entry_gap_max', label: '买入乖离率上限(%)', type: 'number', default: 0 },
      ] },
    { strategy_id: 'rotation', name: '转债轮动', description: '横截面因子轮动。', instrument_types: ['convertible_bond'], markets: ['cn'], live_mode: null, parameters: [] },
  ],
};

// 一条启用的 order 实例、一条未启用的 notify 实例、一条仅回测实例
const configsPayload = {
  items: [
    { id: 12, name: '轮动实验', description: null, strategy_id: 'rotation', strategy_version: 'v1', qmt_account: '-', enabled: false,
      symbols: ['113050', '113062', '118000', '123001'], parameters: { rebalance_interval: 5 }, rebalance_frequency_days: 1, schedule_time: '14:35' },
    { id: 9, name: 'ETF信号通知', description: '红利+纳指', strategy_id: 'signal', strategy_version: 'v1', qmt_account: '-', enabled: false, symbols: ['510880'],
      parameters: { ma_window: 20, entry_rules: [{ factor: 'ma_gap_pct', op: '<', value: 0 }], exit_rules: [{ factor: 'ma_gap_pct', op: '>', value: 7 }] },
      rebalance_frequency_days: 1, schedule_time: '14:25', last_run: { id: 77, trade_date: '2026-10-16', status: 'completed', mode: 'notify' } },
    { id: 1, name: '低溢价主仓', description: null, strategy_id: 'low-premium', strategy_version: 'v1', qmt_account: '135129739',
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

describe('StrategyManagementPanel 策略管理（实例为主）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.get.mockImplementation(async (url: string) => {
      if (url === '/api/v1/live-strategy/strategies') return { data: strategiesPayload };
      if (url === '/api/v1/live-strategy/configs') return { data: configsPayload };
      throw new Error(`unexpected GET ${url}`);
    });
    client.put.mockResolvedValue({ data: configsPayload.items[1] });
    client.post.mockImplementation(async (url: string) => {
      if (url.endsWith('/run')) {
        return { data: { run_id: 9, trade_date: '2026-10-17', status: 'completed', feishu_sent: true, message: 'ETF 信号通知 · 2026-10-17\n- 红利ETF（510880）：→ 买入信号' } };
      }
      return { data: configsPayload.items[1] };
    });
    client.delete.mockResolvedValue({ data: { deleted: true, id: 1 } });
  });

  it('实例表：模板/品种/能力/状态/调度/最近运行 + 实例口径汇总卡与模板折叠区', async () => {
    renderPanel();
    // 实例行（实例名 + 模板中文名）
    expect(await screen.findByText('低溢价主仓')).toBeInTheDocument();
    expect(screen.getByText('ETF信号通知')).toBeInTheDocument();
    expect(screen.getByText('轮动实验')).toBeInTheDocument();
    expect(screen.getAllByText('低溢价轮动').length).toBeGreaterThanOrEqual(1);
    // 品种标识 + 能力三态（实例表内）
    expect(screen.getAllByText('可转债').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('ETF')).toBeInTheDocument();
    expect(screen.getAllByText('实盘交易').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('信号通知').length).toBeGreaterThanOrEqual(1);
    // 运行状态与调度
    expect(screen.getAllByText('实盘运行中').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('14:35 · 交易日')).toBeInTheDocument();
    expect(screen.getByText('14:25 · 交易日')).toBeInTheDocument();
    // 最近运行（notify 实例带 last_run）
    expect(screen.getByText('2026-10-16')).toBeInTheDocument();
    // 汇总卡：实例口径 + 模板数（汇总卡标签与实例状态 Tag 同文案，用 getAll）
    expect(screen.getByText('策略实例')).toBeInTheDocument();
    expect(screen.getAllByText('实盘运行中').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('策略模板')).toBeInTheDocument();
    // 仅回测实例提供「去回测」入口（带实例血缘参数）
    expect(screen.getByRole('link', { name: '去回测' })).toHaveAttribute('href', '/strategy-lab?instance=12');
    // 展开模板总览折叠区：模板行与能力三态齐全
    fireEvent.click(screen.getByText(/策略模板总览/));
    expect(await screen.findByText('双低轮动')).toBeInTheDocument();
    expect(screen.getAllByText('实盘交易').length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText('仅回测').length).toBeGreaterThanOrEqual(1);
  }, 15000);

  it('新建策略：抽屉默认模板回填参数，创建提交 POST /configs（snake_case）', async () => {
    renderPanel();
    await screen.findByText('低溢价主仓'); // 等数据就绪（未就绪时按钮 disabled）
    fireEvent.click(screen.getByRole('button', { name: '新建策略' }));
    // 抽屉表单：实例名 + 模板默认参数（low-premium 为首个 order 模板）
    const nameInput = await screen.findByRole('textbox', { name: '实例名' });
    fireEvent.change(nameInput, { target: { value: '低溢价小仓' } });
    fireEvent.change(screen.getByRole('spinbutton', { name: '实例参数-max_positions' }), { target: { value: '5' } });
    // aria-label「保存策略实例」统一标注创建/更新两个动作，按可见文案断言角色名需用 aria-label
    fireEvent.click(screen.getByRole('button', { name: '保存策略实例' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/api/v1/live-strategy/configs', expect.objectContaining({
      name: '低溢价小仓', strategy_id: 'low-premium', qmt_account: 'testS', enabled: false,
      parameters: expect.objectContaining({ max_positions: 5 }),
    })));
  }, 15000);

  it('编辑 notify 实例：抽屉回填名称/标的/参数/时刻，保存提交 PUT /configs/{id}，显式规则原样保留', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: '编辑实例-ETF信号通知' }));
    expect(await screen.findByRole('textbox', { name: '实例名' })).toHaveValue('ETF信号通知');
    expect(screen.getByRole('textbox', { name: '实例标的' })).toHaveValue('510880');
    expect(screen.getByRole('textbox', { name: '自动运行时刻' })).toHaveValue('14:25');
    expect(screen.getByRole('spinbutton', { name: '实例参数-ma_window' })).toHaveValue(20);
    // 编辑态模板不可换
    expect(screen.getByRole('combobox', { name: '策略模板' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '保存策略实例' }));
    await waitFor(() => expect(client.put).toHaveBeenCalledWith('/api/v1/live-strategy/configs/9', expect.objectContaining({
      name: 'ETF信号通知', strategy_id: 'signal', enabled: false, symbols: ['510880'],
      parameters: expect.objectContaining({
        ma_window: 20, entry_gap_max: 0,
        // 未占参数面板的结构化键不能在编辑保存时被丢掉
        entry_rules: [{ factor: 'ma_gap_pct', op: '<', value: 0 }],
        exit_rules: [{ factor: 'ma_gap_pct', op: '>', value: 7 }],
      }), schedule_time: '14:25',
    })));
  }, 15000);

  it('notify 实例立即运行：POST /configs/{id}/run 并展示运行结果', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: '立即运行-ETF信号通知' }));
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/api/v1/live-strategy/configs/9/run'));
    expect(await screen.findByText(/买入信号/)).toBeInTheDocument();
    expect(screen.getByText('飞书已发送')).toBeInTheDocument();
  }, 15000);
});
