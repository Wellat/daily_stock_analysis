import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiErrorAlert } from '../common';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { portfolioApi } from '../../api/portfolio';
import type { PortfolioAccountItem } from '../../types/portfolio';
import {
  strategyLabApi,
  type StrategyLabRunItem,
  type StrategyLabSignalItem,
} from '../../api/strategyLab';
import { SL_INPUT_CLASS, SL_PANEL_CLASS } from './utils';

const today = new Date().toISOString().slice(0, 10);

const actionTag = (action: string) => {
  const map: Record<string, { color: string; label: string }> = {
    buy: { color: 'success', label: '买入' },
    sell: { color: 'error', label: '卖出' },
    hold: { color: 'default', label: '持有' },
  };
  const entry = map[action.toLowerCase()] ?? { color: 'default', label: action };
  return <Tag color={entry.color}>{entry.label}</Tag>;
};

export const LiveSignalPanel: React.FC = () => {
  const [runs, setRuns] = useState<StrategyLabRunItem[]>([]);
  const [signals, setSignals] = useState<StrategyLabSignalItem[]>([]);
  const [accounts, setAccounts] = useState<PortfolioAccountItem[]>([]);
  const [selectedSignalId, setSelectedSignalId] = useState<number | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [signalForm, setSignalForm] = useState({ action: 'buy', confidence: '0.8', reason: '' });
  const [confirmForm, setConfirmForm] = useState({ accountId: '', tradeDate: today, quantity: '', price: '', side: 'buy', fee: '0', tax: '0' });

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [runItems, signalItems, accountItems] = await Promise.all([
        strategyLabApi.listRuns(),
        strategyLabApi.listSignals(),
        portfolioApi.getAccounts(false),
      ]);
      setRuns(runItems);
      setSignals(signalItems);
      setAccounts(accountItems.accounts);
      setSelectedSignalId((previous) => previous ?? signalItems[0]?.id ?? null);
      setSelectedRunId((previous) => previous ?? runItems[0]?.id ?? null);
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

  const cnAccounts = useMemo(() => accounts.filter((account) => account.market === 'cn'), [accounts]);
  const selectedSignal = signals.find((signal) => signal.id === selectedSignalId) ?? null;

  const columns: ColumnsType<StrategyLabSignalItem> = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '标的', dataIndex: 'symbol', width: 100 },
    { title: '建议', dataIndex: 'suggested_action', width: 80, render: actionTag },
    { title: '置信度', dataIndex: 'confidence', width: 90, render: (value: number | null) => value == null ? '--' : value.toFixed(2) },
    { title: '状态', dataIndex: 'status', width: 100, render: (status: string) => <Tag color={status === 'confirmed' ? 'success' : 'processing'}>{status}</Tag> },
    { title: '依据', dataIndex: 'reason', ellipsis: true },
    { title: '创建时间', dataIndex: 'created_at', width: 160, render: (value: string | null) => value ? value.replace('T', ' ').slice(0, 19) : '--' },
  ];

  return (
    <div className="grid gap-4">
      <div className={`${SL_PANEL_CLASS}`}>
        <h2 className="text-base font-semibold text-foreground">生成信号</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-sm">
            运行
            <select aria-label="信号运行" className={`${SL_INPUT_CLASS} mt-1`} value={selectedRunId ?? ''} onChange={(event) => setSelectedRunId(Number(event.target.value))}>
              {runs.map((run) => <option key={run.id} value={run.id}>#{run.id} {run.strategy_name}</option>)}
            </select>
          </label>
          <label className="text-sm">
            建议
            <select aria-label="建议动作" className={`${SL_INPUT_CLASS} mt-1`} value={signalForm.action} onChange={(event) => setSignalForm({ ...signalForm, action: event.target.value })}>
              <option value="buy">买入</option>
              <option value="sell">卖出</option>
              <option value="hold">持有</option>
            </select>
          </label>
          <label className="text-sm">
            置信度
            <input aria-label="信号置信度" className={`${SL_INPUT_CLASS} mt-1`} type="number" min="0" max="1" step="0.01" value={signalForm.confidence} onChange={(event) => setSignalForm({ ...signalForm, confidence: event.target.value })} />
          </label>
          <label className="text-sm">
            依据
            <input aria-label="信号依据" className={`${SL_INPUT_CLASS} mt-1`} value={signalForm.reason} onChange={(event) => setSignalForm({ ...signalForm, reason: event.target.value })} />
          </label>
        </div>
        <Button
          type="primary"
          className="mt-4"
          loading={actionLoading === 'signal'}
          disabled={selectedRunId == null}
          onClick={() => selectedRunId != null && void runAction('signal', () => strategyLabApi.createSignal({ run_id: selectedRunId, suggested_action: signalForm.action, confidence: Number(signalForm.confidence), reason: signalForm.reason || undefined }))}
        >
          从选中运行生成信号
        </Button>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className={SL_PANEL_CLASS}>
          <h2 className="text-base font-semibold text-foreground">信号列表</h2>
          <Table
            rowKey="id"
            size="small"
            className="mt-3"
            columns={columns}
            dataSource={signals}
            pagination={false}
            loading={loading}
            rowClassName={(signal) => (signal.id === selectedSignalId ? 'bg-card/80' : '')}
            onRow={(signal) => ({ onClick: () => setSelectedSignalId(signal.id), style: { cursor: 'pointer' } })}
            locale={{ emptyText: '暂无信号' }}
          />
        </div>

        <div className={SL_PANEL_CLASS}>
          <h2 className="text-base font-semibold text-foreground">确认写入 Portfolio</h2>
          {selectedSignal ? (
            <div className="mt-2 text-sm text-secondary-text">信号 #{selectedSignal.id} {selectedSignal.symbol} {actionTag(selectedSignal.suggested_action)} · {selectedSignal.status}</div>
          ) : <div className="mt-2 text-sm text-secondary-text">先选择一条信号</div>}
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <label className="text-sm">
              Portfolio 账户
              <select aria-label="Portfolio 账户" className={`${SL_INPUT_CLASS} mt-1`} value={confirmForm.accountId} onChange={(event) => setConfirmForm({ ...confirmForm, accountId: event.target.value })}>
                <option value="">选择 A 股账户</option>
                {cnAccounts.map((account) => <option key={account.id} value={account.id}>{account.name} #{account.id}</option>)}
              </select>
            </label>
            <label className="text-sm">
              成交日期
              <input aria-label="成交日期" className={`${SL_INPUT_CLASS} mt-1`} type="date" value={confirmForm.tradeDate} onChange={(event) => setConfirmForm({ ...confirmForm, tradeDate: event.target.value })} />
            </label>
            <label className="text-sm">
              数量
              <input aria-label="成交数量" className={`${SL_INPUT_CLASS} mt-1`} type="number" min="0.0001" value={confirmForm.quantity} onChange={(event) => setConfirmForm({ ...confirmForm, quantity: event.target.value })} />
            </label>
            <label className="text-sm">
              价格
              <input aria-label="成交价格" className={`${SL_INPUT_CLASS} mt-1`} type="number" min="0.0001" value={confirmForm.price} onChange={(event) => setConfirmForm({ ...confirmForm, price: event.target.value })} />
            </label>
          </div>
          <Button
            className="mt-4"
            loading={actionLoading === 'confirm'}
            disabled={!selectedSignal || !confirmForm.accountId || !Number(confirmForm.quantity) || !Number(confirmForm.price)}
            onClick={() => selectedSignal && void runAction('confirm', () => strategyLabApi.confirmSignal(selectedSignal.id, {
              portfolio_account_id: Number(confirmForm.accountId),
              trade_date: confirmForm.tradeDate,
              quantity: Number(confirmForm.quantity),
              price: Number(confirmForm.price),
              side: confirmForm.side as 'buy' | 'sell',
              fee: Number(confirmForm.fee),
              tax: Number(confirmForm.tax),
            }))}
          >
            确认写入 Portfolio
          </Button>
          {error ? <ApiErrorAlert error={error} className="mt-4" /> : null}
        </div>
      </div>
    </div>
  );
};
