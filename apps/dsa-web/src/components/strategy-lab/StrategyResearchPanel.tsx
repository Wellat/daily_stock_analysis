import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Input, Modal, Segmented, Select, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { FileDown, RefreshCcw, RefreshCw, Save, Trash2 } from 'lucide-react';
import { ApiErrorAlert } from '../common';
import {
  strategyLabApi,
  type StrategyLabConfigItem,
  type StrategyLabRunItem,
  type StrategyLabStrategyItem,
  type StrategyLabTradeItem,
} from '../../api/strategyLab';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { SL_INPUT_CLASS, SL_PANEL_CLASS, formatNumber, formatPct, parseSymbols } from './utils';
import { BacktestSummaryTable } from './backtest/BacktestSummaryTable';
import { BacktestTrendChart } from './backtest/BacktestTrendChart';
import { ReturnDistribution } from './backtest/ReturnDistribution';
import { HoldingsTable } from './backtest/HoldingsTable';
import { RotationParamsForm } from './backtest/RotationParamsForm';
import {
  DEFAULT_ROTATION_FORM,
  applyRotationParameters,
  buildRotationParameters,
  type RotationFormState,
} from './backtest/rotationForm';

const today = new Date().toISOString().slice(0, 10);

type BenchmarkMode = 'equal_weight' | 'csi300' | 'custom';

export const StrategyResearchPanel: React.FC = () => {
  const [strategies, setStrategies] = useState<StrategyLabStrategyItem[]>([]);
  const [runs, setRuns] = useState<StrategyLabRunItem[]>([]);
  const [configs, setConfigs] = useState<StrategyLabConfigItem[]>([]);
  const [selectedRun, setSelectedRun] = useState<StrategyLabRunItem | null>(null);
  const [trades, setTrades] = useState<StrategyLabTradeItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const [strategyId, setStrategyId] = useState('rotation');
  const [runForm, setRunForm] = useState({ startDate: '2024-01-02', endDate: today, initialCash: '100000', maxPositions: '2', symbols: '' });
  const [dateMode, setDateMode] = useState<'range' | 'single'>('range');
  const [benchmarkMode, setBenchmarkMode] = useState<BenchmarkMode>('equal_weight');
  const [benchmarkCustom, setBenchmarkCustom] = useState('');
  const [rotationForm, setRotationForm] = useState<RotationFormState>(DEFAULT_ROTATION_FORM);
  const [saveModalOpen, setSaveModalOpen] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [saveDescription, setSaveDescription] = useState('');

  const selectedStrategy = useMemo(
    () => strategies.find((item) => item.strategy_id === strategyId),
    [strategies, strategyId],
  );
  const isRotation = strategyId === 'rotation';

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [strategyItems, runItems, configItems] = await Promise.all([
        strategyLabApi.listStrategies(),
        strategyLabApi.listRuns(),
        strategyLabApi.listConfigs().catch(() => []),
      ]);
      setStrategies(strategyItems);
      setRuns(runItems);
      setConfigs(configItems);
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

  const benchmarkSymbol = (): string | undefined => {
    if (benchmarkMode === 'csi300') return '000300';
    if (benchmarkMode === 'custom') return benchmarkCustom.trim() || undefined;
    return undefined; // 标的池等权（后端缺省）
  };

  const buildParameters = (): Record<string, unknown> => {
    if (isRotation) return buildRotationParameters(rotationForm);
    return { max_positions: Number(runForm.maxPositions) };
  };

  const loadConfig = (configId: number) => {
    const config = configs.find((item) => item.id === configId);
    if (!config) return;
    setStrategyId(config.strategy_id);
    setRunForm((prev) => ({ ...prev, symbols: (config.symbols ?? []).join(',') }));
    if (config.strategy_id === 'rotation') {
      setRotationForm(applyRotationParameters(config.parameters));
    } else if (config.parameters?.max_positions != null) {
      setRunForm((prev) => ({ ...prev, maxPositions: String(config.parameters.max_positions) }));
    }
  };

  const saveCurrentConfig = async () => {
    if (!saveName.trim()) return;
    await runAction('save-config', async () => {
      await strategyLabApi.createConfig({
        name: saveName.trim(),
        description: saveDescription.trim() || undefined,
        strategy_id: strategyId,
        market: 'cn',
        instrument_type: 'convertible_bond',
        parameters: buildParameters(),
        symbols: parseSymbols(runForm.symbols),
      });
      setSaveModalOpen(false);
      setSaveName('');
      setSaveDescription('');
      await refresh();
    });
  };

  const deleteConfig = async (configId: number) => {
    await runAction(`delete-config-${configId}`, async () => {
      await strategyLabApi.deleteConfig(configId);
      await refresh();
    });
  };

  const downloadReport = (format: 'md' | 'holdings-csv' | 'trades-csv') => {
    if (!selectedRun) return;
    window.open(strategyLabApi.getRunReportUrl(selectedRun.id, format), '_blank');
  };

  const tradeColumns: ColumnsType<StrategyLabTradeItem> = [
    { title: '日期', dataIndex: 'trade_date', width: 110 },
    { title: '方向', dataIndex: 'side', width: 70, render: (side: string) => <Tag color={side === 'buy' ? 'success' : 'error'}>{side === 'buy' ? '买入' : '卖出'}</Tag> },
    { title: '标的', dataIndex: 'symbol', width: 170, render: (_, record) => (record.symbol_name ? `${record.symbol_name}（${record.symbol}）` : record.symbol) },
    { title: '数量', dataIndex: 'quantity', width: 100, align: 'right' },
    { title: '价格', dataIndex: 'price', width: 100, align: 'right', render: (value: number) => formatNumber(value) },
    { title: '金额', dataIndex: 'amount', width: 110, align: 'right', render: (value: number) => formatNumber(value) },
    { title: '依据', dataIndex: 'reason', ellipsis: true },
  ];

  const PRESET_LABELS: Record<string, string> = {
    double_low: '双低',
    low_premium: '低溢价',
    weighted_double_low: '加权双低',
    triple_low: '三低',
  };

  const paramsDigest = (run: StrategyLabRunItem): string => {
    const parameters = run.parameters ?? {};
    const positions = parameters.max_positions;
    if (run.strategy_id === 'rotation') {
      const preset = parameters.score_preset ? (PRESET_LABELS[String(parameters.score_preset)] ?? String(parameters.score_preset)) : '自定义因子';
      const unit = parameters.rebalance_unit === 'month' ? '月' : parameters.rebalance_unit === 'week' ? '周' : '日';
      const interval = parameters.rebalance_interval ?? 1;
      return `${preset} · ${positions ?? '?'}只 · 每${interval}${unit}`;
    }
    return positions ? `${positions}只` : '--';
  };

  const fmtTime = (value?: string | null): string => (value ? value.replace('T', ' ').slice(5, 16) : '--');

  const pctSpan = (value?: number | null) => (
    <span className={value == null ? 'text-secondary-text' : value > 0 ? 'text-red-500' : value < 0 ? 'text-green-600' : ''}>
      {formatPct(value)}
    </span>
  );

  const runColumns: ColumnsType<StrategyLabRunItem> = [
    { title: '运行', dataIndex: 'id', width: 120, render: (id: number, record) => (<span className="whitespace-nowrap">#{id} <span className="text-xs text-secondary-text">{fmtTime(record.created_at)}</span></span>) },
    { title: '策略', dataIndex: 'strategy_name', width: 130, ellipsis: true, render: (_, record) => (<span className="whitespace-nowrap">{record.strategy_name}{record.strategy_id === 'rotation' ? <Tag className="ml-1" color="cyan">轮动</Tag> : null}</span>) },
    { title: '回测区间', key: 'range', width: 175, render: (_, record) => (<span className="whitespace-nowrap text-xs">{record.start_date} ~ {record.end_date}</span>) },
    { title: '总收益率', key: 'total', width: 92, align: 'right', render: (_, record) => pctSpan(record.metrics?.total_return_pct) },
    { title: '基准', dataIndex: 'benchmark_return_pct', width: 92, align: 'right', render: (value: number | null) => pctSpan(value) },
    { title: '最大回撤', key: 'mdd', width: 92, align: 'right', render: (_, record) => pctSpan(record.metrics?.max_drawdown_pct) },
    { title: '夏普', key: 'sharpe', width: 78, align: 'right', render: (_, record) => formatNumber(record.metrics?.sharpe_ratio) },
    { title: '参数', key: 'params', width: 165, ellipsis: true, render: (_, record) => paramsDigest(record) },
    { title: '状态', dataIndex: 'status', width: 92, render: (status: string) => (
      <Tag color={status === 'completed' ? 'success' : status === 'failed' ? 'error' : 'processing'}>{status}</Tag>
    ) },
  ];

  const metrics = selectedRun?.metrics;
  const isSingleDayRun = Boolean(selectedRun?.start_date && selectedRun.start_date === selectedRun.end_date);
  const isFixtureEngine = (selectedRun?.engine_name ?? '').toLowerCase().includes('fixture');
  const buyTrades = trades.filter((trade) => trade.side === 'buy');
  const buyAmount = buyTrades.reduce((sum, trade) => sum + (trade.amount || 0), 0);

  return (
    <div className="grid gap-4">
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
              benchmark_symbol: benchmarkSymbol(),
              symbols: parseSymbols(runForm.symbols),
              parameters: buildParameters(),
            });
            setSelectedRun(run);
            setTrades(await strategyLabApi.listRunTrades(run.id));
            await refresh();
          });
        }}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold text-foreground">策略回测</h2>
          <div className="flex items-center gap-2">
            <Select
              aria-label="加载已保存配置"
              size="small"
              className="min-w-40"
              placeholder="加载已保存配置"
              value={undefined}
              options={configs.map((config) => ({ label: config.name, value: config.id }))}
              onSelect={(value) => { if (value != null) loadConfig(value); }}
              notFoundContent="暂无保存的配置"
            />
            {configs.length ? (
              <Button
                size="small"
                type="text"
                aria-label="删除已保存配置"
                icon={<Trash2 className="h-3.5 w-3.5" />}
                onClick={() => {
                  const latest = configs[0];
                  if (latest) void deleteConfig(latest.id);
                }}
                title={`删除配置：${configs[0]?.name ?? ''}`}
              />
            ) : null}
            <Button size="small" icon={<Save className="h-3.5 w-3.5" />} onClick={() => setSaveModalOpen(true)}>保存配置</Button>
          </div>
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

        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-sm">
            策略
            <select aria-label="策略" className={`${SL_INPUT_CLASS} mt-1`} value={strategyId} onChange={(event) => setStrategyId(event.target.value)}>
              {strategies.map((strategy) => <option key={strategy.strategy_id} value={strategy.strategy_id}>{strategy.name}</option>)}
            </select>
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
            基准指标
            <Select
              aria-label="基准指标"
              className="mt-1 w-full"
              value={benchmarkMode}
              options={[
                { label: '标的池等权', value: 'equal_weight' },
                { label: '沪深300', value: 'csi300' },
                { label: '指定转债', value: 'custom' },
              ]}
              onChange={(mode) => setBenchmarkMode(mode as BenchmarkMode)}
            />
          </label>
          {benchmarkMode === 'custom' ? (
            <label className="text-sm">
              基准转债代码
              <input aria-label="基准转债代码" className={`${SL_INPUT_CLASS} mt-1`} value={benchmarkCustom} onChange={(event) => setBenchmarkCustom(event.target.value)} placeholder="例如 113001" />
            </label>
          ) : null}
          {!isRotation ? (
            <label className="text-sm">
              最大持仓
              <input aria-label="最大持仓" className={`${SL_INPUT_CLASS} mt-1`} type="number" min="1" value={runForm.maxPositions} onChange={(event) => setRunForm({ ...runForm, maxPositions: event.target.value })} />
            </label>
          ) : null}
          <label className={isRotation ? 'text-sm lg:col-span-4' : 'text-sm sm:col-span-2'}>
            标的筛选
            <input aria-label="标的筛选" className={`${SL_INPUT_CLASS} mt-1`} value={runForm.symbols} onChange={(event) => setRunForm({ ...runForm, symbols: event.target.value })} placeholder="逗号分隔，留空使用全部同步标的" />
          </label>
        </div>

        {isRotation ? (
          <RotationParamsForm
            value={rotationForm}
            onChange={setRotationForm}
            factors={selectedStrategy?.factors ?? []}
            scorePresets={selectedStrategy?.score_presets ?? []}
          />
        ) : null}

        <Button type="primary" htmlType="submit" className="mt-4" loading={actionLoading === 'run'}>
          {dateMode === 'single' ? '运行单日回测' : '开始回测'}
        </Button>
      </form>

      <div className={SL_PANEL_CLASS}>
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-foreground">运行记录</h2>
          <Button size="small" type="text" icon={<RefreshCw className="h-3.5 w-3.5" />} loading={loading} onClick={() => void refresh()}>刷新</Button>
        </div>
        {error ? <ApiErrorAlert error={error} className="mt-3" /> : null}
        <Table
          rowKey="id"
          size="small"
          className="mt-3"
          columns={runColumns}
          dataSource={runs}
          pagination={{
            pageSize: 5,
            showSizeChanger: true,
            pageSizeOptions: [5, 10, 20],
            showTotal: (total) => `共 ${total} 条`,
            size: 'small',
          }}
          rowClassName={(record) => (record.id === selectedRun?.id ? 'bg-cyan/10' : '')}
          onRow={(record) => ({ onClick: () => void selectRun(record.id), style: { cursor: 'pointer' } })}
          locale={{ emptyText: '暂无运行记录' }}
        />
      </div>

      {selectedRun ? (
        <div className="grid gap-4">
          <div className={SL_PANEL_CLASS}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="flex items-baseline gap-2 text-base font-semibold text-foreground">
                <span>
                  运行 #{selectedRun.id} {selectedRun.engine_name}
                  {isSingleDayRun ? <Tag className="ml-2">单日</Tag> : null}
                </span>
              </h2>
              <div className="flex items-center gap-2">
                <Button size="small" icon={<FileDown className="h-3.5 w-3.5" />} onClick={() => downloadReport('md')}>导出报告</Button>
                <Button size="small" icon={<FileDown className="h-3.5 w-3.5" />} onClick={() => downloadReport('holdings-csv')}>持仓 CSV</Button>
                <Button size="small" icon={<FileDown className="h-3.5 w-3.5" />} onClick={() => downloadReport('trades-csv')}>成交 CSV</Button>
              </div>
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
            <div className="mt-3 text-xs text-secondary-text">
              {isSingleDayRun ? `${selectedRun.start_date}（单日）` : `${selectedRun.start_date} ~ ${selectedRun.end_date}`}
              {isSingleDayRun && buyTrades.length ? ` · 当日买入 ${buyTrades.length} 只 · ${formatNumber(buyAmount)} 元` : ''}
              {metrics?.diagnostics && typeof metrics.diagnostics === 'object' && 'benchmark_mode' in metrics.diagnostics
                ? ` · 基准：${String(metrics.diagnostics.benchmark_mode)}`
                : ''}
              {metrics?.win_rate_pct != null ? ` · 胜率 ${formatPct(metrics.win_rate_pct)}` : ''}
            </div>
            <div className="mt-3">
              <BacktestSummaryTable run={selectedRun} />
            </div>
          </div>

          <div className={SL_PANEL_CLASS}>
            <h3 className="mb-3 text-sm font-semibold text-foreground">回测走势</h3>
            <BacktestTrendChart run={selectedRun} />
          </div>

          <div className={SL_PANEL_CLASS}>
            <h3 className="mb-3 text-sm font-semibold text-foreground">回报分布</h3>
            <ReturnDistribution equityCurve={selectedRun.equity_curve} initialCash={selectedRun.initial_cash} />
          </div>

          <div className={SL_PANEL_CLASS}>
            <h3 className="mb-3 text-sm font-semibold text-foreground">持仓详情</h3>
            <HoldingsTable run={selectedRun} />
          </div>

          <div className={SL_PANEL_CLASS}>
            <h3 className="mb-3 text-sm font-semibold text-foreground">成交明细</h3>
            <Table
              rowKey="id"
              size="small"
              columns={tradeColumns}
              dataSource={trades}
              pagination={{ pageSize: 20, showSizeChanger: false, simple: true }}
              locale={{ emptyText: '无成交记录' }}
            />
          </div>
        </div>
      ) : (
        <div className={`${SL_PANEL_CLASS} flex items-center gap-2 text-sm text-secondary-text`}>
          <RefreshCcw className="h-3.5 w-3.5" />
          选择一条运行记录或运行一次回测查看详情
        </div>
      )}

      <Modal
        title="保存当前配置"
        open={saveModalOpen}
        onCancel={() => setSaveModalOpen(false)}
        onOk={() => void saveCurrentConfig()}
        okButtonProps={{ loading: actionLoading === 'save-config', disabled: !saveName.trim() }}
        okText="保存"
        cancelText="取消"
      >
        <div className="grid gap-3">
          <label className="text-sm">
            配置名称
            <Input aria-label="配置名称" className="mt-1" value={saveName} onChange={(event) => setSaveName(event.target.value)} placeholder="例如：双低月轮动" maxLength={100} />
          </label>
          <label className="text-sm">
            说明（可选）
            <Input aria-label="配置说明" className="mt-1" value={saveDescription} onChange={(event) => setSaveDescription(event.target.value)} placeholder="备注这组参数的意图" maxLength={200} />
          </label>
        </div>
      </Modal>
    </div>
  );
};
