import { useCallback, useEffect, useState } from 'react';
import { Button, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiErrorAlert } from '../common';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { strategyLabApi, type StrategyLabSyncRunItem } from '../../api/strategyLab';
import { SL_INPUT_CLASS, SL_PANEL_CLASS, parseSymbols } from '../strategy-lab/utils';

const TEXT_LIMIT = 200;
const SYNC_RUN_POLL_INTERVAL_MS = 10_000;
const SYNC_RUN_POLLING_STORAGE_KEY = 'dsa.data-sync-runs.polling-enabled';

// 同步能力：cb_basic=基础数据 / cb_ohlc=行情 / cb_premium_history=补溢价率与剩余规模 / cb_factors=因子计算 / cb_scheduled=盘后调度链路
type SyncKind = 'cb_basic' | 'cb_ohlc' | 'cb_premium_history' | 'cb_factors' | 'cb_scheduled';

const statusTag = (status: string) => {
  const map: Record<string, string> = { completed: 'success', running: 'processing', failed: 'error', cancelled: 'default' };
  return <Tag color={map[status] ?? 'default'}>{status}</Tag>;
};

const summarizeResult = (run: StrategyLabSyncRunItem): string => {
  const r = run.result ?? {};
  if (r.stage && typeof r.processed === 'number' && typeof r.total === 'number') {
    return `${r.stage} ${r.processed}/${r.total} · 因子=${r.cb_factor_upserted ?? 0}`;
  }
  const entries = Object.entries(r);
  return entries.length ? entries.map(([key, value]) => `${key}=${value}`).join(' · ') : '--';
};

/** 固定宽度、自动换行、超长省略、hover 展示完整内容 */
const WrappedCell: React.FC<{ text: string; className?: string }> = ({ text, className }) => {
  const needsTruncation = text.length > TEXT_LIMIT;
  const display = needsTruncation ? `${text.slice(0, TEXT_LIMIT)}…` : text;
  const content = (
    <span className={`${className ?? ''} block break-all whitespace-normal`}>{display}</span>
  );
  return needsTruncation ? <Tooltip title={text}>{content}</Tooltip> : content;
};

export const DataSyncPanel: React.FC = () => {
  const [syncRuns, setSyncRuns] = useState<StrategyLabSyncRunItem[]>([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  // 同步能力见 SyncKind 定义
  const [syncKind, setSyncKind] = useState<SyncKind>('cb_basic');
  const [includeDelisted, setIncludeDelisted] = useState(false);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [syncSymbols, setSyncSymbols] = useState('');
  const [pollingEnabled, setPollingEnabled] = useState(() => {
    try {
      const stored = window.localStorage.getItem(SYNC_RUN_POLLING_STORAGE_KEY);
      return stored == null ? true : stored === 'true';
    } catch {
      return true;
    }
  });

  const refresh = useCallback(async (targetPage: number, targetSize: number, showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      const resp = await strategyLabApi.listSyncRuns({ page: targetPage, limit: targetSize });
      setSyncRuns(resp.items);
      setTotal(resp.total);
      setError(null);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(page, pageSize); }, [page, pageSize, refresh]);

  useEffect(() => {
    try {
      window.localStorage.setItem(SYNC_RUN_POLLING_STORAGE_KEY, String(pollingEnabled));
    } catch {
      // localStorage is best-effort; the toggle still applies for this session.
    }
  }, [pollingEnabled]);

  useEffect(() => {
    if (!pollingEnabled) return undefined;
    const timer = window.setInterval(() => {
      void refresh(page, pageSize, false);
    }, SYNC_RUN_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [page, pageSize, pollingEnabled, refresh]);

  const waitSyncRun = async (runId: number): Promise<StrategyLabSyncRunItem | null> => {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const resp = await strategyLabApi.listSyncRuns({ limit: 50 });
      const run = resp.items.find((item) => item.id === runId);
      if (run) setSyncRuns(resp.items); // 实时刷新，展示后台同步进度
      if (!run || run.status === 'completed' || run.status === 'failed' || run.status === 'cancelled') return run ?? null;
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
    return null;
  };

  const runAction = async (key: string, action: () => Promise<{ sync_run_id?: number }>) => {
    setActionLoading(key);
    try {
      const result = await action();
      if (result.sync_run_id) await waitSyncRun(result.sync_run_id);
      await refresh(page, pageSize);
      setError(null);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setActionLoading(null);
    }
  };

  const columns: ColumnsType<StrategyLabSyncRunItem> = [
    { title: 'ID', dataIndex: 'id', width: 70 },
    { title: '类型', dataIndex: 'sync_type', width: 180, ellipsis: true },
    { title: '状态', dataIndex: 'status', width: 110, render: statusTag },
    { title: '结果', key: 'result', width: 240, render: (_, run) => (
      <WrappedCell text={summarizeResult(run)} className="text-xs text-secondary-text" />
    ) },
    { title: '错误', dataIndex: 'error_message', width: 180, render: (value: string | null) => (
      value ? <WrappedCell text={value} className="text-danger" /> : '--'
    ) },
    { title: '时间', dataIndex: 'created_at', width: 160, render: (value: string | null) => value ? value.replace('T', ' ').slice(0, 19) : '--' },
    { title: '完成时间', dataIndex: 'completed_at', width: 160, render: (value: string | null) => value ? value.replace('T', ' ').slice(0, 19) : '--' },
    { title: '操作', key: 'actions', width: 90, render: (_, run) => (
      run.status === 'running' ? (
        <Button
          aria-label="取消同步"
          size="small"
          danger
          loading={actionLoading === `cancel-${run.id}`}
          onClick={() => {
            void runAction(`cancel-${run.id}`, async () => {
              await strategyLabApi.cancelSyncRun(run.id);
              return {};
            });
          }}
        >
          取消
        </Button>
      ) : '--'
    ) },
  ];

  return (
    <div className="flex flex-col gap-4">
      <form
        className={SL_PANEL_CLASS}
        onSubmit={(event) => {
          event.preventDefault();
          const payload: {
            market: string;
            source: string;
            sync_type: string;
            include_delisted: boolean;
            start_date?: string;
            end_date?: string;
            symbols: string[];
          } = {
            market: 'cn',
            source: 'opencli',
            sync_type: syncKind,
            include_delisted: includeDelisted,
            symbols: parseSymbols(syncSymbols),
          };
          if (syncKind === 'cb_ohlc') {
            if (startDate) payload.start_date = startDate;
            if (endDate) payload.end_date = endDate;
          }
          if (syncKind === 'cb_factors' && endDate) {
            // 因子计算为单日语义：因子日期走 end_date，缺省由后端取今天
            payload.end_date = endDate;
          }
          void runAction('sync', () => strategyLabApi.syncData(payload));
        }}
      >
        <h2 className="text-base font-semibold text-foreground">数据同步</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">
            来源
            <select aria-label="同步来源" className={`${SL_INPUT_CLASS} mt-1`} value={syncKind} onChange={(event) => setSyncKind(event.target.value as SyncKind)}>
              <option value="cb_basic">可转债基础数据（cb_basic）</option>
              <option value="cb_ohlc">可转债行情（cb_ohlc）</option>
              <option value="cb_premium_history">可转债补溢价/规模（cb_premium_history）</option>
              <option value="cb_factors">可转债因子计算（cb_factors）</option>
              <option value="cb_scheduled">可转债盘后调度同步（基础+行情+因子-cb_scheduled）</option>
            </select>
          </label>
          <label className="text-sm">
            可转债代码
            <input aria-label="同步可转债代码" className={`${SL_INPUT_CLASS} mt-1`} value={syncSymbols} onChange={(event) => setSyncSymbols(event.target.value)} placeholder="可选，逗号分隔" />
          </label>
          {syncKind === 'cb_ohlc' ? (
            <>
              <label className="text-sm">
                起始日期
                <input aria-label="起始日期" type="date" className={`${SL_INPUT_CLASS} mt-1`} value={startDate} onChange={(event) => setStartDate(event.target.value)} />
              </label>
              <label className="text-sm">
                结束日期
                <input aria-label="结束日期" type="date" className={`${SL_INPUT_CLASS} mt-1`} value={endDate} onChange={(event) => setEndDate(event.target.value)} />
              </label>
            </>
          ) : null}
          {syncKind === 'cb_factors' ? (
            <label className="text-sm">
              因子日期
              <input aria-label="因子日期" type="date" className={`${SL_INPUT_CLASS} mt-1`} value={endDate} onChange={(event) => setEndDate(event.target.value)} />
            </label>
          ) : null}
          <label className="text-sm flex items-center gap-2">
            <input aria-label="包含已退市" type="checkbox" className="accent-primary" checked={includeDelisted} onChange={(event) => setIncludeDelisted(event.target.checked)} />
            包含已退市可转债
          </label>
        </div>
        <Button type="primary" htmlType="submit" className="mt-4" loading={actionLoading === 'sync'}>开始同步</Button>
        {error ? <ApiErrorAlert error={error} className="mt-4" /> : null}
      </form>

      <div className={SL_PANEL_CLASS}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold text-foreground">同步记录</h2>
          <label className="flex items-center gap-2 text-sm text-secondary-text">
            <input
              aria-label="自动刷新同步记录"
              type="checkbox"
              className="accent-primary"
              checked={pollingEnabled}
              onChange={(event) => setPollingEnabled(event.target.checked)}
            />
            自动刷新（10 秒）
          </label>
        </div>
        <Table
          rowKey="id"
          size="small"
          className="mt-3"
          columns={columns}
          dataSource={syncRuns}
          loading={loading}
          locale={{ emptyText: '暂无同步记录' }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50],
            showTotal: (count) => `共 ${count} 条`,
            onChange: (nextPage, nextSize) => {
              setPage(nextPage);
              setPageSize(nextSize);
            },
          }}
        />
      </div>
    </div>
  );
};
