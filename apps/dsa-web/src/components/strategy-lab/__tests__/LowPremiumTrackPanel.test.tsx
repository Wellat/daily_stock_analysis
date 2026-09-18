import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LowPremiumTrackPanel } from '../LowPremiumTrackPanel';

const { getPremiumTrack } = vi.hoisted(() => ({ getPremiumTrack: vi.fn() }));

vi.mock('../../../api/strategyLab', () => ({ strategyLabApi: { getPremiumTrack } }));
vi.mock('echarts', () => ({
  init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
}));

function makeTrack(overrides: Record<string, unknown> = {}) {
  return {
    market: 'cn',
    top_n: 10,
    start: '2026-06-22',
    end: '2026-09-16',
    dates: ['2026-09-15', '2026-09-16'],
    bonds: [
      {
        bond_code: '110077',
        bond_name: '洪城转债',
        days_count: 2,
        ratio: 1.0,
        first_date: '2026-09-15',
        last_date: '2026-09-16',
        avg_premium: 0.5,
        best_rank: 1,
        day_indexes: [0, 1],
        premiums: [0.4, 0.6],
      },
      {
        bond_code: '113042',
        bond_name: '上银转债',
        days_count: 1,
        ratio: 0.5,
        first_date: '2026-09-16',
        last_date: '2026-09-16',
        avg_premium: 2.5,
        best_rank: 2,
        day_indexes: [1],
        premiums: [2.5],
      },
    ],
    turnover: [
      { date: '2026-09-15', overlap: null, entered: null, exited: null, threshold: 1.5 },
      { date: '2026-09-16', overlap: 9, entered: 1, exited: 1, threshold: 2.5 },
    ],
    stats: { window_days: 2, distinct_bonds: 11, avg_overlap: 9.0, avg_entered: 1.0 },
    ...overrides,
  };
}

describe('LowPremiumTrackPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders stats, both charts and the membership table', async () => {
    getPremiumTrack.mockResolvedValueOnce(makeTrack());

    render(<LowPremiumTrackPanel />);

    await waitFor(() => expect(getPremiumTrack).toHaveBeenCalledTimes(1));
    expect(getPremiumTrack).toHaveBeenCalledWith(expect.objectContaining({ top_n: 10 }));
    expect(screen.getByLabelText('低溢价在榜分布图')).toBeInTheDocument();
    expect(screen.getByLabelText('低溢价稳定性曲线')).toBeInTheDocument();
    // 统计卡与表格行（名称（代码））
    expect(screen.getByText('洪城转债（110077）')).toBeInTheDocument();
    expect(screen.getByText('9 / 10')).toBeInTheDocument(); // 日均与前日重叠
    expect(screen.getByText('11')).toBeInTheDocument(); // 上榜转债数
  });

  it('switching range presets reloads with new dates', async () => {
    getPremiumTrack.mockResolvedValue(makeTrack());

    render(<LowPremiumTrackPanel />);
    await waitFor(() => expect(getPremiumTrack).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('radio', { name: '近30天' }));
    await waitFor(() => expect(getPremiumTrack).toHaveBeenCalledTimes(2));
    expect(getPremiumTrack.mock.calls[1]?.[0]).toEqual(
      expect.objectContaining({ start: expect.any(String), end: expect.any(String), top_n: 10 }),
    );

    // 切换榜单规模触发重载并带新 top_n
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '榜单规模' }));
    const option = await screen.findByText('前 5 只');
    fireEvent.click(option);
    await waitFor(() => expect(getPremiumTrack).toHaveBeenLastCalledWith(expect.objectContaining({ top_n: 5 })));
  });

  it('shows empty state when no data in range', async () => {
    getPremiumTrack.mockResolvedValueOnce(makeTrack({ dates: [], bonds: [], turnover: [], stats: { window_days: 0, distinct_bonds: 0, avg_overlap: null, avg_entered: null } }));

    render(<LowPremiumTrackPanel />);

    expect(await screen.findByText('暂无榜单数据')).toBeInTheDocument();
    expect(screen.queryByLabelText('低溢价在榜分布图')).not.toBeInTheDocument();
  });
});
