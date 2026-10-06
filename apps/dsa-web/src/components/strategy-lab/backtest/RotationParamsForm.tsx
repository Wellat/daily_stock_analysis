import { Button, Input, InputNumber, Select, Switch, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Plus, Trash2 } from 'lucide-react';
import type {
  StrategyLabFactorMeta,
  StrategyLabScorePreset,
} from '../../../api/strategyLab';
import { SL_INPUT_CLASS } from '../utils';
import type { ExclusionFactorRow, RotationFormState, ScoreFactorRow } from './rotationForm';

const REBALANCE_UNIT_OPTIONS = [
  { label: '按交易日', value: 'trading_day' },
  { label: '按周', value: 'week' },
  { label: '按月', value: 'month' },
];

const DIRECTION_OPTIONS = [
  { label: '越小越优', value: 'asc' },
  { label: '越大越优', value: 'desc' },
];

const OP_OPTIONS = [
  { label: '>', value: '>' },
  { label: '≥', value: '>=' },
  { label: '<', value: '<' },
  { label: '≤', value: '<=' },
  { label: '=', value: '==' },
];

type Props = {
  value: RotationFormState;
  onChange: (next: RotationFormState) => void;
  factors: StrategyLabFactorMeta[];
  scorePresets: StrategyLabScorePreset[];
};

/** rotation 策略参数表单：换仓节奏 / 持有区间 / 打分因子表 / 排除因子表 / 风控开关。 */
export const RotationParamsForm: React.FC<Props> = ({ value, onChange, factors, scorePresets }) => {
  const factorOptions = (factors.length ? factors : [{ factor: 'price', label: '转债价格' } as StrategyLabFactorMeta]).map(
    (factor) => ({ label: factor.label || factor.factor, value: factor.factor }),
  );
  const factorLabel = (factorId: string) => factorOptions.find((option) => option.value === factorId)?.label ?? factorId;

  const patch = (partial: Partial<RotationFormState>) => onChange({ ...value, ...partial });

  const applyPreset = (presetId: string) => {
    const preset = scorePresets.find((item) => item.preset === presetId);
    patch({
      scorePreset: presetId,
      scoreFactors: preset
        ? preset.factors.map((row) => ({ factor: row.factor, direction: row.direction === 'desc' ? 'desc' : 'asc', weight: String(row.weight) }))
        : value.scoreFactors,
    });
  };

  const patchScoreRow = (index: number, partial: Partial<ScoreFactorRow>) => {
    patch({ scoreFactors: value.scoreFactors.map((row, i) => (i === index ? { ...row, ...partial } : row)) });
  };
  const patchExclusionRow = (index: number, partial: Partial<ExclusionFactorRow>) => {
    patch({ exclusionFactors: value.exclusionFactors.map((row, i) => (i === index ? { ...row, ...partial } : row)) });
  };

  const scoreColumns: ColumnsType<ScoreFactorRow> = [
    {
      title: '因子',
      dataIndex: 'factor',
      width: 180,
      render: (_, record, index) => (
        <Select
          aria-label={`打分因子-${index}`}
          size="small"
          className="w-full"
          value={record.factor || undefined}
          options={factorOptions}
          onChange={(factor) => patchScoreRow(index, { factor })}
          placeholder="选择因子"
        />
      ),
    },
    {
      title: '方向偏好',
      dataIndex: 'direction',
      width: 130,
      render: (_, record, index) => (
        <Select
          aria-label={`因子方向-${index}`}
          size="small"
          className="w-full"
          value={record.direction}
          options={DIRECTION_OPTIONS}
          onChange={(direction) => patchScoreRow(index, { direction: direction as 'asc' | 'desc' })}
        />
      ),
    },
    {
      title: '权重',
      dataIndex: 'weight',
      width: 100,
      render: (_, record, index) => (
        <InputNumber
          aria-label={`因子权重-${index}`}
          size="small"
          className="w-full"
          min={0}
          step={0.1}
          value={Number(record.weight) || 0}
          onChange={(weight) => patchScoreRow(index, { weight: weight == null ? '' : String(weight) })}
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
          aria-label={`删除打分因子-${index}`}
          icon={<Trash2 className="h-3.5 w-3.5" />}
          onClick={() => patch({ scoreFactors: value.scoreFactors.filter((_, i) => i !== index) })}
        />
      ),
    },
  ];

  const exclusionColumns: ColumnsType<ExclusionFactorRow> = [
    {
      title: '因子',
      dataIndex: 'factor',
      width: 160,
      render: (_, record, index) => (
        <Select
          aria-label={`排除因子-${index}`}
          size="small"
          className="w-full"
          value={record.factor || undefined}
          options={factorOptions}
          onChange={(factor) => patchExclusionRow(index, { factor })}
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
          aria-label={`排除比较符-${index}`}
          size="small"
          className="w-full"
          value={record.op}
          options={OP_OPTIONS}
          onChange={(op) => patchExclusionRow(index, { op })}
        />
      ),
    },
    {
      title: '值',
      dataIndex: 'value',
      width: 110,
      render: (_, record, index) => (
        <Input
          aria-label={`排除值-${index}`}
          size="small"
          className="w-full"
          value={record.value}
          onChange={(event) => patchExclusionRow(index, { value: event.target.value })}
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
          aria-label={`删除排除因子-${index}`}
          icon={<Trash2 className="h-3.5 w-3.5" />}
          onClick={() => patch({ exclusionFactors: value.exclusionFactors.filter((_, i) => i !== index) })}
        />
      ),
    },
  ];

  return (
    <div className="mt-3 grid gap-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-sm">
          换仓频率类型
          <Select
            aria-label="换仓频率类型"
            className="mt-1 w-full"
            value={value.rebalanceUnit}
            options={REBALANCE_UNIT_OPTIONS}
            onChange={(unit) => patch({ rebalanceUnit: unit as RotationFormState['rebalanceUnit'] })}
          />
        </label>
        <label className="text-sm">
          换仓频率
          <input
            aria-label="换仓频率"
            className={`${SL_INPUT_CLASS} mt-1`}
            type="number"
            min="1"
            value={value.rebalanceInterval}
            onChange={(event) => patch({ rebalanceInterval: event.target.value })}
          />
        </label>
        <label className="text-sm">
          最小持有数量
          <input
            aria-label="最小持有数量"
            className={`${SL_INPUT_CLASS} mt-1`}
            type="number"
            min="1"
            value={value.minPositions}
            onChange={(event) => patch({ minPositions: event.target.value })}
          />
        </label>
        <label className="text-sm">
          最大持有数量
          <input
            aria-label="最大持有数量"
            className={`${SL_INPUT_CLASS} mt-1`}
            type="number"
            min="1"
            value={value.maxPositions}
            onChange={(event) => patch({ maxPositions: event.target.value })}
          />
        </label>
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
          最小交易单位(张)
          <input
            aria-label="最小交易单位"
            className={`${SL_INPUT_CLASS} mt-1`}
            type="number"
            min="1"
            value={value.lotSize}
            onChange={(event) => patch({ lotSize: event.target.value })}
          />
        </label>
        <label className="text-sm">
          因子缺失处理
          <Select
            aria-label="因子缺失处理"
            className="mt-1 w-full"
            value={value.scoreMissing}
            options={[
              { label: '跳过该标的', value: 'skip' },
              { label: '按中性值计', value: 'neutral' },
            ]}
            onChange={(scoreMissing) => patch({ scoreMissing: scoreMissing as RotationFormState['scoreMissing'] })}
          />
        </label>
      </div>

      <div>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-foreground">打分因子</h3>
          <div className="flex items-center gap-2">
            <Select
              aria-label="打分预设"
              size="small"
              className="min-w-32"
              value={value.scorePreset}
              options={(scorePresets.length ? scorePresets : [{ preset: 'double_low', label: '双低', factors: [] }]).map((preset) => ({
                label: preset.label,
                value: preset.preset,
              }))}
              onChange={applyPreset}
            />
            <Button
              size="small"
              icon={<Plus className="h-3.5 w-3.5" />}
              onClick={() => patch({ scoreFactors: [...value.scoreFactors, { factor: '', direction: 'asc', weight: '1' }] })}
            >
              添加因子
            </Button>
          </div>
        </div>
        <Table
          rowKey={(_, index) => `score-${index}`}
          size="small"
          columns={scoreColumns}
          dataSource={value.scoreFactors}
          pagination={false}
          locale={{ emptyText: '暂无数据（至少一个打分因子）' }}
        />
        <p className="mt-1 text-xs text-secondary-text">
          加权合成打分，分数越小越优先买入；{value.scoreFactors.filter((row) => row.factor).map((row) => `${factorLabel(row.factor)}×${row.weight || 0}`).join(' + ') || '未配置'}
        </p>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">排除因子（高级）</h3>
          <Button
            size="small"
            icon={<Plus className="h-3.5 w-3.5" />}
            onClick={() => patch({ exclusionFactors: [...value.exclusionFactors, { factor: '', op: '<=', value: '' }] })}
          >
            添加排除
          </Button>
        </div>
        <Table
          rowKey={(_, index) => `exclusion-${index}`}
          size="small"
          columns={exclusionColumns}
          dataSource={value.exclusionFactors}
          pagination={false}
          locale={{ emptyText: '暂无数据' }}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-sm">
          排除新债天数
          <input
            aria-label="排除新债天数"
            className={`${SL_INPUT_CLASS} mt-1`}
            type="number"
            min="0"
            value={value.excludeNewBondDays}
            onChange={(event) => patch({ excludeNewBondDays: event.target.value })}
          />
        </label>
        <label className="text-sm">
          排除临近到期天数
          <input
            aria-label="排除临近到期天数"
            className={`${SL_INPUT_CLASS} mt-1`}
            type="number"
            min="0"
            value={value.excludeLastTradingDays}
            onChange={(event) => patch({ excludeLastTradingDays: event.target.value })}
          />
        </label>
        <div className="flex items-end gap-2 text-sm">
          <Switch
            aria-label="排除风险事件"
            size="small"
            checked={value.excludeEventBlocked}
            onChange={(checked) => patch({ excludeEventBlocked: checked })}
          />
          <span>排除风险事件</span>
          <Tag className="m-0" color="orange">强赎/下修/回售</Tag>
        </div>
        <div className="flex items-end gap-2 text-sm">
          <Switch
            aria-label="换仓日再平衡"
            size="small"
            checked={value.rebalanceWeights}
            onChange={(checked) => patch({ rebalanceWeights: checked })}
          />
          <span>换仓日再平衡权重</span>
        </div>
      </div>
      <label className="block text-sm">
        排除指定转债
        <input
          aria-label="排除指定转债"
          className={`${SL_INPUT_CLASS} mt-1`}
          value={value.excludedSymbols}
          onChange={(event) => patch({ excludedSymbols: event.target.value })}
          placeholder="逗号分隔转债代码，留空不排除"
        />
      </label>
    </div>
  );
};
