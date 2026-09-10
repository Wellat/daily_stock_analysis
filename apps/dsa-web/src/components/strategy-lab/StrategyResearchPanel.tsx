import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Descriptions, Segmented, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Download, RefreshCw } from 'lucide-react';
import { ApiErrorAlert } from '../common';
import { EChart } from '../common/EChart';
import type { EChartOption } from '../common/EChart';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { liveStrategyApi } from '../../api/liveStrategy';
import {
  strategyLabApi,
  type StrategyLabRunItem,
  type StrategyLabStrategyItem,
  type StrategyLabTradeItem,
} from '../../api/strategyLab';
import { SL_INPUT_CLASS, SL_PANEL_CLASS, formatNumber, formatPct, parseSymbols } from './utils';

const today = new Date().toISOString().slice(0, 10);

// 导入实盘配置时展示的参数标签；max_positions 由“最大持仓”输入框承载，不在此展示。
const LIVE_PARAM_LABELS: Record<string, string> = {
  per_position_cash: '单债目标资金',
  lot_size: '最小交易单位',
  max_abs_premium: '最大溢价率',
  exclude_event_blocked: '排除风险事件',
};

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
  const [dateMode, setDateMode] = useState<'range' | 'single'>('range');
  // 导入实盘配置后的策略参数与来源标识；null 表示未导入，提交时沿用策略默认参数。
  const [liveParams, setLiveParams] = useState<Record<string, unknown> | null>(null);
  const [liveImportedFrom, setLiveImportedFrom] = useState<string | null>(null);

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

  // 导入实盘配置复用实盘的策略/自选池/参数；回测资金按 单债目标资金 × 最大持仓 映射，
  // 使每仓资金语义与实盘 ExecutionPlanner 的每债目标资金一致。
  const importLiveConfig = async () => {
    await runAction('import', async () => {
      const config = await liveStrategyApi.getConfig();
      const params = config.parameters ?? {};
      const maxPositions = Math.max(1, Number(params.max_positions) || 2);
      const perPositionCash = Math.max(1, Number(params.per_position_cash) || 10000);
      setStrategyId(config.strategyId);
      setRunForm((prev) => ({
        ...prev,
        maxPositions: String(maxPositions),
        initialCash: String(perPositionCash * maxPositions),
        symbols: (config.symbols ?? []).join(','),
      }));
      setLiveParams(params);
      setLiveImportedFrom(`${config.strategyId} · ${config.qmtAccount}`);
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
    { title: '标的', dataIndex: 'symbol', width: 170, render: (_, record) => (record.symbol_name ? `${record.symbol_name}（${record.symbol}）` : record.symbol) },
    { title: '数量', dataIndex: 'quantity', width: 100, align: 'right' },
    { title: '价格', dataIndex: 'price', width: 100, align: 'right', render: (value: number) => formatNumber(value) },
    { title: '金额', dataIndex: 'amount', width: 110, align: 'right', render: (value: number) => formatNumber(value) },
    { title: '依据', dataIndex: 'reason', ellipsis: true },
  ];

  const metrics = selectedRun?.metrics;
  const isSingleDayRun = Boolean(selectedRun?.start_date && selectedRun.start_date === selectedRun.end_date);
  // 引擎名含 fixture 说明请求区间没有已同步行情，结果退化为内置样本数据。
  const isFixtureEngine = (selectedRun?.engine_name ?? '').toLowerCase().includes('fixture');
  const buyTrades = trades.filter((trade) => trade.side === 'buy');
  const buyAmount = buyTrades.reduce((sum, trade) => sum + (trade.amount || 0), 0);
  const hasEquityCurve = Boolean(selectedRun?.equity_curve && selectedRun.equity_curve.length > 1);

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
              end_date: dateMode === 'single' ? runForm.startDate : runForm.endDate,
              initial_cash: Number(runForm.initialCash),
              benchmark_symbol: runForm.benchmark || undefined,
              symbols: parseSymbols(runForm.symbols),
              // 最大持仓以表单输入为准，其余参数沿用导入的实盘配置；未导入时为策略默认值。
              parameters: { ...(liveParams ?? {}), max_positions: Number(runForm.maxPositions) },
            });
            setSelectedRun(run);
            setTrades(await strategyLabApi.listRunTrades(run.id));
          });
        }}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-foreground">策略回测</h2>
          <Button size="small" type="text" icon={<Download className="h-3.5 w-3.5" />} loading={actionLoading === 'import'} onClick={() => void importLiveConfig()}>导入实盘配置</Button>
        </div>
        <div className="mt-3">
          <Segmented
            value={dateMode}
            onChange={(value) => setDateMode(value as 'range' | 'single')}
            options={[
              { label: '区间回测', value: 'range' },
              { label: '单日运行', value: 'single' },
            ]}
          />
          {dateMode === 'single' ? (
            <p className="mt-2 text-xs text-secondary-text">单日运行基于该日已同步行情复算策略选择（开始=结束），用于核对策略当日目标组合。</p>
          ) : null}
        </div>
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
          {dateMode === 'single' ? (
            <label className="text-sm">
              日期
              <input aria-label="日期" className={`${SL_INPUT_CLASS} mt-1`} type="date" value={runForm.startDate} onChange={(event) => setRunForm({ ...runForm, startDate: event.target.value })} required />
            </label>
          ) : (
            <>
              <label className="text-sm">
                开始日期
                <input aria-label="开始日期" className={`${SL_INPUT_CLASS} mt-1`} type="date" value={runForm.startDate} onChange={(event) => setRunForm({ ...runForm, startDate: event.target.value })} required />
              </label>
              <label className="text-sm">
                结束日期
                <input aria-label="结束日期" className={`${SL_INPUT_CLASS} mt-1`} type="date" value={runForm.endDate} onChange={(event) => setRunForm({ ...runForm, endDate: event.target.value })} required />
              </label>
            </>
          )}
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
        {liveParams ? (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-secondary-text">
            <Tag color="cyan" className="m-0">实盘配置 {liveImportedFrom}</Tag>
            {Object.entries(LIVE_PARAM_LABELS).filter(([key]) => liveParams[key] !== undefined && liveParams[key] !== null).map(([key, label]) => (
              <Tag key={key} className="m-0">{label} {typeof liveParams[key] === 'boolean' ? (liveParams[key] ? '开' : '关') : String(liveParams[key])}</Tag>
            ))}
            <span>初始资金已按 单债目标资金 × 最大持仓 映射</span>
          </div>
        ) : null}
        <Button type="primary" htmlType="submit" className="mt-4" loading={actionLoading === 'run'}>{dateMode === 'single' ? '运行单日回测' : '运行回测'}</Button>
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
                <h2 className="text-base font-semibold text-foreground">
                  运行 #{selectedRun.id} {selectedRun.engine_name}
                  {isSingleDayRun ? <Tag className="ml-2">单日</Tag> : null}
                </h2>
                <span className="text-lg font-semibold text-cyan">{formatPct(metrics?.total_return_pct)}</span>
              </div>
              {isFixtureEngine ? (
                <Alert
                  className="mt-3"
                  type="warning"
                  showIcon
                  message="请求区间没有已同步的转债行情"
                  description="本次运行退化为内置样本数据集，结果不代表真实行情；请先在数据同步页补齐该日期区间的行情后再运行。"
                />
              ) : null}
              <Descriptions size="small" column={{ xs: 2, md: 4 }} className="mt-3">
                <Descriptions.Item label="运行区间">{isSingleDayRun ? `${selectedRun.start_date}（单日）` : `${selectedRun.start_date} ~ ${selectedRun.end_date}`}</Descriptions.Item>
                {isSingleDayRun ? <Descriptions.Item label="当日买入">{buyTrades.length} 只 · {formatNumber(buyAmount)} 元</Descriptions.Item> : null}
                <Descriptions.Item label="年化收益">{formatPct(metrics?.annualized_return_pct)}</Descriptions.Item>
                <Descriptions.Item label="最大回撤">{formatPct(metrics?.max_drawdown_pct)}</Descriptions.Item>
                <Descriptions.Item label="夏普比率">{formatNumber(metrics?.sharpe_ratio)}</Descriptions.Item>
                <Descriptions.Item label="交易次数">{metrics?.trade_count ?? 0}</Descriptions.Item>
              </Descriptions>
            </div>
            <div className={SL_PANEL_CLASS}>
              <h3 className="mb-3 text-sm font-semibold text-foreground">净值曲线</h3>
              {hasEquityCurve ? <EChart option={equityOption} height={280} aria-label="净值曲线" /> : <div className="text-sm text-secondary-text">区间数据不足，无净值曲线</div>}
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
