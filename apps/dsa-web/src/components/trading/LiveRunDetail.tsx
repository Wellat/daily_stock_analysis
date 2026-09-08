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

type Props = {
  run: LiveStrategyRun;
  decisions: LiveStrategyDecision[];
  orders: LiveStrategyOrder[];
  batch?: LiveStrategyBatch | null;
  loading?: boolean;
  onClose: () => void;
};

/**单次实盘运行的完整产出：概要、目标组合、策略决策、订单执行、风控诊断、批次信息。*/
export const LiveRunDetail: React.FC<Props> = ({ run, decisions, orders, batch, loading, onClose }) => {
  const targetRows = Object.values(run.target ?? {});
  const risk = run.risk ?? {};
  const riskChecks = risk.riskChecks ?? [];
  const skipped = risk.skipped ?? [];
  const progress = batch?.orders;
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
      </Descriptions>
      {run.status === 'failed' && run.errorMessage && (
        <div className="mt-1 text-xs text-red-600">失败原因：{run.errorMessage}</div>
      )}
      {run.skipReason && <div className="mt-1 text-xs text-secondary-text">跳过原因：{run.skipReason}</div>}

      <Section title="目标组合">
        <Table size="small" pagination={false} rowKey="symbol" dataSource={targetRows}
          locale={{ emptyText: '本次运行未产生目标组合（如事件检查模式）' }}
          columns={[
            { title: '标的', dataIndex: 'symbol', render: (_: unknown, r: { symbol: string; symbolName?: string }) => instrumentLabel(r.symbol, r.symbolName) },
            { title: '最新价', dataIndex: 'price', render: (v: number | null | undefined) => (v ?? '-'), align: 'right' },
            { title: '目标数量', dataIndex: 'quantity', align: 'right' },
          ]} />
      </Section>

      <Section title={`策略决策（${decisions.length}）`}>
        <Table size="small" pagination={false} rowKey="id" dataSource={decisions}
          columns={[
            { title: '标的', dataIndex: 'symbol', render: (_: unknown, r: LiveStrategyDecision) => instrumentLabel(r.symbol, r.symbolName) },
            { title: '动作', dataIndex: 'action', width: 90, render: (v: string) => {
              const meta = ACTION_LABELS[v];
              return meta ? <Tag color={meta.color}>{meta.label}</Tag> : <Tag>{v}</Tag>;
            } },
            { title: '数量', dataIndex: 'suggestedQuantity', width: 80, align: 'right', render: (v: number | null | undefined) => v ?? '-' },
            { title: '溢价率', width: 90, align: 'right', render: (_: unknown, r: LiveStrategyDecision) => {
              const premium = r.decisionData?.premiumRate;
              return typeof premium === 'number' ? `${premium.toFixed(2)}%` : '-';
            } },
            { title: '排名', dataIndex: ['decisionData', 'rank'], width: 70, align: 'right', render: (v: unknown) => (typeof v === 'number' ? `#${v}` : '-') },
            { title: '理由', dataIndex: 'reason', ellipsis: true },
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

      <Section title="风控与诊断">
        {riskChecks.length === 0 && skipped.length === 0 ? (
          <div className="text-xs text-secondary-text">无诊断信息</div>
        ) : (
          <div className="flex flex-col gap-1 text-xs">
            <div>整体：<Tag color={risk.passed ? 'success' : 'error'}>{risk.passed ? '通过' : '未通过'}</Tag></div>
            {riskChecks.map((c, i) => (
              <div key={`${c.name}-${i}`} className="flex items-center gap-2">
                <Tag color={c.passed ? 'success' : 'error'}>{c.name}</Tag>
                <span className="text-secondary-text">{c.detail || (c.passed ? '通过' : '未通过')}</span>
              </div>
            ))}
            {skipped.map((s, i) => (
              <div key={`${s.symbol}-${i}`} className="text-secondary-text">跳过 {s.symbol || '-'}：{s.reason}</div>
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
