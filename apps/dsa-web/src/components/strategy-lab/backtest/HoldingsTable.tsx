import { Table, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMemo } from 'react';
import type { StrategyLabEquityPoint, StrategyLabRunItem } from '../../../api/strategyLab';
import { formatNumber, formatPct } from '../utils';

type HoldingsRow = StrategyLabEquityPoint & { key: string; cumulative_return_pct: number | null };

/** 逐日持仓明细：交易日/持有标的/数量/换手率/当日涨跌/累计收益率/账户资产。 */
export const HoldingsTable: React.FC<{ run: StrategyLabRunItem }> = ({ run }) => {
  const rows = useMemo<HoldingsRow[]>(() => {
    const initial = run.initial_cash || 1;
    return [...(run.equity_curve ?? [])]
      .map((point, index) => ({
        ...point,
        key: `${point.trade_date}-${index}`,
        cumulative_return_pct: (point.equity / initial - 1) * 100,
      }))
      .reverse(); // 最新在前
  }, [run]);

  const columns: ColumnsType<HoldingsRow> = [
    { title: '交易日', dataIndex: 'trade_date', width: 120, fixed: 'left' },
    {
      title: '持有标的',
      dataIndex: 'holdings',
      render: (_, record) => {
        const holdings = record.holdings ?? [];
        if (!holdings.length) return <span className="text-secondary-text">--</span>;
        const summary = holdings.join('、');
        return (
          <Tooltip title={summary}>
            <span className="block max-w-md truncate">{summary}</span>
          </Tooltip>
        );
      },
    },
    { title: '持有数量', dataIndex: 'holdings_count', width: 90, align: 'right', render: (value: number | null, record) => (value ?? record.holdings?.length ?? 0) },
    { title: '换手率', dataIndex: 'turnover_pct', width: 90, align: 'right', render: (value: number | null) => (value == null ? '--' : formatPct(value)) },
    {
      title: '当日涨跌',
      dataIndex: 'daily_return_pct',
      width: 90,
      align: 'right',
      render: (value: number | null) => (
        <span className={value == null ? 'text-secondary-text' : value > 0 ? 'text-red-500' : value < 0 ? 'text-green-600' : ''}>
          {formatPct(value)}
        </span>
      ),
    },
    { title: '累计收益率', dataIndex: 'cumulative_return_pct', width: 100, align: 'right', render: (value: number | null) => formatPct(value) },
    { title: '账户资产', dataIndex: 'equity', width: 110, align: 'right', render: (value: number) => formatNumber(value, 2) },
  ];

  if (!rows.length) {
    return <div className="text-sm text-secondary-text">该记录为旧版引擎结果，无逐日持仓明细</div>;
  }
  return (
    <Table
      rowKey="key"
      size="small"
      columns={columns}
      dataSource={rows}
      pagination={{ pageSize: 15, showSizeChanger: false, simple: true }}
      scroll={{ x: 900 }}
    />
  );
};
