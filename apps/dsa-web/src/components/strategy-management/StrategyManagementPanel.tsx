import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Drawer, Input, Switch, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Link } from 'react-router-dom';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import {
  liveStrategyApi,
  type LiveStrategyConfig,
  type LiveStrategyDefinition,
  type NotifyRunResult,
} from '../../api/liveStrategy';
import { ApiErrorAlert, Card, Loading } from '../common';

type StrategyRow = {
  def: LiveStrategyDefinition;
  /** order 模式：当前交易配置即「当前实盘策略」 */
  isLive: boolean;
  /** notify 模式：该策略的信号通知配置（未开通为 null） */
  notifyConfig: LiveStrategyConfig | null;
};

type NotifyDraft = {
  enabled: boolean;
  scheduleTime: string;
  symbolsText: string;
  parameters: Record<string, unknown>;
};

const INSTRUMENT_LABELS: Record<string, { label: string; color: string }> = {
  convertible_bond: { label: '可转债', color: 'blue' },
  etf: { label: 'ETF', color: 'cyan' },
  stock: { label: '股票', color: 'geekblue' },
  hk_stock: { label: '港股', color: 'purple' },
  us_stock: { label: '美股', color: 'purple' },
};

const instrumentCell = (types?: string[]) => {
  const list = (types ?? []).map((t) => INSTRUMENT_LABELS[t] ?? { label: t, color: 'default' });
  if (!list.length) return <span className="text-secondary-text">--</span>;
  return <span className="flex flex-wrap gap-1">{list.map((x) => <Tag key={x.label} color={x.color}>{x.label}</Tag>)}</span>;
};

const liveModeCell = (mode?: string | null) => {
  if (mode === 'order') return <Tag color="green">实盘交易</Tag>;
  if (mode === 'notify') return <Tag color="orange">信号通知</Tag>;
  return <Tag>仅回测</Tag>;
};

const SCHEDULE_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;

const draftFromConfig = (def: LiveStrategyDefinition, config: LiveStrategyConfig | null): NotifyDraft => ({
  enabled: config?.enabled ?? false,
  scheduleTime: config?.scheduleTime ?? '14:25',
  symbolsText: (config?.symbols ?? []).join(','),
  parameters: Object.fromEntries(
    (def.parameters ?? []).map((p) => [p.key, config?.parameters?.[p.key] ?? p.default]),
  ),
});

export const StrategyManagementPanel: React.FC<{ className?: string }> = ({ className }) => {
  const [defs, setDefs] = useState<LiveStrategyDefinition[]>([]);
  const [configs, setConfigs] = useState<LiveStrategyConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [message, setMessage] = useState('');
  // order 配置（当前实盘策略）的内联开关与时刻
  const [scheduleDraft, setScheduleDraft] = useState('');
  // notify 抽屉
  const [drawerDef, setDrawerDef] = useState<LiveStrategyDefinition | null>(null);
  const [draft, setDraft] = useState<NotifyDraft | null>(null);
  const [drawerBusy, setDrawerBusy] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [drawerError, setDrawerError] = useState<ParsedApiError | null>(null);
  const [runResult, setRunResult] = useState<NotifyRunResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [strategyDefs, allConfigs] = await Promise.all([
        liveStrategyApi.listStrategies(),
        liveStrategyApi.listConfigs(),
      ]);
      setDefs(strategyDefs);
      setConfigs(allConfigs);
      const orderIds = new Set(strategyDefs.filter((d) => d.liveMode === 'order').map((d) => d.strategyId));
      const orderConfig = allConfigs.find((c) => orderIds.has(c.strategyId));
      setScheduleDraft(orderConfig?.scheduleTime ?? '14:35');
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const orderIds = new Set(defs.filter((d) => d.liveMode === 'order').map((d) => d.strategyId));
  const orderConfig = configs.find((c) => orderIds.has(c.strategyId)) ?? null;

  const saveLiveSettings = useCallback(async (patch: Partial<LiveStrategyConfig>) => {
    if (!orderConfig) return;
    setMessage('');
    try {
      await liveStrategyApi.saveConfig({ ...orderConfig, ...patch });
      await load();
      if (patch.scheduleTime !== undefined) setMessage('已保存');
    } catch (err) {
      setError(getParsedApiError(err));
    }
  }, [orderConfig, load]);

  const openNotifyDrawer = (def: LiveStrategyDefinition) => {
    const config = configs.find((c) => c.strategyId === def.strategyId) ?? null;
    setDrawerDef(def);
    setDraft(draftFromConfig(def, config));
    setDrawerError(null);
    setRunResult(null);
  };

  const saveNotifyDraft = useCallback(async () => {
    if (!drawerDef || !draft) return;
    setDrawerBusy(true);
    setDrawerError(null);
    try {
      await liveStrategyApi.saveNotifyConfig({
        strategyId: drawerDef.strategyId,
        enabled: draft.enabled,
        symbols: draft.symbolsText.split(/[\s,，]+/).map((s) => s.trim()).filter(Boolean),
        parameters: draft.parameters,
        scheduleTime: SCHEDULE_PATTERN.test(draft.scheduleTime) ? draft.scheduleTime : undefined,
      });
      setMessage('信号通知配置已保存');
      await load();
    } catch (err) {
      setDrawerError(getParsedApiError(err));
    } finally {
      setDrawerBusy(false);
    }
  }, [drawerDef, draft, load]);

  const runNotifyNow = useCallback(async () => {
    if (!drawerDef) return;
    const config = configs.find((c) => c.strategyId === drawerDef.strategyId);
    if (!config?.id) return; // 未保存过配置时按钮已禁用
    setRunBusy(true);
    setDrawerError(null);
    try {
      setRunResult(await liveStrategyApi.runNotifyConfig(config.id));
    } catch (err) {
      setDrawerError(getParsedApiError(err));
    } finally {
      setRunBusy(false);
    }
  }, [drawerDef, configs]);

  const rows: StrategyRow[] = defs.map((def) => ({
    def,
    isLive: def.liveMode === 'order' && orderConfig?.strategyId === def.strategyId,
    notifyConfig: def.liveMode === 'notify' ? configs.find((c) => c.strategyId === def.strategyId) ?? null : null,
  }));
  const liveDef = defs.find((d) => d.strategyId === orderConfig?.strategyId);
  const counts = {
    total: defs.length,
    order: defs.filter((d) => d.liveMode === 'order').length,
    notify: defs.filter((d) => d.liveMode === 'notify').length,
    notifyActive: rows.filter((r) => r.notifyConfig?.enabled).length,
    backtestOnly: defs.filter((d) => !d.liveMode).length,
  };

  const columns: ColumnsType<StrategyRow> = [
    {
      title: '策略',
      dataIndex: ['def', 'name'],
      render: (_, row) => (
        <div>
          <div className="font-medium">{row.def.name}</div>
          <div className="text-xs text-secondary-text">{row.def.strategyId}</div>
        </div>
      ),
    },
    { title: '品种', dataIndex: ['def', 'instrumentTypes'], render: (_, row) => instrumentCell(row.def.instrumentTypes) },
    { title: '实盘能力', dataIndex: ['def', 'liveMode'], width: 110, render: (_, row) => liveModeCell(row.def.liveMode) },
    {
      title: '实盘状态',
      key: 'liveStatus',
      width: 110,
      render: (_, row) => {
        if (row.isLive) {
          return orderConfig?.enabled
            ? <Tag color="success">实盘运行中</Tag>
            : <Tag color="default">已暂停</Tag>;
        }
        if (row.notifyConfig) {
          return row.notifyConfig.enabled
            ? <Tag color="success">通知运行中</Tag>
            : <Tag color="default">通知已暂停</Tag>;
        }
        return <span className="text-secondary-text">--</span>;
      },
    },
    {
      title: '自动运行',
      key: 'schedule',
      width: 110,
      render: (_, row) => {
        const time = row.isLive ? orderConfig?.scheduleTime : row.notifyConfig?.scheduleTime;
        return time ? <span>{time} · 交易日</span> : <span className="text-secondary-text">--</span>;
      },
    },
    {
      title: '说明',
      dataIndex: ['def', 'description'],
      ellipsis: { showTitle: false },
      render: (_, row) => (
        <Tooltip title={row.def.description} placement="topLeft">
          <span className="text-secondary-text">{row.def.description}</span>
        </Tooltip>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 320,
      render: (_, row) => {
        if (row.isLive && orderConfig) {
          const draftInvalid = !SCHEDULE_PATTERN.test(scheduleDraft) || scheduleDraft === orderConfig.scheduleTime;
          return (
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-1 text-sm">
                自动实盘
                <Switch
                  aria-label="自动实盘开关"
                  checked={orderConfig.enabled}
                  onChange={(enabled) => void saveLiveSettings({ enabled })}
                />
              </label>
              <Input
                aria-label="自动运行时刻"
                className="w-24"
                placeholder="14:35"
                value={scheduleDraft}
                onChange={(e) => setScheduleDraft(e.target.value)}
              />
              <Button
                size="small"
                disabled={draftInvalid}
                onClick={() => void saveLiveSettings({ scheduleTime: scheduleDraft })}
              >
                保存时刻
              </Button>
            </div>
          );
        }
        if (row.def.liveMode === 'order') {
          return <Link className="text-sm underline-offset-2 hover:underline" to="/trading">去实盘页配置</Link>;
        }
        if (row.def.liveMode === 'notify') {
          return (
            <Button size="small" aria-label={`信号通知设置-${row.def.strategyId}`} onClick={() => openNotifyDrawer(row.def)}>
              信号通知设置
            </Button>
          );
        }
        return <span className="text-secondary-text">--</span>;
      },
    },
  ];

  return (
    <div className={className}>
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-6">
            <div className="min-w-28">
              <div className="text-xs text-secondary-text">策略总数</div>
              <div className="text-base font-medium text-foreground">{counts.total}</div>
            </div>
            <div className="min-w-28">
              <div className="text-xs text-secondary-text">可实盘交易</div>
              <div className="text-base font-medium text-foreground">{counts.order}</div>
            </div>
            <div className="min-w-28">
              <div className="text-xs text-secondary-text">信号通知</div>
              <div className="text-base font-medium text-foreground">{counts.notifyActive}/{counts.notify}</div>
            </div>
            <div className="min-w-28">
              <div className="text-xs text-secondary-text">仅回测</div>
              <div className="text-base font-medium text-foreground">{counts.backtestOnly}</div>
            </div>
            <div className="min-w-40">
              <div className="text-xs text-secondary-text">当前实盘策略</div>
              <div className="text-base font-medium text-foreground">
                {liveDef?.name ?? '未配置'}
                {liveDef ? ` · ${orderConfig?.scheduleTime ?? '14:35'} · ${orderConfig?.enabled ? '运行中' : '已暂停'}` : ''}
              </div>
            </div>
          </div>
          {message ? <Tag>{message}</Tag> : null}
        </div>
      </Card>

      <div className="mt-4">
        {error ? (
          <ApiErrorAlert error={error} actionLabel="重试" onAction={() => void load()} />
        ) : null}
        {loading ? (
          <Loading />
        ) : (
          <Table<StrategyRow>
            size="small"
            rowKey={(row) => row.def.strategyId}
            dataSource={rows}
            columns={columns}
            pagination={false}
            locale={{ emptyText: '暂无策略' }}
          />
        )}
      </div>

      <Drawer
        title={`信号通知设置 · ${drawerDef?.name ?? ''}`}
        width={420}
        open={drawerDef !== null}
        onClose={() => { setDrawerDef(null); setDraft(null); }}
      >
        {drawerDef && draft ? (
          <div className="space-y-4">
            <p className="text-sm text-secondary-text">
              交易日按配置时刻评估信号并推送飞书群机器人（复用 FEISHU_WEBHOOK_URL），只通知、不下单；每日汇总必发。
            </p>
            <label className="flex items-center gap-2 text-sm">
              自动通知
              <Switch
                aria-label="自动通知开关"
                checked={draft.enabled}
                onChange={(enabled) => setDraft({ ...draft, enabled })}
              />
            </label>
            <label className="block text-sm">
              自动运行时刻（交易日）
              <Input
                aria-label="通知运行时刻"
                className="mt-1"
                placeholder="14:25"
                value={draft.scheduleTime}
                onChange={(e) => setDraft({ ...draft, scheduleTime: e.target.value })}
              />
            </label>
            <label className="block text-sm">
              监测标的（逗号分隔 ETF 代码）
              <Input
                aria-label="监测标的"
                className="mt-1"
                placeholder="510880,159307"
                value={draft.symbolsText}
                onChange={(e) => setDraft({ ...draft, symbolsText: e.target.value })}
              />
            </label>
            <div className="grid gap-2 sm:grid-cols-2">
              {(drawerDef.parameters ?? []).map((p) => p.type === 'boolean' ? (
                <label key={p.key} className="flex items-center gap-1 text-sm">
                  {p.label}
                  <Switch
                    aria-label={`通知参数-${p.key}`}
                    checked={Boolean(draft.parameters[p.key] ?? p.default)}
                    onChange={(v) => setDraft({ ...draft, parameters: { ...draft.parameters, [p.key]: v } })}
                  />
                </label>
              ) : (
                <label key={p.key} className="block text-sm">
                  {p.label}
                  <Input
                    aria-label={`通知参数-${p.key}`}
                    className="mt-1"
                    type="number"
                    value={String(draft.parameters[p.key] ?? '')}
                    onChange={(e) => setDraft({ ...draft, parameters: { ...draft.parameters, [p.key]: Number(e.target.value) } })}
                  />
                </label>
              ))}
            </div>
            {drawerError ? <Alert type="error" showIcon message={drawerError.title} description={drawerError.message} /> : null}
            <div className="flex flex-wrap gap-2">
              <Button
                type="primary"
                aria-label="保存信号通知配置"
                loading={drawerBusy}
                disabled={!draft.symbolsText.trim() || !SCHEDULE_PATTERN.test(draft.scheduleTime)}
                onClick={() => void saveNotifyDraft()}
              >
                保存配置
              </Button>
              <Button
                aria-label="立即运行信号通知"
                loading={runBusy}
                disabled={!configs.some((c) => c.strategyId === drawerDef.strategyId)}
                onClick={() => void runNotifyNow()}
              >
                立即运行
              </Button>
            </div>
            {runResult ? (
              <Card>
                <div className="mb-2 flex items-center gap-2 text-sm">
                  <Tag color={runResult.status === 'completed' ? 'success' : 'error'}>{runResult.status === 'completed' ? '运行完成' : '运行失败'}</Tag>
                  {runResult.feishuSent ? <span>飞书已发送</span> : <span className="text-secondary-text">飞书未发送（检查 FEISHU_WEBHOOK_URL 或重试）</span>}
                </div>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{runResult.message ?? runResult.errorMessage ?? '--'}</pre>
              </Card>
            ) : null}
          </div>
        ) : null}
      </Drawer>
    </div>
  );
};
