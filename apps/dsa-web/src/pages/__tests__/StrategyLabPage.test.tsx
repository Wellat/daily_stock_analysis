import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import StrategyLabPage from '../StrategyLabPage';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';

const api = vi.hoisted(() => ({
  listStrategies: vi.fn(), listRuns: vi.fn(), listBatches: vi.fn(), listSignals: vi.fn(), listSyncRuns: vi.fn(),
  createRun: vi.fn(), listRunTrades: vi.fn(), getRun: vi.fn(), createBatch: vi.fn(), getBatch: vi.fn(),
  retryBatch: vi.fn(), resumeBatch: vi.fn(), deleteBatch: vi.fn(), getBatchStreamUrl: vi.fn(), createSignal: vi.fn(), confirmSignal: vi.fn(), syncData: vi.fn(),
}));

vi.mock('echarts', () => ({
  init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
}));
vi.mock('../../api/strategyLab', () => ({ strategyLabApi: api }));
vi.mock('../../api/portfolio', () => ({ portfolioApi: { getAccounts: vi.fn().mockResolvedValue({ accounts: [] }) } }));

describe('StrategyLabPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listStrategies.mockResolvedValue([
      { strategy_id: 'double-low', name: '双低轮动', instrument_types: ['convertible_bond'], markets: ['cn'] },
      { strategy_id: 'ma-crossover', name: '均线交叉', instrument_types: ['convertible_bond'], markets: ['cn'] },
    ]);
    api.listRuns.mockResolvedValue([]);
    api.listBatches.mockResolvedValue([]);
    api.listSignals.mockResolvedValue([]);
    api.listSyncRuns.mockResolvedValue([]);
    api.syncData.mockResolvedValue({ sync_run_id: 1 });
    api.createBatch.mockResolvedValue({ id: 1, status: 'completed', total_tasks: 2, completed_tasks: 2 });
    api.createRun.mockResolvedValue({ id: 1, strategy_name: '双低轮动', metrics: null, status: 'completed', equity_curve: [] });
    api.listRunTrades.mockResolvedValue([]);
  });

  it('renders four top-level tabs and keeps backtest on the default research tab', async () => {
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    expect(await screen.findByRole('heading', { name: '策略实验室' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '策略研究' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '参数搜索' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '数据同步' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '实盘信号' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '运行回测' })).toBeInTheDocument();
  }, 15000);

  it('submits a backtest run with the selected strategy', async () => {
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '运行回测' });
    fireEvent.change(screen.getByLabelText('策略'), { target: { value: 'ma-crossover' } });
    fireEvent.click(screen.getByRole('button', { name: '运行回测' }));
    await waitFor(() => expect(api.createRun).toHaveBeenCalledWith(expect.objectContaining({ strategy_id: 'ma-crossover', instrument_type: 'convertible_bond' })));
  }, 15000);

  it('switches to the data-sync tab and submits a sync request', async () => {
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '运行回测' });
    fireEvent.click(screen.getByRole('tab', { name: '数据同步' }));
    fireEvent.change(await screen.findByLabelText('同步来源'), { target: { value: 'jisilu' } });
    fireEvent.click(screen.getByRole('button', { name: '开始同步' }));
    await waitFor(() => expect(api.syncData).toHaveBeenCalledWith({ market: 'cn', source: 'jisilu', symbols: [] }));
  }, 15000);

  it('switches to the parameter-search tab and submits an async batch', async () => {
    render(<UiLanguageProvider><StrategyLabPage /></UiLanguageProvider>);
    await screen.findByRole('button', { name: '运行回测' });
    fireEvent.click(screen.getByRole('tab', { name: '参数搜索' }));
    await screen.findByRole('button', { name: '运行参数批次' });
    fireEvent.click(screen.getByRole('button', { name: '运行参数批次' }));
    await waitFor(() => expect(api.createBatch).toHaveBeenCalledWith(expect.objectContaining({ strategy_id: 'double-low', run_async: true })));
  }, 15000);
});
