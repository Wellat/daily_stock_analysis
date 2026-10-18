import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Collapse, Drawer, Input, Modal, Popconfirm, Select, Switch, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Link } from 'react-router-dom';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import {
  LIVE_ACCOUNT_OPTIONS,
  liveStrategyApi,
  type LiveStrategyConfig,
  type LiveStrategyDefinition,
  type NotifyRunResult,
} from '../../api/liveStrategy';
import { ApiErrorAlert, Card, Loading } from '../common';

/**策略实例编辑草稿（新建/编辑/复制共用一套表单）*/
type InstanceDraft = {
  strategyId: string;
  name: string;
  description: string;
  symbolsText: string;
  parameters: Record<string, unknown>;
  enabled: boolean;
  scheduleTime: string;
  qmtAccount: string;
};

type EditorState = {
  mode: 'create' | 'edit' | 'duplicate';
  /**编辑/复制时的原实例 id（create 为空）*/
  instanceId?: number;
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

const SYMBOLS_PLACEHOLDER: Record<string, string> = {
  notify: '510880,159307（必填）',
  order: '113050,113062（自选池，留空=全市场）',
};

const draftFromDef = (def: LiveStrategyDefinition): InstanceDraft => ({
  strategyId: def.strategyId,
  name: '',
  description: '',
  symbolsText: '',
  parameters: Object.fromEntries((def.parameters ?? []).map((p) => [p.key, p.default])),
  enabled: false,
  scheduleTime: def.liveMode === 'notify' ? '14:25' : '14:35',
  qmtAccount: def.liveMode === 'order' ? (LIVE_ACCOUNT_OPTIONS[0]?.value ?? '') : '',
});

const draftFromInstance = (def: LiveStrategyDefinition, config: LiveStrategyConfig, nameSuffix = ''): InstanceDraft => ({
  strategyId: config.strategyId,
  name: `${config.name ?? def.name}${nameSuffix}`,
  description: config.description ?? '',
  symbolsText: (config.symbols ?? []).join(','),
  // 先铺全量参数再覆盖渲染键：信号族的显式规则（entry_rules/exit_rules 等）
  // 不占参数面板，编辑保存时必须原样带回，否则会被静默丢掉
  parameters: {
    ...(config.parameters ?? {}),
    ...Object.fromEntries(
      (def.parameters ?? []).map((p) => [p.key, config.parameters?.[p.key] ?? p.default]),
    ),
  },
  enabled: config.enabled,
  scheduleTime: config.scheduleTime ?? '14:35',
  qmtAccount: config.qmtAccount && config.qmtAccount !== '-' ? config.qmtAccount : (LIVE_ACCOUNT_OPTIONS[0]?.value ?? ''),
});

const fmtParamValue = (v: unknown) => (typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v));

const paramsDigest = (parameters: Record<string, unknown>) => {
  const entries = Object.entries(parameters ?? {}).filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (!entries.length) return '--';
  return entries.slice(0, 3).map(([k, v]) => `${k}=${fmtParamValue(v)}`).join(' · ') + (entries.length > 3 ? ' …' : '');
};

export const StrategyManagementPanel: React.FC<{ className?: string }> = ({ className }) => {
  const [defs, setDefs] = useState<LiveStrategyDefinition[]>([]);
  const [configs, setConfigs] = useState<LiveStrategyConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [message, setMessage] = useState('');
  // 实例编辑抽屉
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [draft, setDraft] = useState<InstanceDraft | null>(null);
  const [drawerBusy, setDrawerBusy] = useState(false);
  const [drawerError, setDrawerError] = useState<ParsedApiError | null>(null);
  // 立即运行结果弹窗
  const [runBusyId, setRunBusyId] = useState<number | null>(null);
  const [runResult, setRunResult] = useState<{ instanceName: string; data: NotifyRunResult | Record<string, unknown> } | null>(null);

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
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const defOf = useCallback(
    (strategyId: string) => defs.find((d) => d.strategyId === strategyId) ?? null,
    [defs],
  );

  const openCreate = () => {
    const first = defs[0];
    if (!first) return;
    setEditor({ mode: 'create' });
    setDraft(draftFromDef(first));
    setDrawerError(null);
  };

  const openEditor = (config: LiveStrategyConfig, mode: 'edit' | 'duplicate') => {
    const def = defOf(config.strategyId);
    if (!def) return; // 模板已下线的孤儿实例不支持编辑，仅可删除
    setEditor({ mode, instanceId: config.id });
    setDraft(draftFromInstance(def, config, mode === 'duplicate' ? ' 副本' : ''));
    setDrawerError(null);
  };

  const saveDraft = useCallback(async () => {
    if (!editor || !draft) return;
    const def = defOf(draft.strategyId);
    if (!def || !draft.name.trim()) return;
    setDrawerBusy(true);
    setDrawerError(null);
    try {
      const payload = {
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        strategyId: draft.strategyId,
        qmtAccount: def.liveMode === 'order' ? draft.qmtAccount : null,
        enabled: def.liveMode == null ? false : draft.enabled,
        symbols: draft.symbolsText.split(/[\s,，]+/).map((s) => s.trim()).filter(Boolean),
        parameters: draft.parameters,
        scheduleTime: SCHEDULE_PATTERN.test(draft.scheduleTime) ? draft.scheduleTime : null,
      };
      if (editor.mode === 'edit' && editor.instanceId) {
        await liveStrategyApi.updateInstance(editor.instanceId, payload);
        setMessage('实例已更新');
      } else {
        await liveStrategyApi.createInstance(payload);
        setMessage('策略实例已创建');
      }
      setEditor(null);
      setDraft(null);
      await load();
    } catch (err) {
      setDrawerError(getParsedApiError(err));
    } finally {
      setDrawerBusy(false);
    }
  }, [editor, draft, defOf, load]);

  const removeInstance = useCallback(async (config: LiveStrategyConfig) => {
    if (!config.id) return;
    setMessage('');
    try {
      await liveStrategyApi.deleteInstance(config.id);
      setMessage(`已删除实例「${config.name}」（历史运行记录保留）`);
      await load();
    } catch (err) {
      setError(getParsedApiError(err));
    }
  }, [load]);

  const runInstanceNow = useCallback(async (config: LiveStrategyConfig) => {
    if (!config.id) return;
    setRunBusyId(config.id);
    setError(null);
    try {
      const data = await liveStrategyApi.runInstance(config.id);
      setRunResult({ instanceName: config.name ?? '', data: data as Record<string, unknown> });
      await load();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setRunBusyId(null);
    }
  }, [load]);

  const counts = useMemo(() => {
    const orderIds = new Set(defs.filter((d) => d.liveMode === 'order').map((d) => d.strategyId));
    const notifyIds = new Set(defs.filter((d) => d.liveMode === 'notify').map((d) => d.strategyId));
    return {
      instances: configs.length,
      orderActive: configs.filter((c) => c.enabled && orderIds.has(c.strategyId)).length,
      notifyActive: configs.filter((c) => c.enabled && notifyIds.has(c.strategyId)).length,
      inactive: configs.filter((c) => !c.enabled).length,
      templates: defs.length,
    };
  }, [defs, configs]);

  const instanceColumns: ColumnsType<LiveStrategyConfig> = [
    {
      title: '实例',
      key: 'name',
      render: (_, c) => (
        <div>
          <div className="font-medium">{c.name || `#${c.id}`}</div>
          {c.description ? <div className="text-xs text-secondary-text">{c.description}</div> : null}
        </div>
      ),
    },
    {
      title: '模板',
      dataIndex: 'strategyId',
      render: (_, c) => {
        const def = defOf(c.strategyId);
        return (
          <div>
            <div>{def?.name ?? <span className="text-secondary-text">{c.strategyId}</span>}</div>
            {def ? instrumentCell(def.instrumentTypes) : null}
          </div>
        );
      },
    },
    {
      title: '标的',
      key: 'symbols',
      render: (_, c) => (c.symbols?.length
        ? <Tooltip title={c.symbols.join('、')}><span>{c.symbols.slice(0, 3).join('、')}{c.symbols.length > 3 ? ' …' : ''}</span></Tooltip>
        : <span className="text-secondary-text">全市场</span>),
    },
    {
      title: '参数摘要',
      key: 'params',
      ellipsis: { showTitle: false },
      render: (_, c) => (
        <Tooltip title={Object.entries(c.parameters ?? {}).map(([k, v]) => `${k}=${fmtParamValue(v)}`).join('\n') || '--'}>
          <span className="text-secondary-text">{paramsDigest(c.parameters ?? {})}</span>
        </Tooltip>
      ),
    },
    { title: '能力', key: 'capability', width: 96, render: (_, c) => liveModeCell(defOf(c.strategyId)?.liveMode) },
    {
      title: '运行状态',
      key: 'status',
      width: 110,
      render: (_, c) => {
        const mode = defOf(c.strategyId)?.liveMode;
        if (mode == null) return <Tag>仅回测</Tag>;
        if (!c.enabled) return <Tag color="default">未启用</Tag>;
        return mode === 'order'
          ? <Tag color="success">实盘运行中</Tag>
          : <Tag color="success">通知运行中</Tag>;
      },
    },
    {
      title: '自动运行',
      key: 'schedule',
      width: 100,
      render: (_, c) => {
        const mode = defOf(c.strategyId)?.liveMode;
        if (mode == null) return <span className="text-secondary-text">--</span>;
        return <span>{c.scheduleTime ?? '14:35'} · 交易日</span>;
      },
    },
    {
      title: '最近运行',
      key: 'lastRun',
      width: 170,
      render: (_, c) => {
        const run = c.lastRun;
        if (!run) return <span className="text-secondary-text">--</span>;
        return (
          <Tooltip title={`${run.tradeDate} · ${run.status}`}>
            <span className="flex items-center gap-1">
              <Tag color={run.status === 'completed' ? 'success' : run.status === 'failed' ? 'error' : 'processing'}>{run.status}</Tag>
              <span className="text-xs text-secondary-text">{run.tradeDate}</span>
            </span>
          </Tooltip>
        );
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 300,
      render: (_, c) => {
        const mode = defOf(c.strategyId)?.liveMode;
        return (
          <div className="flex flex-wrap items-center gap-2">
            <Button size="small" aria-label={`编辑实例-${c.name ?? c.id}`} onClick={() => openEditor(c, 'edit')}>编辑</Button>
            <Button size="small" aria-label={`复制实例-${c.name ?? c.id}`} onClick={() => openEditor(c, 'duplicate')}>复制</Button>
            <Popconfirm
              title="删除策略实例"
              description={`确定删除「${c.name}」？历史运行记录会保留。`}
              okText="删除"
              okButtonProps={{ danger: true }}
              onConfirm={() => void removeInstance(c)}
            >
              <Button size="small" danger>删除</Button>
            </Popconfirm>
            {mode == null ? (
              <Link className="text-sm underline-offset-2 hover:underline" to={`/strategy-lab?instance=${c.id}`}>去回测</Link>
            ) : mode === 'order' ? (
              <Popconfirm
                title="立即运行实盘策略"
                description="将按该实例当前参数真实生成并提交 QMT 订单，确认运行？"
                okText="运行"
                onConfirm={() => void runInstanceNow(c)}
              >
                <Button size="small" aria-label={`立即运行实盘-${c.name ?? c.id}`} loading={runBusyId === c.id}>立即运行</Button>
              </Popconfirm>
            ) : (
              <Button size="small" aria-label={`立即运行-${c.name ?? c.id}`} loading={runBusyId === c.id} onClick={() => void runInstanceNow(c)}>立即运行</Button>
            )}
          </div>
        );
      },
    },
  ];

  const templateColumns: ColumnsType<LiveStrategyDefinition> = [
    {
      title: '策略模板',
      dataIndex: 'name',
      width: '20%',
      render: (_, d) => (
        <div>
          <div className="font-medium">{d.name}</div>
          <div className="text-xs text-secondary-text">{d.strategyId}</div>
        </div>
      ),
    },
    { title: '品种', dataIndex: 'instrumentTypes', width: '10%', render: (_, d) => instrumentCell(d.instrumentTypes) },
    { title: '实盘能力', dataIndex: 'liveMode', width: '10%', render: (_, d) => liveModeCell(d.liveMode) },
    {
      title: '说明',
      dataIndex: 'description',
      ellipsis: { showTitle: false },
      render: (_, d) => (
        <Tooltip title={d.description} placement="topLeft">
          <span className="text-secondary-text">{d.description}</span>
        </Tooltip>
      ),
    },
  ];

  const draftDef = draft ? defOf(draft.strategyId) : null;

  return (
    <div className={className}>
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-6">
            <div className="min-w-24">
              <div className="text-xs text-secondary-text">策略实例</div>
              <div className="text-base font-medium text-foreground">{counts.instances}</div>
            </div>
            <div className="min-w-24">
              <div className="text-xs text-secondary-text">实盘运行中</div>
              <div className="text-base font-medium text-foreground">{counts.orderActive}</div>
            </div>
            <div className="min-w-24">
              <div className="text-xs text-secondary-text">通知运行中</div>
              <div className="text-base font-medium text-foreground">{counts.notifyActive}</div>
            </div>
            <div className="min-w-24">
              <div className="text-xs text-secondary-text">未启用</div>
              <div className="text-base font-medium text-foreground">{counts.inactive}</div>
            </div>
            <div className="min-w-24">
              <div className="text-xs text-secondary-text">策略模板</div>
              <div className="text-base font-medium text-foreground">{counts.templates}</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {message ? <Tag>{message}</Tag> : null}
            <Button type="primary" aria-label="新建策略" disabled={!defs.length} onClick={openCreate}>新建策略</Button>
          </div>
        </div>
      </Card>

      <div className="mt-4">
        {error ? (
          <ApiErrorAlert error={error} actionLabel="重试" onAction={() => void load()} />
        ) : null}
        {loading ? (
          <Loading />
        ) : (
          <Table<LiveStrategyConfig>
            size="small"
            rowKey="id"
            dataSource={configs}
            columns={instanceColumns}
            pagination={false}
            locale={{ emptyText: '暂无策略实例，点击右上角「新建策略」从模板创建' }}
          />
        )}
      </div>

      <Collapse
        className="mt-4"
        items={[{
          key: 'templates',
          label: <span className="text-sm">策略模板总览（{defs.length} 个 · 代码内置定义，新实例从模板创建）</span>,
          children: (
            <Table<LiveStrategyDefinition>
              size="small"
              rowKey="strategyId"
              dataSource={defs}
              columns={templateColumns}
              pagination={false}
              locale={{ emptyText: '暂无模板' }}
            />
          ),
        }]}
      />

      <Drawer
        title={editor?.mode === 'edit' ? `编辑实例 · ${draft?.name ?? ''}` : '新建策略实例'}
        width={460}
        open={editor !== null}
        onClose={() => { setEditor(null); setDraft(null); }}
      >
        {draft && draftDef ? (
          <div className="space-y-4">
            <p className="text-sm text-secondary-text">
              策略实例 = 模板 + 参数 + 标的 + 调度。保存后可在下方开启定时运行（order 实盘交易 / notify 信号通知），
              或去策略实验室回测。
            </p>
            <label className="block text-sm">
              策略模板
              <Select
                aria-label="策略模板"
                className="mt-1 w-full"
                value={draft.strategyId}
                disabled={editor?.mode === 'edit'}
                options={defs.map((d) => ({
                  value: d.strategyId,
                  label: `${d.name}（${d.liveMode === 'order' ? '实盘交易' : d.liveMode === 'notify' ? '信号通知' : '仅回测'}）`,
                }))}
                onChange={(strategyId) => {
                  const next = defOf(strategyId);
                  if (next) setDraft(draftFromDef(next));
                }}
              />
            </label>
            <label className="block text-sm">
              实例名
              <Input
                aria-label="实例名"
                className="mt-1"
                placeholder="如：红利ETF均线回归"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </label>
            <label className="block text-sm">
              备注
              <Input
                aria-label="实例备注"
                className="mt-1"
                placeholder="可选"
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              />
            </label>
            <label className="block text-sm">
              标的（逗号分隔）
              <Input
                aria-label="实例标的"
                className="mt-1"
                placeholder={SYMBOLS_PLACEHOLDER[draftDef.liveMode ?? ''] ?? '可选'}
                value={draft.symbolsText}
                onChange={(e) => setDraft({ ...draft, symbolsText: e.target.value })}
              />
            </label>
            {draftDef.liveMode != null ? (
              <div className="rounded border border-border p-3 space-y-3">
                <div className="text-sm font-medium">定时运行（交易日）</div>
                <label className="flex items-center gap-2 text-sm">
                  {draftDef.liveMode === 'order' ? '自动实盘' : '自动通知'}
                  <Switch
                    aria-label="自动运行开关"
                    checked={draft.enabled}
                    onChange={(enabled) => setDraft({ ...draft, enabled })}
                  />
                </label>
                <label className="block text-sm">
                  自动运行时刻（HH:MM）
                  <Input
                    aria-label="自动运行时刻"
                    className="mt-1 w-28"
                    placeholder={draftDef.liveMode === 'order' ? '14:35' : '14:25'}
                    value={draft.scheduleTime}
                    onChange={(e) => setDraft({ ...draft, scheduleTime: e.target.value })}
                  />
                </label>
                {draftDef.liveMode === 'order' ? (
                  <label className="block text-sm">
                    QMT 账户
                    <Select
                      aria-label="QMT账户"
                      className="mt-1 w-full"
                      value={draft.qmtAccount}
                      options={LIVE_ACCOUNT_OPTIONS}
                      onChange={(qmtAccount) => setDraft({ ...draft, qmtAccount })}
                    />
                  </label>
                ) : null}
                {draftDef.liveMode === 'order' && draft.enabled ? (
                  <Alert
                    type="warning"
                    showIcon
                    message="多个实盘实例同时启用会在同一 QMT 账户并发下单，建议错开各实例的自动运行时刻。"
                  />
                ) : null}
              </div>
            ) : (
              <Alert type="info" showIcon message="该模板仅支持回测；保存为实例后可在策略实验室快速加载复用。" />
            )}
            <div className="grid gap-2 sm:grid-cols-2">
              {(draftDef.parameters ?? []).map((p) => p.type === 'boolean' ? (
                <label key={p.key} className="flex items-center gap-1 text-sm">
                  {p.label}
                  <Switch
                    aria-label={`实例参数-${p.key}`}
                    checked={Boolean(draft.parameters[p.key] ?? p.default)}
                    onChange={(v) => setDraft({ ...draft, parameters: { ...draft.parameters, [p.key]: v } })}
                  />
                </label>
              ) : p.type === 'enum' ? (
                <label key={p.key} className="block text-sm">
                  {p.label}
                  <Select
                    aria-label={`实例参数-${p.key}`}
                    className="mt-1 w-full"
                    value={String(draft.parameters[p.key] ?? p.default ?? '')}
                    options={(p.options ?? []).map((o) => ({ value: String(o), label: String(o) }))}
                    onChange={(v) => setDraft({ ...draft, parameters: { ...draft.parameters, [p.key]: v } })}
                  />
                </label>
              ) : (
                <label key={p.key} className="block text-sm">
                  {p.label}
                  <Input
                    aria-label={`实例参数-${p.key}`}
                    className="mt-1"
                    type="number"
                    value={String(draft.parameters[p.key] ?? '')}
                    onChange={(e) => setDraft({ ...draft, parameters: { ...draft.parameters, [p.key]: Number(e.target.value) } })}
                  />
                </label>
              ))}
            </div>
            {drawerError ? <Alert type="error" showIcon message={drawerError.title} description={drawerError.message} /> : null}
            <Button
              type="primary"
              aria-label="保存策略实例"
              loading={drawerBusy}
              disabled={!draft.name.trim() || (draftDef.liveMode === 'notify' && !draft.symbolsText.trim()) || !SCHEDULE_PATTERN.test(draft.scheduleTime)}
              onClick={() => void saveDraft()}
            >
              {editor?.mode === 'edit' ? '保存修改' : '创建实例'}
            </Button>
          </div>
        ) : null}
      </Drawer>

      <Modal
        open={runResult !== null}
        title={`运行结果 · ${runResult?.instanceName ?? ''}`}
        footer={null}
        onCancel={() => setRunResult(null)}
        width={560}
      >
        {runResult ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm">
              <Tag color={runResult.data.status === 'completed' ? 'success' : 'error'}>
                {runResult.data.status === 'completed' ? '运行完成' : '运行失败'}
              </Tag>
              {'feishuSent' in runResult.data
                ? (runResult.data.feishuSent
                  ? <span>飞书已发送</span>
                  : <span className="text-secondary-text">飞书未发送（检查 FEISHU_WEBHOOK_URL 或重试）</span>)
                : null}
              {'runId' in runResult.data ? <span className="text-secondary-text">运行记录 #{String(runResult.data.runId)}</span> : null}
            </div>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 text-xs">
              {String(runResult.data.message ?? runResult.data.errorMessage ?? JSON.stringify(runResult.data, null, 2))}
            </pre>
          </div>
        ) : null}
      </Modal>
    </div>
  );
};
