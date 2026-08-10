import { useCallback, useEffect, useState } from 'react';
import { Button, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiErrorAlert } from '../common';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { strategyLabApi, type StrategyLabSyncRunItem } from '../../api/strategyLab';
import { SL_INPUT_CLASS, SL_PANEL_CLASS, parseSymbols } from './utils';

const statusTag = (status: string) => {
  const map: Record<string, string> = { completed: 'success', running: 'processing', failed: 'error' };
  return <Tag color={map[status] ?? 'default'}>{status}</Tag>;
};

export const DataSyncPanel: React.FC = () => {
  const [syncRuns, setSyncRuns] = useState<StrategyLabSyncRunItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [syncSource, setSyncSource] = useState('fixture');
  const [syncSymbols, setSyncSymbols] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setSyncRuns(await strategyLabApi.listSyncRuns());
      setError(null);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const runAction = async (key: string, action: () => Promise<unknown>) => {
    setActionLoading(key);
    try {
      await action();
      await refresh();
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
    { title: '结果', key: 'result', width: 200, render: (_, run) => (
      <span className="text-xs text-secondary-text">
        {Object.entries(run.result ?? {}).map(([key, value]) => `${key}=${value}`).join(' · ') || '--'}
      </span>
    ) },
    { title: '错误', dataIndex: 'error_message', ellipsis: true, render: (value: string | null) => value ? <span className="text-danger">{value}</span> : '--' },
    { title: '时间', dataIndex: 'created_at', width: 160, render: (value: string | null) => value ? value.replace('T', ' ').slice(0, 19) : '--' },
  ];

  return (
    <div className="grid gap-4 xl:grid-cols-2">
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
          pagination={false}
          loading={loading}
          locale={{ emptyText: '暂无同步记录' }}
        />
      </div>
    </div>
  );
};
