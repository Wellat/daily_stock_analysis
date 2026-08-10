import { useCallback, useEffect, useState } from 'react';
import { Button, Progress, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { RotateCcw, Trash2 } from 'lucide-react';
import { ApiErrorAlert } from '../common';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import {
  strategyLabApi,
  type StrategyLabBatchItem,
  type StrategyLabStrategyItem,
} from '../../api/strategyLab';
import { SL_INPUT_CLASS, SL_PANEL_CLASS, parsePositiveNumbers, parseSymbols } from './utils';

const today = new Date().toISOString().slice(0, 10);

const statusTag = (status: string) => {
  const map: Record<string, string> = { completed: 'success', partial_failed: 'warning', failed: 'error', running: 'processing', pending: 'default' };
  return <Tag color={map[status] ?? 'default'}>{status}</Tag>;
};

export const ParameterSearchPanel: React.FC = () => {
  const [strategies, setStrategies] = useState<StrategyLabStrategyItem[]>([]);
  const [batches, setBatches] = useState<StrategyLabBatchItem[]>([]);
  const [selectedBatch, setSelectedBatch] = useState<StrategyLabBatchItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [strategyId, setStrategyId] = useState('double-low');
  const [runForm, setRunForm] = useState({ startDate: '2024-01-02', endDate: today, initialCash: '100000', symbols: '' });
  const [batchPositions, setBatchPositions] = useState('1,2,3');
  const [runBatchAsync, setRunBatchAsync] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [strategyItems, batchItems] = await Promise.all([strategyLabApi.listStrategies(), strategyLabApi.listBatches()]);
      setStrategies(strategyItems);
      setBatches(batchItems);
      setError(null);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const selectBatch = async (batchId: number) => {
    setActionLoading(`batch-${batchId}`);
    try {
      setSelectedBatch(await strategyLabApi.getBatch(batchId));
      setError(null);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setActionLoading(null);
    }
  };

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

  useEffect(() => {
    if (!selectedBatch || ['completed', 'partial_failed'].includes(selectedBatch.status)) return undefined;
    const stream = new EventSource(strategyLabApi.getBatchStreamUrl(selectedBatch.id));
    stream.addEventListener('progress', () => { void selectBatch(selectedBatch.id); void refresh(); });
    stream.addEventListener('batch_done', () => { stream.close(); void selectBatch(selectedBatch.id); void refresh(); });
    stream.onerror = () => stream.close();
    return () => stream.close();
  }, [selectedBatch?.id, selectedBatch?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const batchColumns: ColumnsType<StrategyLabBatchItem> = [
    { title: '批次', dataIndex: 'id', width: 70, render: (id: number) => `#${id}` },
    { title: '策略', dataIndex: 'strategy_name', ellipsis: true },
    { title: '状态', dataIndex: 'status', width: 130, render: statusTag },
    { title: '进度', key: 'progress', width: 160, render: (_, batch) => (
      <Progress percent={batch.total_tasks ? Math.round((batch.completed_tasks / batch.total_tasks) * 100) : 0} size="small" format={() => `${batch.success_tasks}/${batch.total_tasks}`} />
    ) },
  ];

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <form
        className={SL_PANEL_CLASS}
        onSubmit={(event) => {
          event.preventDefault();
          void runAction('batch', async () => {
            const batch = await strategyLabApi.createBatch({
              strategy_id: strategyId,
              market: 'cn',
              instrument_type: 'convertible_bond',
              base_config: {
                strategy_id: strategyId,
                market: 'cn',
                instrument_type: 'convertible_bond',
                start_date: runForm.startDate,
                end_date: runForm.endDate,
                initial_cash: Number(runForm.initialCash),
                symbols: parseSymbols(runForm.symbols),
              },
              parameter_grid: { max_positions: parsePositiveNumbers(batchPositions) },
              run_async: runBatchAsync,
            });
            setSelectedBatch(batch);
          });
        }}
      >
        <h2 className="text-base font-semibold text-foreground">参数搜索</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">
            策略
            <select aria-label="策略" className={`${SL_INPUT_CLASS} mt-1`} value={strategyId} onChange={(event) => setStrategyId(event.target.value)}>
              {strategies.map((strategy) => <option key={strategy.strategy_id} value={strategy.strategy_id}>{strategy.name}</option>)}
            </select>
          </label>
          <label className="text-sm">
            最大持仓候选
            <input aria-label="最大持仓候选" className={`${SL_INPUT_CLASS} mt-1`} value={batchPositions} onChange={(event) => setBatchPositions(event.target.value)} />
          </label>
          <label className="text-sm">
            开始日期
            <input aria-label="开始日期" className={`${SL_INPUT_CLASS} mt-1`} type="date" value={runForm.startDate} onChange={(event) => setRunForm({ ...runForm, startDate: event.target.value })} required />
          </label>
          <label className="text-sm">
            结束日期
            <input aria-label="结束日期" className={`${SL_INPUT_CLASS} mt-1`} type="date" value={runForm.endDate} onChange={(event) => setRunForm({ ...runForm, endDate: event.target.value })} required />
          </label>
          <label className="text-sm">
            初始资金
            <input aria-label="初始资金" className={`${SL_INPUT_CLASS} mt-1`} type="number" min="1" value={runForm.initialCash} onChange={(event) => setRunForm({ ...runForm, initialCash: event.target.value })} required />
          </label>
          <label className="text-sm">
            标的筛选
            <input aria-label="标的筛选" className={`${SL_INPUT_CLASS} mt-1`} value={runForm.symbols} onChange={(event) => setRunForm({ ...runForm, symbols: event.target.value })} placeholder="逗号分隔，留空使用全部同步标的" />
          </label>
        </div>
        <label className="mt-3 flex items-center gap-2 text-sm text-secondary-text">
          <input aria-label="后台执行批次" type="checkbox" checked={runBatchAsync} onChange={(event) => setRunBatchAsync(event.target.checked)} />
          后台执行并显示进度
        </label>
        <Button type="primary" htmlType="submit" className="mt-4" loading={actionLoading === 'batch'} disabled={parsePositiveNumbers(batchPositions).length === 0}>运行参数批次</Button>
      </form>

      <div className={`${SL_PANEL_CLASS}`}>
        <h2 className="text-base font-semibold text-foreground">批次历史</h2>
        <Table
          rowKey="id"
          size="small"
          className="mt-3"
          columns={batchColumns}
          dataSource={batches}
          pagination={false}
          loading={loading}
          onRow={(batch) => ({ onClick: () => void selectBatch(batch.id), style: { cursor: 'pointer' } })}
          locale={{ emptyText: '暂无批次记录' }}
        />
        {selectedBatch ? (
          <div className="mt-3 border-t border-border/60 pt-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm">批次 #{selectedBatch.id}: {statusTag(selectedBatch.status)}</span>
              <Button size="small" disabled={actionLoading !== null || !selectedBatch.failed_tasks} onClick={() => void runAction('retry', () => strategyLabApi.retryBatch(selectedBatch.id))}>重试失败项</Button>
              <Button size="small" icon={<RotateCcw className="h-3.5 w-3.5" />} disabled={actionLoading !== null} onClick={() => void runAction('resume', () => strategyLabApi.resumeBatch(selectedBatch.id))}>恢复未完成项</Button>
              <Button size="small" danger icon={<Trash2 className="h-3.5 w-3.5" />} disabled={actionLoading !== null} onClick={() => void runAction('delete-batch', async () => { await strategyLabApi.deleteBatch(selectedBatch.id); setSelectedBatch(null); })}>删除</Button>
            </div>
            <div className="mt-2 text-xs text-secondary-text">
              {selectedBatch.completed_tasks}/{selectedBatch.total_tasks} · {selectedBatch.items?.map((item) => `#${item.id} ${item.status}`).join(' · ')}
            </div>
          </div>
        ) : null}
        {error ? <ApiErrorAlert error={error} className="mt-4" /> : null}
      </div>
    </div>
  );
};
