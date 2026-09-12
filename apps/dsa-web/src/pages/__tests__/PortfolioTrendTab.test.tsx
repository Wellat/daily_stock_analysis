import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PortfolioTrendTab } from '../../components/portfolio/PortfolioTrendTab';

const { getTrend } = vi.hoisted(() => ({ getTrend: vi.fn() }));

vi.mock('../../api/portfolio', () => ({ portfolioApi: { getTrend } }));
vi.mock('echarts', () => ({
  init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
}));

function makeTrend(items: Array<Record<string, unknown>>, overrides: Record<string, unknown> = {}) {
  return {
    accountId: undefined,
    costMethod: 'fifo',
    currency: 'CNY',
    fromDate: '2026-06-15',
    toDate: '2026-09-12',
    backfilled: 0,
    truncated: false,
    items,
    ...overrides,
  };
}

describe('PortfolioTrendTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads the default 90d range and renders both charts', async () => {
    getTrend.mockResolvedValueOnce(makeTrend([
      { date: '2026-09-10', totalCash: 1000, totalMarketValue: 20000, totalEquity: 21000, realizedPnl: 50, unrealizedPnl: 150, totalPnl: 200 },
      { date: '2026-09-11', totalCash: 1000, totalMarketValue: 20500, totalEquity: 21500, realizedPnl: 50, unrealizedPnl: 200, totalPnl: 250 },
    ]));

    render(<PortfolioTrendTab accountId={7} costMethod="fifo" />);

    await waitFor(() => expect(getTrend).toHaveBeenCalledTimes(1));
    expect(getTrend).toHaveBeenCalledWith(expect.objectContaining({
      accountId: 7,
      costMethod: 'fifo',
      start: expect.any(String),
      end: expect.any(String),
    }));
    expect(await screen.findByLabelText('总市值趋势图')).toBeInTheDocument();
    expect(screen.getByLabelText('总收益趋势图')).toBeInTheDocument();
  });

  it('switching range preset updates the query window', async () => {
    getTrend.mockResolvedValue(makeTrend([]));

    render(<PortfolioTrendTab costMethod="avg" />);
    await waitFor(() => expect(getTrend).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('radio', { name: '近7天' }));

    await waitFor(() => expect(getTrend).toHaveBeenCalledTimes(2));
    const secondCall = getTrend.mock.calls[1]?.[0] as Record<string, unknown>;
    expect(secondCall).toEqual(expect.objectContaining({
      costMethod: 'avg',
      start: expect.any(String),
      end: expect.any(String),
    }));
  });

  it('shows the empty state when there are no trend points', async () => {
    getTrend.mockResolvedValueOnce(makeTrend([]));

    render(<PortfolioTrendTab costMethod="fifo" />);

    expect(await screen.findByText('暂无趋势数据')).toBeInTheDocument();
    expect(screen.queryByLabelText('总市值趋势图')).not.toBeInTheDocument();
  });

  it('shows the truncation warning when backfill is capped', async () => {
    getTrend.mockResolvedValueOnce(
      makeTrend([
        { date: '2026-09-11', totalCash: 0, totalMarketValue: 1, totalEquity: 1, realizedPnl: 0, unrealizedPnl: 0, totalPnl: 0 },
      ], { truncated: true, backfilled: 400 }),
    );

    render(<PortfolioTrendTab costMethod="fifo" />);

    expect(await screen.findByText(/本次仅回补最近的部分缺失日期/)).toBeInTheDocument();
    expect(screen.getByText(/本次回补 400 天/)).toBeInTheDocument();
  });
});
