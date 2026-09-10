import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import StrategyLabPage from '../StrategyLabPage';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';

const api = vi.hoisted(() => ({
  listStrategies: vi.fn(), listRuns: vi.fn(), listBatches: vi.fn(), listSignals: vi.fn(),
  createRun: vi.fn(), listRunTrades: vi.fn(), getRun: vi.fn(), createBatch: vi.fn(), getBatch: vi.fn(),
  retryBatch: vi.fn(), resumeBatch: vi.fn(), deleteBatch: vi.fn(), getBatchStreamUrl: vi.fn(), createSignal: vi.fn(), confirmSignal: vi.fn(),
}));

const liveApi = vi.hoisted(() => ({ getConfig: vi.fn() }));

vi.mock('echarts', () => ({
  init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
}));
vi.mock('../../api/strategyLab', () => ({ strategyLabApi: api }));
vi.mock('../../api/liveStrategy', () => ({ liveStrategyApi: liveApi }));
vi.mock('../../api/portfolio', () => ({ portfolioApi: { getAccounts: vi.fn().mockResolvedValue({ accounts: [] }) } }));

describe('StrategyLabPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listStrategies.mockResolvedValue([
      { strategy_id: 'double-low', name: '双低轮动', instrument_types: ['convertible_bond'], markets: ['cn'] },
      { strategy_id: 'low-premium', name: '低溢价轮动', instrument_types: ['convertible_bond'], markets: ['cn'] },
      { strategy_id: 'ma-crossover', name: '均线交叉', instrument_types: ['convertible_bond'], markets: ['cn'] },
    ]);
    api.listRuns.mockResolvedValue([]);
    api.listBatches.mockResolvedValue([]);
    api.listSignals.mockResolvedValue([]);
    api.createBatch.mockResolvedValue({ id: 1, status: 'completed', total_tasks: 2, completed_tasks: 2 });
    api.createRun.mockResolvedValue({ id: 1, strategy_name: '双低轮动', engine_name: 'unified_low_premium_v1', status: 'completed', start_date: '2024-01-02', end_date: '2024-01-31', metrics: null, equity_curve: [] });
    api.listRunTrades.mockResolvedValue([]);
    liveApi.getConfig.mockReset();
  });

  it('renders three top-level tabs and keeps backtest on the default research tab', async () => {
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    expect(await screen.findByRole('heading', { name: '策略实验室' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '策略研究' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '参数搜索' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '实盘信号' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '数据同步' })).not.toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '运行回测' })).toBeInTheDocument();
  }, 15000);

  it('submits a backtest run with the selected strategy', async () => {
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '运行回测' });
    fireEvent.change(screen.getByLabelText('策略'), { target: { value: 'ma-crossover' } });
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));
    await waitFor(() => expect(api.createRun).toHaveBeenCalledWith(expect.objectContaining({ strategy_id: 'ma-crossover', instrument_type: 'convertible_bond' })));
  }, 15000);

  it('switches to the parameter-search tab and submits an async batch', async () => {
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '运行回测' });
    fireEvent.click(screen.getByRole('tab', { name: '参数搜索' }));
    await screen.findByRole('button', { name: '运行参数批次' });
    fireEvent.click(screen.getByRole('button', { name: '运行参数批次' }));
    await waitFor(() => expect(api.createBatch).toHaveBeenCalledWith(expect.objectContaining({ strategy_id: 'double-low', run_async: true })));
  }, 15000);

  it('imports the live config into the run form and submits live parameters', async () => {
    liveApi.getConfig.mockResolvedValue({
      strategyId: 'low-premium',
      qmtAccount: 'testS',
      symbols: ['113001', '113002'],
      parameters: { max_positions: 3, per_position_cash: 8000, lot_size: 10, max_abs_premium: 200, exclude_event_blocked: true },
    });
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '运行回测' });

    fireEvent.click(screen.getByRole('button', { name: '导入实盘配置' }));
    expect(await screen.findByText('实盘配置 low-premium · testS')).toBeInTheDocument();
    expect(screen.getByLabelText('策略')).toHaveValue('low-premium');
    expect(screen.getByLabelText('最大持仓')).toHaveValue(3);
    // 初始资金按 单债目标资金 × 最大持仓 映射：8000 × 3 = 24000
    expect(screen.getByLabelText('初始资金')).toHaveValue(24000);
    expect(screen.getByLabelText('标的筛选')).toHaveValue('113001,113002');

    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));
    await waitFor(() => expect(api.createRun).toHaveBeenCalledWith(expect.objectContaining({
      strategy_id: 'low-premium',
      initial_cash: 24000,
      symbols: ['113001', '113002'],
      parameters: expect.objectContaining({ max_positions: 3, per_position_cash: 8000, lot_size: 10 }),
    })));
  }, 15000);

  it('runs a single-day backtest with start=end and flags fixture-sample results', async () => {
    api.createRun.mockResolvedValue({ id: 2, strategy_name: '双低轮动', engine_name: 'fixture_double_low_v1', status: 'completed', start_date: '2024-01-05', end_date: '2024-01-05', metrics: null, equity_curve: [] });
    api.listRunTrades.mockResolvedValue([
      { id: 1, run_id: 2, trade_date: '2024-01-05', symbol: '113001', symbol_name: '低溢价', side: 'buy', quantity: 100, price: 100, amount: 10000, fee: 0 },
    ]);
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '运行回测' });

    fireEvent.click(screen.getByText('单日运行'));
    expect(screen.queryByLabelText('结束日期')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('日期'), { target: { value: '2024-01-05' } });
    fireEvent.click(screen.getByRole('button', { name: '运行单日回测' }));

    await waitFor(() => expect(api.createRun).toHaveBeenCalledWith(expect.objectContaining({ start_date: '2024-01-05', end_date: '2024-01-05' })));
    // 单日标记 + 运行区间 + fixture 样本数据警示
    expect(await screen.findByText('单日')).toBeInTheDocument();
    expect(screen.getByText('2024-01-05（单日）')).toBeInTheDocument();
    expect(screen.getByText('请求区间没有已同步的转债行情')).toBeInTheDocument();
    expect(screen.getByText(/内置样本数据集/)).toBeInTheDocument();
    expect(screen.getByText('区间数据不足，无净值曲线')).toBeInTheDocument();
    // 成交明细标的展示 名称（代码）
    expect(screen.getByText('低溢价（113001）')).toBeInTheDocument();
    expect(screen.getByText('1 只 · 10000.00 元')).toBeInTheDocument();
  }, 15000);
});
