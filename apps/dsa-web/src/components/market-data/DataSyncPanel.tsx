import { useCallback, useEffect, useState } from 'react';
import { Button, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiErrorAlert } from '../common';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { strategyLabApi, type StrategyLabSyncRunItem } from '../../api/strategyLab';
import { SL_INPUT_CLASS, SL_PANEL_CLASS, parseSymbols } from '../strategy-lab/utils';

const TEXT_LIMIT = 200;

const statusTag = (status: string) => {
  const map: Record<string, string> = { completed: 'success', running: 'processing', failed: 'error' };
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
  const [syncSource, setSyncSource] = useState('fixture');
  const [syncSymbols, setSyncSymbols] = useState('');

  const refresh = useCallback(async (targetPage: number, targetSize: number) => {
    setLoading(true);
    try {
      const resp = await strategyLabApi.listSyncRuns({ page: targetPage, limit: targetSize });
      setSyncRuns(resp.items);
      setTotal(resp.total);
      setError(null);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(page, pageSize); }, [page, pageSize, refresh]);

  const waitSyncRun = async (runId: number): Promise<StrategyLabSyncRunItem | null> => {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const resp = await strategyLabApi.listSyncRuns({ limit: 50 });
      const run = resp.items.find((item) => item.id === runId);
      if (run) setSyncRuns(resp.items); // 实时刷新，展示后台同步进度
      if (!run || run.status === 'completed' || run.status === 'failed') return run ?? null;
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
    { title: '类型', dataIndex: 'sync_type', ellipsis: true },
    { title: '状态', dataIndex: 'status', width: 110, render: statusTag },
    { title: '结果', key: 'result', width: 260, render: (_, run) => (
      <WrappedCell text={summarizeResult(run)} className="text-xs text-secondary-text" />
    ) },
    { title: '错误', dataIndex: 'error_message', width: 260, render: (value: string | null) => (
      value ? <WrappedCell text={value} className="text-danger" /> : '--'
    ) },
    { title: '时间', dataIndex: 'created_at', width: 160, render: (value: string | null) => value ? value.replace('T', ' ').slice(0, 19) : '--' },
  ];

  return (
    <div className="flex flex-col gap-4">
      <form
        className={SL_PANEL_CLASS}
        onSubmit={(event) => {
          event.preventDefault();
          void runAction('sync', () => strategyLabApi.syncData({ market: 'cn', source: syncSource, symbols: parseSymbols(syncSymbols) }));
        }}
      >
        <h2 className="text-base font-semibold text-foreground">数据同步</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">
            来源
            <select aria-label="同步来源" className={`${SL_INPUT_CLASS} mt-1`} value={syncSource} onChange={(event) => setSyncSource(event.target.value)}>
              <option value="fixture">内置样例</option>
              <option value="akshare">AkShare</option>
              <option value="jisilu">集思录</option>
              <option value="opencli">OpenCLI</option>
            </select>
          </label>
          <label className="text-sm">
            可转债代码
            <input aria-label="同步可转债代码" className={`${SL_INPUT_CLASS} mt-1`} value={syncSymbols} onChange={(event) => setSyncSymbols(event.target.value)} placeholder="可选，逗号分隔" />
          </label>
        </div>
        <Button type="primary" htmlType="submit" className="mt-4" loading={actionLoading === 'sync'}>开始同步</Button>
        {error ? <ApiErrorAlert error={error} className="mt-4" /> : null}
      </form>

      <div className={SL_PANEL_CLASS}>
        <h2 className="text-base font-semibold text-foreground">同步记录</h2>
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
