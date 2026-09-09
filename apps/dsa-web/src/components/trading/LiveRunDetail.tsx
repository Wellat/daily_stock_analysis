import { Button, Card, Descriptions, Table, Tag } from 'antd';
import type { LiveStrategyBatch, LiveStrategyDecision, LiveStrategyOrder, LiveStrategyRun } from '../../api/liveStrategy';
import { runModeTag, runStatusTag } from './liveRunTags';

const ACTION_LABELS: Record<string, { label: string; color: string }> = {
  buy: { label: '买入', color: 'success' },
  sell: { label: '卖出', color: 'error' },
  exit: { label: '事件退出', color: 'orange' },
  hold: { label: '持有', color: 'default' },
  blocked: { label: '受阻', color: 'error' },
};

const ORDER_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  pending: { label: '待执行', color: 'warning' },
  submitted: { label: '已提交', color: 'processing' },
  filled: { label: '已成交', color: 'success' },
  rejected: { label: '已失败', color: 'error' },
  cancelled: { label: '已取消', color: 'default' },
};

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div className="mt-3">
    <div className="mb-1 text-sm font-semibold text-foreground">{title}</div>
    {children}
  </div>
);

/**标的展示为“名称（代码）”，缺名称时退回代码。*/
const instrumentLabel = (symbol?: string | null, name?: string | null) =>
  (name && symbol ? `${name}（${symbol}）` : name || symbol) || '-';

const actionTag = (v: string) => {
  const meta = ACTION_LABELS[v];
  return meta ? <Tag color={meta.color}>{meta.label}</Tag> : <Tag>{v}</Tag>;
};

type Props = {
  run: LiveStrategyRun;
  decisions: LiveStrategyDecision[];
  orders: LiveStrategyOrder[];
  batch?: LiveStrategyBatch | null;
  loading?: boolean;
  onClose: () => void;
};

/**单次实盘运行的完整产出，按执行管线分三段：目标组合（策略选什么）→ 计划结果（怎么调）→ 订单执行（执行得怎样）。*/
export const LiveRunDetail: React.FC<Props> = ({ run, decisions, orders, batch, loading, onClose }) => {
  const risk = run.risk ?? {};
  const riskChecks = risk.riskChecks ?? [];
  const skipped = risk.skipped ?? [];
  const progress = batch?.orders;
  const planned = run.rebalance ?? [];
  // 计划结果表用名称合并展示，卖出/跳过条目从决策记录反查名称
  const nameBySymbol = new Map<string, string>();
  decisions.forEach((d) => { if (d.symbol && d.symbolName) nameBySymbol.set(d.symbol, d.symbolName); });
  const label = (symbol?: string | null) => instrumentLabel(symbol, nameBySymbol.get(symbol ?? '') ?? null);

  const buyDecisions = decisions.filter((d) => d.action === 'buy');
  const perPositionCash = buyDecisions.find((d) => d.targetAmount != null)?.targetAmount;
  const isEventCheck = run.mode === 'event_check';
  type PlanRow = { key: string; symbol: string | null; side?: string; quantity: number | null; reason?: string; skipped: boolean };
  const planRows: PlanRow[] = [
    ...planned.map((o, i) => ({ key: `o-${i}`, symbol: o.symbol, side: o.side, quantity: o.quantity as number | null, reason: o.reason, skipped: false })),
    ...skipped.map((s, i) => ({ key: `s-${i}`, symbol: s.symbol ?? null, side: undefined, quantity: null, reason: s.reason, skipped: true })),
  ];

  return (
    <Card className="mt-3" loading={loading}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <b className="text-base">运行详情</b>
          <span className="text-xs text-secondary-text">{run.runUid}</span>
          {runModeTag(run.mode)}
          {runStatusTag(run.status)}
        </div>
        <Button size="small" onClick={onClose}>关闭</Button>
      </div>
      <Descriptions className="mt-2" size="small" column={{ xs: 1, sm: 2, md: 3 }}>
        <Descriptions.Item label="交易日">{run.tradeDate}</Descriptions.Item>
        <Descriptions.Item label="策略">{run.strategyId ? `${run.strategyId}${run.strategyVersion ? `（${run.strategyVersion}）` : ''}` : '-'}</Descriptions.Item>
        <Descriptions.Item label="账户">{run.qmtAccount || '-'}</Descriptions.Item>
        <Descriptions.Item label="数据快照">{run.dataSnapshotAt || '-'}</Descriptions.Item>
        <Descriptions.Item label="完成时间">{run.completedAt || '-'}</Descriptions.Item>
        <Descriptions.Item label="决策 / 订单">{run.decisionCount ?? decisions.length} / {run.orderCount ?? orders.length}</Descriptions.Item>
        <Descriptions.Item label="单债目标资金">{perPositionCash != null ? perPositionCash : '-'}</Descriptions.Item>
      </Descriptions>
      {run.status === 'failed' && run.errorMessage && (
        <div className="mt-1 text-xs text-red-600">失败原因：{run.errorMessage}</div>
      )}
      {run.skipReason && <div className="mt-1 text-xs text-secondary-text">跳过原因：{run.skipReason}</div>}

      {isEventCheck ? (
        <Section title={`持仓事件扫描（${decisions.length}）`}>
          <Table size="small" pagination={false} rowKey="id" dataSource={decisions}
            columns={[
              { title: '标的', dataIndex: 'symbol', render: (_: unknown, r: LiveStrategyDecision) => label(r.symbol) },
              { title: '动作', dataIndex: 'action', width: 100, render: actionTag },
              { title: '理由', dataIndex: 'reason', render: (v: string | null | undefined) => v || '-' },
            ]} />
        </Section>
      ) : (
        <Section title={`目标组合（${buyDecisions.length}）`}>
          <Table size="small" pagination={false} rowKey="symbol" dataSource={buyDecisions}
            locale={{ emptyText: '本次运行策略未产生买入标的' }}
            columns={[
              { title: '标的', dataIndex: 'symbol', render: (_: unknown, r: LiveStrategyDecision) => label(r.symbol) },
              { title: '最新价', width: 100, align: 'right', render: (_: unknown, r: LiveStrategyDecision) => {
                const close = r.decisionData?.close;
                return typeof close === 'number' ? close : '-';
              } },
              { title: '溢价率', width: 100, align: 'right', render: (_: unknown, r: LiveStrategyDecision) => {
                const premium = r.decisionData?.premiumRate;
                return typeof premium === 'number' ? `${premium.toFixed(2)}%` : '-';
              } },
              { title: '排名', width: 70, align: 'right', render: (_: unknown, r: LiveStrategyDecision) => {
                const rank = r.decisionData?.rank;
                return typeof rank === 'number' ? `#${rank}` : '-';
              } },
            ]} />
        </Section>
      )}

      <Section title={`计划结果（${planned.length} 单${skipped.length ? `，${skipped.length} 项跳过` : ''}）`}>
        <Table size="small" pagination={false} rowKey="key" dataSource={planRows}
          locale={{ emptyText: '无调仓计划' }}
          columns={[
            { title: '标的', dataIndex: 'symbol', render: (_: unknown, r: PlanRow) => label(r.symbol) },
            { title: '动作', width: 90, render: (_: unknown, r: PlanRow) =>
              r.skipped ? <Tag>跳过</Tag> : <Tag color={r.side === 'buy' ? 'success' : 'error'}>{r.side === 'buy' ? '买入' : '卖出'}</Tag> },
            { title: '数量', dataIndex: 'quantity', width: 80, align: 'right', render: (v: number | null) => v ?? '-' },
            { title: '理由', dataIndex: 'reason', ellipsis: true, render: (v: string | undefined) => v || '-' },
          ]} />
      </Section>

      <Section title={`订单执行（${orders.length}）`}>
        <Table size="small" pagination={false} rowKey="id" dataSource={orders}
          locale={{ emptyText: '本次运行未生成订单' }}
          columns={[
            { title: '标的', dataIndex: 'symbol', render: (_: unknown, r: LiveStrategyOrder) => instrumentLabel(r.symbol, r.symbolName) },
            { title: '方向', dataIndex: 'side', width: 80, render: (v: string) => (
              <Tag color={v === 'buy' ? 'success' : 'error'}>{v === 'buy' ? '买入' : '卖出'}</Tag>
            ) },
            { title: '数量', dataIndex: 'quantity', width: 80, align: 'right' },
            { title: '状态', dataIndex: 'status', width: 90, render: (v: string) => {
              const meta = ORDER_STATUS_LABELS[v];
              return meta ? <Tag color={meta.color}>{meta.label}</Tag> : <Tag>{v}</Tag>;
            } },
            { title: '成交价', dataIndex: 'filledPrice', width: 90, align: 'right', render: (v: number | null | undefined) => v ?? '-' },
            { title: 'QMT单号', dataIndex: 'qmtOrderId', width: 110, ellipsis: true, render: (v: string | null | undefined) => v || '-' },
            { title: '完成时间', dataIndex: 'completedAt', width: 150, render: (v: string | null | undefined) => v || '-' },
          ]} />
      </Section>

      <Section title="风控检查">
        {riskChecks.length === 0 ? (
          <div className="text-xs text-secondary-text">无风控检查记录</div>
        ) : (
          <div className="flex flex-col gap-1 text-xs">
            <div>整体：<Tag color={risk.passed ? 'success' : 'error'}>{risk.passed ? '通过' : '未通过'}</Tag></div>
            {riskChecks.map((c, i) => (
              <div key={`${c.name}-${i}`} className="flex items-center gap-2">
                <Tag color={c.passed ? 'success' : 'error'}>{c.name}</Tag>
                <span className="text-secondary-text">{c.detail || (c.passed ? '通过' : '未通过')}</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="调仓批次">
        {batch ? (
          <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }}>
            <Descriptions.Item label="批次UID"><span className="text-xs">{batch.batchUid}</span></Descriptions.Item>
            <Descriptions.Item label="创建时间">{batch.createdAt || '-'}</Descriptions.Item>
            <Descriptions.Item label="订单进度">
              {progress ? `${progress.filled}/${progress.total} 已成交${progress.rejected ? `，${progress.rejected} 失败` : ''}` : batch.summary?.count ?? '-'}
            </Descriptions.Item>
          </Descriptions>
        ) : <div className="text-xs text-secondary-text">无关联批次</div>}
      </Section>
    </Card>
  );
};
