import { Tag, Tooltip } from 'antd';

const MODE_LABELS: Record<string, { label: string; color: string; tip: string }> = {
  rebalance: { label: '调仓', color: 'blue', tip: '按调仓节奏轮动：全市场排序选债并重构目标组合' },
  event_check: { label: '事件检查', color: 'orange', tip: '盘中风险事件扫描：只处理持仓的强赎/下修/回售事件，不买入' },
};

const SKIP_REASON_LABELS: Record<string, string> = {
  intraday_sync_unavailable: '盘中数据同步未完成，暂不能生成调仓',
  rebalance_frequency: '未到调仓日，本次不生成调仓',
};

export const runStatusTag = (status?: string) => {
  const color = status === 'completed' ? 'success' : status === 'failed' ? 'error' : 'processing';
  const label = status === 'completed' ? '已完成' : status === 'failed' ? '失败' : status === 'running' ? '运行中' : status || '-';
  return <Tag color={color}>{label}</Tag>;
};

export const runModeTag = (mode?: string | null) => {
  const meta = mode ? MODE_LABELS[mode] : undefined;
  if (!meta) return <Tag>{mode || '-'}</Tag>;
  return <Tooltip title={meta.tip}><Tag color={meta.color}>{meta.label}</Tag></Tooltip>;
};

export const skipReasonLabel = (reason?: string | null) => {
  if (!reason) return '';
  return SKIP_REASON_LABELS[reason] ?? reason;
};
