import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import StrategyLabPage from '../StrategyLabPage';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';

const api = vi.hoisted(() => ({
  listStrategies: vi.fn(), listRuns: vi.fn(), listBatches: vi.fn(), listConfigs: vi.fn(),
  createRun: vi.fn(), listRunTrades: vi.fn(), getRun: vi.fn(), createBatch: vi.fn(), getBatch: vi.fn(),
  retryBatch: vi.fn(), resumeBatch: vi.fn(), deleteBatch: vi.fn(), getBatchStreamUrl: vi.fn(),
  getPremiumTrack: vi.fn(), createConfig: vi.fn(), deleteConfig: vi.fn(),
  getRunReportUrl: vi.fn(),
}));

vi.mock('echarts', () => ({
  init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
}));
vi.mock('../../api/strategyLab', () => ({ strategyLabApi: api }));
vi.mock('../../api/portfolio', () => ({ portfolioApi: { getAccounts: vi.fn().mockResolvedValue({ accounts: [] }) } }));

const ROTATION_STRATEGY = {
  strategy_id: 'rotation',
  name: '可转债轮动',
  instrument_types: ['convertible_bond'],
  markets: ['cn'],
  factors: [
    { factor: 'price', label: '转债价格', column: 'close', transform: 'raw', default_direction: 'asc' },
    { factor: 'premium_rate', label: '转股溢价率', column: 'premium_rate', transform: 'raw', default_direction: 'asc' },
  ],
  score_presets: [
    { preset: 'double_low', label: '双低', factors: [{ factor: 'price', direction: 'asc', weight: 1 }, { factor: 'premium_rate', direction: 'asc', weight: 1 }] },
    { preset: 'low_premium', label: '低溢价', factors: [{ factor: 'premium_rate', direction: 'asc', weight: 1 }] },
  ],
};

const LEGACY_STRATEGIES = [
  { strategy_id: 'double-low', name: '双低轮动', instrument_types: ['convertible_bond'], markets: ['cn'] },
  { strategy_id: 'ma-crossover', name: '均线交叉', instrument_types: ['convertible_bond'], markets: ['cn'] },
];

describe('StrategyLabPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listStrategies.mockResolvedValue([ROTATION_STRATEGY, ...LEGACY_STRATEGIES]);
    api.listRuns.mockResolvedValue([]);
    api.listBatches.mockResolvedValue([]);
    api.listConfigs.mockResolvedValue([]);
    api.createBatch.mockResolvedValue({ id: 1, status: 'completed', total_tasks: 2, completed_tasks: 2 });
    api.createRun.mockResolvedValue({ id: 1, strategy_name: '可转债轮动', engine_name: 'cb_rotation_v1', status: 'completed', start_date: '2024-01-02', end_date: '2024-01-31', metrics: null, equity_curve: [] });
    api.listRunTrades.mockResolvedValue([]);
    api.getRunReportUrl.mockReturnValue('/api/v1/strategy-lab/runs/1/report?format=md');
    api.getPremiumTrack.mockResolvedValue({
      market: 'cn', top_n: 10, start: '2026-06-22', end: '2026-09-16', dates: [], bonds: [], turnover: [],
      stats: { window_days: 0, distinct_bonds: 0, avg_overlap: null, avg_entered: null },
    });
  });

  it('renders three top-level tabs with rotation as the default strategy', async () => {
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    expect(await screen.findByRole('heading', { name: '策略实验室' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '策略研究' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '参数搜索' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '低溢价跟踪' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '实盘信号' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '数据同步' })).not.toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '开始回测' })).toBeInTheDocument();
    // rotation 为默认策略，表单包含轮动参数区
    expect(screen.getAllByLabelText('换仓频率类型').length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText('打分预设').length).toBeGreaterThan(0);
    // 实盘导入入口已按重构决策移除
    expect(screen.queryByRole('button', { name: '导入实盘配置' })).not.toBeInTheDocument();
  }, 15000);

  it('opens the premium-track tab and requests the membership data', async () => {
    api.getPremiumTrack.mockResolvedValue({
      market: 'cn', top_n: 10, start: '2026-06-22', end: '2026-09-16',
      dates: ['2026-09-15', '2026-09-16'],
      bonds: [{ bond_code: '110077', bond_name: '洪城转债', days_count: 2, ratio: 1, first_date: '2026-09-15', last_date: '2026-09-16', avg_premium: 0.5, best_rank: 1, day_indexes: [0, 1], premiums: [0.4, 0.6] }],
      turnover: [{ date: '2026-09-15', overlap: null, entered: null, exited: null, threshold: 1.5 }, { date: '2026-09-16', overlap: 9, entered: 1, exited: 1, threshold: 2.5 }],
      stats: { window_days: 2, distinct_bonds: 11, avg_overlap: 9, avg_entered: 1 },
    });
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '开始回测' });

    fireEvent.click(screen.getByRole('tab', { name: '低溢价跟踪' }));
    expect(await screen.findByLabelText('低溢价在榜分布图')).toBeInTheDocument();
    expect(screen.getByLabelText('低溢价稳定性曲线')).toBeInTheDocument();
    expect(screen.getByText('洪城转债（110077）')).toBeInTheDocument();
  }, 15000);

  it('submits a rotation run with rebalance parameters', async () => {
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '开始回测' });
    fireEvent.change(screen.getByLabelText('换仓频率'), { target: { value: '5' } });
    fireEvent.change(screen.getByLabelText('最大持有数量'), { target: { value: '20' } });
    fireEvent.click(screen.getByRole('button', { name: '开始回测' }));
    await waitFor(() => expect(api.createRun).toHaveBeenCalledWith(expect.objectContaining({
      strategy_id: 'rotation',
      instrument_type: 'convertible_bond',
      parameters: expect.objectContaining({
        rebalance_unit: 'trading_day',
        rebalance_interval: 5,
        max_positions: 20,
        score_preset: 'double_low',
        score_factors: [
          { factor: 'price', direction: 'asc', weight: 1 },
          { factor: 'premium_rate', direction: 'asc', weight: 1 },
        ],
      }),
    })));
  }, 15000);

  it('submits a legacy strategy run with max_positions only', async () => {
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '开始回测' });
    fireEvent.change(screen.getByLabelText('策略'), { target: { value: 'ma-crossover' } });
    fireEvent.click(screen.getByRole('button', { name: '开始回测' }));
    await waitFor(() => expect(api.createRun).toHaveBeenCalledWith(expect.objectContaining({
      strategy_id: 'ma-crossover',
      parameters: { max_positions: 2 },
    })));
  }, 15000);

  it('saves the current config through the modal', async () => {
    api.createConfig.mockResolvedValue({ id: 9, name: '双低月轮动' });
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '开始回测' });

    fireEvent.click(screen.getByRole('button', { name: '保存配置' }));
    expect(await screen.findByLabelText('配置名称')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('配置名称'), { target: { value: '双低月轮动' } });
    const saveButtons = screen.getAllByRole('button', { name: /保\s*存/ });
    fireEvent.click(saveButtons[saveButtons.length - 1]); // 模态框确认按钮在 body 末尾的 portal 中

    await waitFor(() => expect(api.createConfig).toHaveBeenCalledWith(expect.objectContaining({
      name: '双低月轮动',
      strategy_id: 'rotation',
      parameters: expect.objectContaining({ rebalance_unit: 'trading_day' }),
    })));
  }, 15000);

  it('loads a saved config and applies its parameters', async () => {
    api.listConfigs.mockResolvedValue([
      {
        id: 7, config_uid: 'u7', name: '月度三低', description: null,
        strategy_id: 'rotation', market: 'cn', instrument_type: 'convertible_bond',
        parameters: {
          rebalance_unit: 'month', rebalance_interval: 1, max_positions: 10,
          score_preset: 'triple_low',
          score_factors: [{ factor: 'premium_rate', direction: 'asc', weight: 2 }],
        },
        symbols: ['113001'],
      },
    ]);
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '开始回测' });

    const configSelects = screen.getAllByRole('combobox', { name: '加载已保存配置' });
    const selector = configSelects[0].closest('.ant-select')?.querySelector('.ant-select-selector') as HTMLElement;
    fireEvent.mouseDown(selector);
    fireEvent.click(await screen.findByText('月度三低'));

    await waitFor(() => expect((screen.getByLabelText('换仓频率') as HTMLInputElement).value).toBe('1'));
    expect(screen.getByLabelText('最大持有数量')).toHaveValue(10);
    expect(screen.getByLabelText('标的筛选')).toHaveValue('113001');
    expect(screen.getByText('转股溢价率')).toBeInTheDocument();
  }, 15000);

  it('exports reports for a completed rotation run', async () => {
    const openSpy = vi.fn();
    vi.stubGlobal('open', openSpy);
    api.createRun.mockResolvedValue({
      id: 5, strategy_name: '可转债轮动', engine_name: 'cb_rotation_v1', status: 'completed',
      start_date: '2026-01-05', end_date: '2026-09-30', initial_cash: 500000, final_equity: 469829,
      metrics: {
        total_return_pct: -6.04, period_count: 181, turnover_avg_pct: 2.81,
        benchmark_metrics: { mode: 'equal_weight_pool', total_return_pct: -5.88, relative_excess: { total_return_pct: -0.16 } },
      },
      equity_curve: [
        { trade_date: '2026-01-05', equity: 500000, cash: 500000, positions_value: 0, benchmark_equity: 500000, drawdown_pct: 0, daily_return_pct: 0, holdings: [], holdings_count: 0 },
        { trade_date: '2026-01-06', equity: 501500, cash: 1000, positions_value: 500500, benchmark_equity: 499000, drawdown_pct: 0, daily_return_pct: 0.3, turnover_pct: 99.8, holdings: ['113001', '113002'], holdings_count: 2 },
      ],
    });
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '开始回测' });
    fireEvent.click(screen.getByRole('button', { name: '开始回测' }));

    // 三行汇总表（当前策略/基准/相对超额）
    expect(await screen.findByText('当前策略')).toBeInTheDocument();
    expect(screen.getByText(/基准策略（标的池等权）/)).toBeInTheDocument();
    expect(screen.getByText('相对超额')).toBeInTheDocument();
    // 持仓明细与导出入口
    expect(await screen.findByText('113001、113002')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '导出报告' }));
    expect(openSpy).toHaveBeenCalledWith('/api/v1/strategy-lab/runs/1/report?format=md', '_blank');
    vi.unstubAllGlobals();
  }, 15000);

  it('runs a single-day backtest with start=end and flags fixture-sample results', async () => {
    api.createRun.mockResolvedValue({ id: 2, strategy_name: '可转债轮动', engine_name: 'fixture_rotation_v1', status: 'completed', start_date: '2024-01-05', end_date: '2024-01-05', metrics: null, equity_curve: [] });
    api.listRunTrades.mockResolvedValue([
      { id: 1, run_id: 2, trade_date: '2024-01-05', symbol: '113001', symbol_name: '低溢价', side: 'buy', quantity: 100, price: 100, amount: 10000, fee: 0 },
    ]);
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '开始回测' });

    fireEvent.click(screen.getByText('单日运行'));
    expect(screen.queryByLabelText('结束日期')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('日期'), { target: { value: '2024-01-05' } });
    fireEvent.click(screen.getByRole('button', { name: '运行单日回测' }));

    await waitFor(() => expect(api.createRun).toHaveBeenCalledWith(expect.objectContaining({ start_date: '2024-01-05', end_date: '2024-01-05' })));
    expect(await screen.findByText('单日')).toBeInTheDocument();
    expect(screen.getByText(/2024-01-05（单日）/)).toBeInTheDocument();
    expect(screen.getByText('请求区间没有已同步的转债行情')).toBeInTheDocument();
    expect(screen.getByText(/内置样本数据集/)).toBeInTheDocument();
    expect(screen.getByText('区间数据不足，无回测走势')).toBeInTheDocument();
    expect(screen.getByText('低溢价（113001）')).toBeInTheDocument();
    expect(screen.getByText(/当日买入 1 只 · 10000\.00 元/)).toBeInTheDocument();
  }, 15000);

  it('shows the run history table with key columns and default 5-per-page pagination', async () => {
    api.listRuns.mockResolvedValue(
      Array.from({ length: 7 }, (_, index) => ({
        id: index + 1,
        run_uid: `u${index + 1}`,
        strategy_id: index % 2 === 0 ? 'rotation' : 'double-low',
        strategy_name: index % 2 === 0 ? '可转债轮动' : '双低轮动',
        engine_name: index % 2 === 0 ? 'cb_rotation_v1' : 'database_double_low_v1',
        status: 'completed',
        market: 'cn',
        instrument_type: 'convertible_bond',
        start_date: '2026-06-01',
        end_date: '2026-09-30',
        initial_cash: 500000,
        final_equity: 480000 + index * 1000,
        benchmark_return_pct: -10.05,
        created_at: `2026-10-04T20:${10 + index}:00`,
        parameters: index % 2 === 0
          ? { score_preset: 'double_low', max_positions: 20, rebalance_unit: 'trading_day', rebalance_interval: 10 }
          : { max_positions: 2 },
        metrics: {
          total_return_pct: index % 2 === 0 ? 0.85 : -3.2,
          max_drawdown_pct: -2.96,
          sharpe_ratio: -0.42,
          trade_count: 120,
        },
      })),
    );
    api.getRun.mockResolvedValue({
      id: 1, strategy_name: '可转债轮动', engine_name: 'cb_rotation_v1', status: 'completed',
      start_date: '2026-06-01', end_date: '2026-09-30', initial_cash: 500000,
      metrics: null, equity_curve: [], parameters: {}, symbols: [],
    });
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '开始回测' });

    // 关键列表头
    expect(await screen.findByText('总收益率')).toBeInTheDocument();
    expect(screen.getByText('最大回撤')).toBeInTheDocument();
    expect(screen.getByText('夏普')).toBeInTheDocument();
    expect(screen.getByText('参数')).toBeInTheDocument();
    // 默认 5 条/页：第 6 条（#6）不在首页，分页总数为 7
    expect(screen.getByText('共 7 条')).toBeInTheDocument();
    expect(screen.queryByText('#6')).not.toBeInTheDocument();
    expect(screen.getByText('#1')).toBeInTheDocument();
    // 参数摘要（rotation: 双低 · 20只 · 每10日；legacy: 2只）
    expect(screen.getAllByText('双低 · 20只 · 每10日').length).toBeGreaterThan(0);
    expect(screen.getAllByText('2只').length).toBeGreaterThan(0);
    // 点击行加载详情
    fireEvent.click(screen.getByText('#1'));
    await waitFor(() => expect(api.getRun).toHaveBeenCalledWith(1));
    await waitFor(() => expect(api.listRunTrades).toHaveBeenCalledWith(1));
  }, 15000);

  it('switches to the parameter-search tab and submits an async batch', async () => {
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '开始回测' });
    fireEvent.click(screen.getByRole('tab', { name: '参数搜索' }));
    await screen.findByRole('button', { name: '运行参数批次' });
    fireEvent.change(screen.getByLabelText('换仓频率候选'), { target: { value: '1,5' } });
    fireEvent.click(screen.getByRole('button', { name: '运行参数批次' }));
    await waitFor(() => expect(api.createBatch).toHaveBeenCalledWith(expect.objectContaining({
      strategy_id: 'rotation',
      run_async: true,
      parameter_grid: { max_positions: [1, 2, 3], rebalance_interval: [1, 5] },
    })));
  }, 15000);
});
