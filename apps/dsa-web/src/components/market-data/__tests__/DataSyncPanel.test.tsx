import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DataSyncPanel } from '../DataSyncPanel';

const { listSyncRuns, getScheduledTasks } = vi.hoisted(() => ({
  listSyncRuns: vi.fn(),
  getScheduledTasks: vi.fn(),
}));

vi.mock('../../../api/strategyLab', () => ({
  strategyLabApi: { listSyncRuns },
}));

vi.mock('../../../api/systemConfig', () => ({
  systemConfigApi: { getScheduledTasks },
}));

describe('DataSyncPanel scheduled-task overview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listSyncRuns.mockResolvedValue({ items: [], total: 0, page: 1, limit: 10 });
  });

  it('renders the overview table with merged registry and heartbeat state', async () => {
    getScheduledTasks.mockResolvedValue({
      items: [
        {
          name: 'etf_daily_sync',
          label: 'ETF 日线同步',
          description: '同步启用中 ETF 信号实例标的的后复权日线（增量）',
          schedule: '每交易日 20:30',
          enabled: true,
          lastRunStatus: 'success',
          lastRunDetail: null,
          lastRunAt: '2026-10-27T20:30:12',
          lastRunDurationMs: 4200,
          lastSuccessAt: '2026-10-27T20:30:12',
        },
        {
          name: 'agent_event_monitor',
          label: '告警巡检',
          description: '评估启用的告警规则并推送触发通知（盘中时段）',
          schedule: '每 5 分钟',
          enabled: false,
          lastRunStatus: null,
          lastRunDetail: null,
          lastRunAt: null,
          lastRunDurationMs: null,
          lastSuccessAt: null,
        },
        {
          name: 'cb_after_close_sync',
          label: '可转债盘后同步',
          description: '可转债基础数据、行情、因子与持仓行情（盘后调度链路）',
          schedule: '每交易日 20:00',
          enabled: true,
          lastRunStatus: 'failed',
          lastRunDetail: 'baostock login failed',
          lastRunAt: '2026-10-27T20:00:40',
          lastRunDurationMs: 3000,
          lastSuccessAt: '2026-10-26T20:18:00',
        },
      ],
    });

    render(<DataSyncPanel />);

    expect(await screen.findByText('定时任务总览')).toBeInTheDocument();
    expect(screen.getByText('ETF 日线同步')).toBeInTheDocument();
    expect(screen.getByText('每交易日 20:30')).toBeInTheDocument();
    // 状态列：成功/停用/失败
    expect(screen.getByText('正常')).toBeInTheDocument();
    expect(screen.getByText('已停用')).toBeInTheDocument();
    expect(screen.getByText('最近失败')).toBeInTheDocument();
    // 最近成功与最近运行同刻时出现两处
    expect(screen.getAllByText(/2026-10-27 20:30:12/).length).toBeGreaterThan(0);
  });

  it('keeps sync records working when overview api fails', async () => {
    getScheduledTasks.mockRejectedValue({ parsedError: { message: 'down' } });
    listSyncRuns.mockResolvedValue({
      items: [
        {
          id: 7,
          sync_type: 'cb_scheduled',
          status: 'completed',
          result: {},
          error_message: null,
          created_at: '2026-10-27T20:00:03',
          completed_at: '2026-10-27T20:20:08',
        },
      ],
      total: 1,
      page: 1,
      limit: 10,
    });

    render(<DataSyncPanel />);

    expect(await screen.findByText('同步记录')).toBeInTheDocument();
    expect(await screen.findByText('7')).toBeInTheDocument();
    // 轮询开关存在且可切换（总览失败不影响面板）
    fireEvent.click(screen.getByLabelText('自动刷新同步记录'));
    expect(screen.getByLabelText('自动刷新同步记录')).not.toBeChecked();
    await waitFor(() => expect(getScheduledTasks).toHaveBeenCalled());
  });
});
