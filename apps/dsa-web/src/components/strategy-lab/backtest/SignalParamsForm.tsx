import { Button, Input, Select, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Plus, Trash2 } from 'lucide-react';
import type { StrategyLabFactorMeta } from '../../../api/strategyLab';
import { SL_INPUT_CLASS } from '../utils';
import type { SignalFormState, SignalRuleRow } from './signalForm';

const OP_OPTIONS = [
  { label: '>', value: '>' },
  { label: '≥', value: '>=' },
  { label: '<', value: '<' },
  { label: '≤', value: '<=' },
  { label: '=', value: '==' },
];

type Props = {
  value: SignalFormState;
  onChange: (next: SignalFormState) => void;
  factors: StrategyLabFactorMeta[];
};

/** signal（ETF 时序信号）策略参数表单：均线窗口 / 进出场规则表 / 资金与费用。 */
export const SignalParamsForm: React.FC<Props> = ({ value, onChange, factors }) => {
  const factorOptions = (factors.length ? factors : [{ factor: 'ma_gap_pct', label: '均线乖离率' } as StrategyLabFactorMeta]).map(
    (factor) => ({ label: factor.label || factor.factor, value: factor.factor }),
  );
  const usesDividendYield = [...value.entryRules, ...value.exitRules].some((row) => row.factor === 'dividend_yield');
  const usesRocPct = [...value.entryRules, ...value.exitRules].some((row) => row.factor === 'roc_pct');
  const usesMaGap = [...value.entryRules, ...value.exitRules].some((row) => row.factor === 'ma_gap_pct');
  const usesMacd = [...value.entryRules, ...value.exitRules].some((row) => row.factor.startsWith('macd_'));

  const patch = (partial: Partial<SignalFormState>) => onChange({ ...value, ...partial });

  const patchRule = (key: 'entryRules' | 'exitRules', index: number, partial: Partial<SignalRuleRow>) => {
    patch({ [key]: value[key].map((row, i) => (i === index ? { ...row, ...partial } : row)) } as Partial<SignalFormState>);
  };

  const ruleColumns = (key: 'entryRules' | 'exitRules', ariaPrefix: string): ColumnsType<SignalRuleRow> => [
    {
      title: '因子',
      dataIndex: 'factor',
      width: 200,
      render: (_, record, index) => (
        <Select
          aria-label={`${ariaPrefix}-因子-${index}`}
          size="small"
          className="w-full"
          value={record.factor || undefined}
          options={factorOptions}
          onChange={(factor) => patchRule(key, index, { factor })}
          placeholder="选择因子"
        />
      ),
    },
    {
      title: '比较符',
      dataIndex: 'op',
      width: 90,
      render: (_, record, index) => (
        <Select
          aria-label={`${ariaPrefix}-比较符-${index}`}
          size="small"
          className="w-full"
          value={record.op}
          options={OP_OPTIONS}
          onChange={(op) => patchRule(key, index, { op })}
        />
      ),
    },
    {
      title: '值',
      dataIndex: 'value',
      width: 120,
      render: (_, record, index) => (
        <Input
          aria-label={`${ariaPrefix}-值-${index}`}
          size="small"
          className="w-full"
          value={record.value}
          onChange={(event) => patchRule(key, index, { value: event.target.value })}
          placeholder="数值"
        />
      ),
    },
    {
      title: '',
      key: 'actions',
      width: 44,
      render: (_, __, index) => (
        <Button
          type="text"
          size="small"
          aria-label={`${ariaPrefix}-删除-${index}`}
          icon={<Trash2 className="h-3.5 w-3.5" />}
          onClick={() => patch({ [key]: value[key].filter((_, i) => i !== index) } as Partial<SignalFormState>)}
        />
      ),
    },
  ];

  return (
    <div className="mt-3 grid gap-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {usesMaGap ? (
          <label className="text-sm">
            均线窗口(日)
            <input
              aria-label="均线窗口"
              className={`${SL_INPUT_CLASS} mt-1`}
              type="number"
              min="2"
              value={value.maWindow}
              onChange={(event) => patch({ maWindow: event.target.value })}
            />
          </label>
        ) : null}
        {usesRocPct ? (
          <label className="text-sm">
            涨跌幅窗口(日)
            <input
              aria-label="涨跌幅窗口"
              className={`${SL_INPUT_CLASS} mt-1`}
              type="number"
              min="1"
              value={value.rocWindow}
              onChange={(event) => patch({ rocWindow: event.target.value })}
            />
          </label>
        ) : null}
        {usesMacd ? (
          <>
            <label className="text-sm">
              MACD 快线EMA(日)
              <input
                aria-label="MACD快线"
                className={`${SL_INPUT_CLASS} mt-1`}
                type="number"
                min="2"
                value={value.macdFast}
                onChange={(event) => patch({ macdFast: event.target.value })}
              />
            </label>
            <label className="text-sm">
              MACD 慢线EMA(日)
              <input
                aria-label="MACD慢线"
                className={`${SL_INPUT_CLASS} mt-1`}
                type="number"
                min="3"
                value={value.macdSlow}
                onChange={(event) => patch({ macdSlow: event.target.value })}
              />
            </label>
            <label className="text-sm">
              MACD 信号线EMA(日)
              <input
                aria-label="MACD信号线"
                className={`${SL_INPUT_CLASS} mt-1`}
                type="number"
                min="2"
                value={value.macdSignal}
                onChange={(event) => patch({ macdSignal: event.target.value })}
              />
            </label>
          </>
        ) : null}
        <label className="text-sm">
          单标的最大仓位(%)
          <input
            aria-label="单标的最大仓位"
            className={`${SL_INPUT_CLASS} mt-1`}
            type="number"
            min="1"
            max="100"
            value={value.maxPositionPct}
            onChange={(event) => patch({ maxPositionPct: event.target.value })}
          />
        </label>
        <label className="text-sm">
          单边手续费(‰)
          <input
            aria-label="单边手续费"
            className={`${SL_INPUT_CLASS} mt-1`}
            type="number"
            min="0"
            step="0.01"
            value={value.commissionPermille}
            onChange={(event) => patch({ commissionPermille: event.target.value })}
          />
        </label>
        <label className="text-sm">
          最小交易单位(股)
          <input
            aria-label="最小交易单位"
            className={`${SL_INPUT_CLASS} mt-1`}
            type="number"
            min="1"
            value={value.lotSize}
            onChange={(event) => patch({ lotSize: event.target.value })}
          />
        </label>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">
            买入条件
            <span className="ml-2 text-xs font-normal text-secondary-text">全部满足才买入</span>
          </h3>
          <Button
            size="small"
            icon={<Plus className="h-3.5 w-3.5" />}
            onClick={() => patch({ entryRules: [...value.entryRules, { factor: '', op: '<', value: '' }] })}
          >
            添加条件
          </Button>
        </div>
        <Table
          rowKey={(_, index) => `entry-${index}`}
          size="small"
          columns={ruleColumns('entryRules', '买入条件')}
          dataSource={value.entryRules}
          pagination={false}
          locale={{ emptyText: '暂无数据（至少一条买入条件）' }}
        />
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">
            卖出条件
            <span className="ml-2 text-xs font-normal text-secondary-text">任一满足即全仓卖出</span>
          </h3>
          <Button
            size="small"
            icon={<Plus className="h-3.5 w-3.5" />}
            onClick={() => patch({ exitRules: [...value.exitRules, { factor: '', op: '>', value: '' }] })}
          >
            添加条件
          </Button>
        </div>
        <Table
          rowKey={(_, index) => `exit-${index}`}
          size="small"
          columns={ruleColumns('exitRules', '卖出条件')}
          dataSource={value.exitRules}
          pagination={false}
          locale={{ emptyText: '暂无数据（至少一条卖出条件）' }}
        />
      </div>

      {usesDividendYield ? (
        <div className="flex items-center gap-2 text-xs text-warning">
          <Tag className="m-0" color="orange">股息率数据未同步</Tag>
          <span>当前版本未接入股息率数据源，引用该因子的条件不会触发（回测将持续空仓，属预期行为）</span>
        </div>
      ) : null}
    </div>
  );
};
