/** 可转债展示约定（行情列表 / 实盘持仓共用）。

 * 同一语义只保留一份实现：条款倒计 Tag、剩余年限、最后交易日兜底、
 * 临近关键日期的分级突显。数值格式化跟随各自页面精度需求。
 */
import { Tag } from 'antd';

export const formatNumber = (value?: number | null, digits = 2): string => (value == null ? '--' : value.toFixed(digits));

export const formatPct = (value?: number | null): string => (value == null ? '--' : `${value.toFixed(2)}%`);

/** 转债现价/成本价：行情本身 3 位小数（如 117.891） */
export const formatPrice3 = (value?: number | null): string => (value == null ? '--' : value.toFixed(3));

/** 剩余年限（年，调用方格式化）；已到期为负数，缺失返回 null */
export const yearsToMaturity = (maturityDate?: string | null): number | null => {
  if (!maturityDate) return null;
  const target = new Date(`${maturityDate}T00:00:00`);
  if (Number.isNaN(target.getTime())) return null;
  return (target.getTime() - Date.now()) / (365 * 24 * 3600 * 1000);
};

/** 条款计数取首个 "N/M" 片段（真实文本如「10/15 | 30 2026-08-10 重新计」→ 10/15）；
 * 无计数形态（已公告强赎、暂不强赎等）返回 null */
export const parseCountdownRatio = (value?: string | null): number | null => {
  if (!value) return null;
  const match = value.match(/(\d+)\s*\/\s*(\d+)/);
  if (!match) return null;
  const denominator = Number(match[2]);
  return denominator > 0 ? Number(match[1]) / denominator : null;
};

const MISSING = <span className="text-secondary-text">--</span>;

/** 条款倒计 Tag：有 N/M 计数时只显示 "N/M"（原始文本如「至少还需15天 ( 30天内
 * 0/15 2025-04-16 重新计)」压缩成「0/15」）；已公告红、计数达 60% 以上橙；
 * 暂不强赎/存续期不强赎等无计数状态显示提炼后的短文本 */
export const countdownTag = (value?: string | null) => {
  if (!value) return MISSING;
  if (value.includes('已公告')) return <Tag color="error">{value}</Tag>;
  const match = value.match(/(\d+)\s*\/\s*(\d+)/);
  if (match) {
    const compact = `${match[1]}/${match[2]}`;
    const ratio = parseCountdownRatio(value);
    return ratio != null && ratio >= 0.6
      ? <Tag color="warning">{compact}</Tag>
      : <Tag>{compact}</Tag>;
  }
  if (value.includes('暂不强赎')) return <Tag>暂不强赎</Tag>;
  if (value.includes('存续期内不强赎')) return <Tag>存续期不强赎</Tag>;
  return <Tag>{value}</Tag>;
};

export const exitDateCell = (lastTradingDate?: string | null, maturityDate?: string | null) => {
  if (lastTradingDate) return lastTradingDate;
  if (maturityDate) return <span className="text-secondary-text">{maturityDate}</span>;
  return MISSING;
};

/** 最后交易日 + 剩余交易日倒计：≤5 红、≤15 橙、其余普通文本；缺失用到期日弱色兜底 */
export const proximityDateCell = (
  lastTradingDate?: string | null,
  remainingDays?: number | null,
  maturityDate?: string | null,
) => {
  if (!lastTradingDate) return exitDateCell(lastTradingDate, maturityDate);
  if (remainingDays == null) return <span>{lastTradingDate}</span>;
  if (remainingDays <= 5) return <Tag color="error">{lastTradingDate}（剩{remainingDays}日）</Tag>;
  if (remainingDays <= 15) return <Tag color="warning">{lastTradingDate}（剩{remainingDays}日）</Tag>;
  return <span>{lastTradingDate}（剩{remainingDays}日）</span>;
};
