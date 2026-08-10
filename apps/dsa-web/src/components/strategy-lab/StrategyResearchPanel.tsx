import { useCallback, useEffect, useState } from 'react';
import { Button, Descriptions, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { RefreshCw } from 'lucide-react';
import { ApiErrorAlert } from '../common';
import { EChart } from '../common/EChart';
import type { EChartOption } from '../common/EChart';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import {
  strategyLabApi,
  type StrategyLabRunItem,
  type StrategyLabStrategyItem,
  type StrategyLabTradeItem,
} from '../../api/strategyLab';
import { SL_INPUT_CLASS, SL_PANEL_CLASS, formatNumber, formatPct, parseSymbols } from './utils';

const today = new Date().toISOString().slice(0, 10);

export const StrategyResearchPanel: React.FC = () => {
  const [strategies, setStrategies] = useState<StrategyLabStrategyItem[]>([]);
  const [runs, setRuns] = useState<StrategyLabRunItem[]>([]);
  const [selectedRun, setSelectedRun] = useState<StrategyLabRunItem | null>(null);
  const [trades, setTrades] = useState<StrategyLabTradeItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [strategyId, setStrategyId] = useState('double-low');
  const [runForm, setRunForm] = useState({ startDate: '2024-01-02', endDate: today, initialCash: '100000', maxPositions: '2', symbols: '', benchmark: '' });

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [strategyItems, runItems] = await Promise.all([strategyLabApi.listStrategies(), strategyLabApi.listRuns()]);
      setStrategies(strategyItems);
      setRuns(runItems);
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

  const selectRun = async (runId: number) => {
    await runAction(`run-${runId}`, async () => {
      const [run, runTrades] = await Promise.all([strategyLabApi.getRun(runId), strategyLabApi.listRunTrades(runId)]);
      setSelectedRun(run);
      setTrades(runTrades);
    });
  };

  const equityOption: EChartOption = selectedRun && selectedRun.equity_curve?.length
    ? {
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis' },
        grid: { left: 48, right: 16, top: 16, bottom: 32 },
        xAxis: { type: 'category', data: selectedRun.equity_curve.map((point) => point.trade_date) },
        yAxis: { type: 'value', scale: true },
        series: [{
          name: '净值',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: selectedRun.equity_curve.map((point) => Number(point.equity.toFixed(2))),
          lineStyle: { width: 2, color: '#22d3ee' },
          areaStyle: { color: 'rgba(34, 211, 238, 0.12)' },
        }],
      }
    : {};

  const tradeColumns: ColumnsType<StrategyLabTradeItem> = [
    { title: '日期', dataIndex: 'trade_date', width: 110 },
    { title: '方向', dataIndex: 'side', width: 70, render: (side: string) => <Tag color={side === 'buy' ? 'success' : 'error'}>{side === 'buy' ? '买入' : '卖出'}</Tag> },
    { title: '标的', dataIndex: 'symbol', width: 100 },
    { title: '数量', dataIndex: 'quantity', width: 100, align: 'right' },
    { title: '价格', dataIndex: 'price', width: 100, align: 'right', render: (value: number) => formatNumber(value) },
    { title: '金额', dataIndex: 'amount', width: 110, align: 'right', render: (value: number) => formatNumber(value) },
    { title: '依据', dataIndex: 'reason', ellipsis: true },
  ];

  const metrics = selectedRun?.metrics;

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <form
        className={SL_PANEL_CLASS}
        onSubmit={(event) => {
          event.preventDefault();
          void runAction('run', async () => {
            const run = await strategyLabApi.createRun({
              strategy_id: strategyId,
              market: 'cn',
              instrument_type: 'convertible_bond',
              start_date: runForm.startDate,
              end_date: runForm.endDate,
              initial_cash: Number(runForm.initialCash),
              benchmark_symbol: runForm.benchmark || undefined,
              symbols: parseSymbols(runForm.symbols),
              parameters: { max_positions: Number(runForm.maxPositions) },
            });
            setSelectedRun(run);
            setTrades(await strategyLabApi.listRunTrades(run.id));
          });
        }}
      >
        <h2 className="text-base font-semibold text-foreground">策略回测</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm">
            策略
            <select aria-label="策略" className={`${SL_INPUT_CLASS} mt-1`} value={strategyId} onChange={(event) => setStrategyId(event.target.value)}>
              {strategies.map((strategy) => <option key={strategy.strategy_id} value={strategy.strategy_id}>{strategy.name}</option>)}
            </select>
          </label>
          <label className="text-sm">
            最大持仓
            <input aria-label="最大持仓" className={`${SL_INPUT_CLASS} mt-1`} type="number" min="1" value={runForm.maxPositions} onChange={(event) => setRunForm({ ...runForm, maxPositions: event.target.value })} />
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
            基准标的
            <input aria-label="基准标的" className={`${SL_INPUT_CLASS} mt-1`} value={runForm.benchmark} onChange={(event) => setRunForm({ ...runForm, benchmark: event.target.value })} placeholder="可选，例如 113001" />
          </label>
        </div>
        <label className="mt-3 block text-sm">
          标的筛选
          <input aria-label="标的筛选" className={`${SL_INPUT_CLASS} mt-1`} value={runForm.symbols} onChange={(event) => setRunForm({ ...runForm, symbols: event.target.value })} placeholder="逗号分隔，留空使用全部同步标的" />
        </label>
        <Button type="primary" htmlType="submit" className="mt-4" loading={actionLoading === 'run'}>运行回测</Button>
      </form>

      <div className="grid gap-4">
        <div className={SL_PANEL_CLASS}>
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-foreground">运行记录</h2>
            <Button size="small" type="text" icon={<RefreshCw className="h-3.5 w-3.5" />} loading={loading} onClick={() => void refresh()}>刷新</Button>
          </div>
          <div className="mt-3 max-h-52 overflow-auto">
            {runs.length ? runs.map((run) => (
              <button key={run.id} type="button" className="flex w-full items-center justify-between border-t border-border/60 py-2 text-left text-sm hover:text-cyan" onClick={() => void selectRun(run.id)}>
                <span>#{run.id} {run.strategy_name}</span>
                <span>{formatPct(run.metrics?.total_return_pct)} {run.status}</span>
              </button>
            )) : <div className="text-sm text-secondary-text">暂无运行记录</div>}
          </div>
        </div>
        {error ? <ApiErrorAlert error={error} className="mt-4" /> : null}
      </div>

      <div className="xl:col-span-2">
        {selectedRun ? (
          <div className="grid gap-4 lg:grid-cols-2">
            <div className={`${SL_PANEL_CLASS} lg:col-span-2`}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-base font-semibold text-foreground">运行 #{selectedRun.id} {selectedRun.engine_name}</h2>
                <span className="text-lg font-semibold text-cyan">{formatPct(metrics?.total_return_pct)}</span>
              </div>
              <Descriptions size="small" column={{ xs: 2, md: 4 }} className="mt-3">
                <Descriptions.Item label="年化收益">{formatPct(metrics?.annualized_return_pct)}</Descriptions.Item>
                <Descriptions.Item label="最大回撤">{formatPct(metrics?.max_drawdown_pct)}</Descriptions.Item>
                <Descriptions.Item label="夏普比率">{formatNumber(metrics?.sharpe_ratio)}</Descriptions.Item>
                <Descriptions.Item label="交易次数">{metrics?.trade_count ?? 0}</Descriptions.Item>
              </Descriptions>
            </div>
            <div className={SL_PANEL_CLASS}>
              <h3 className="mb-3 text-sm font-semibold text-foreground">净值曲线</h3>
              <EChart option={equityOption} height={280} aria-label="净值曲线" />
            </div>
            <div className={SL_PANEL_CLASS}>
              <h3 className="mb-3 text-sm font-semibold text-foreground">成交明细</h3>
              <Table
                rowKey="id"
                size="small"
                columns={tradeColumns}
                dataSource={trades}
                pagination={false}
                locale={{ emptyText: '无成交记录' }}
              />
            </div>
          </div>
        ) : (
          <div className={`${SL_PANEL_CLASS} text-sm text-secondary-text`}>选择一条运行记录或运行一次回测查看详情</div>
        )}
      </div>
    </div>
  );
};
