import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Select, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { Check, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { weeklyReviewApi } from '../../api/portfolio';
import { ApiErrorAlert, EmptyState, InlineAlert, Loading, StatCard } from '../common';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { WEEKLY_REVIEW_TEXT } from '../../locales/featureText';
import type {
  WeeklyReviewAttributionItem,
  WeeklyReviewCbHeld,
  WeeklyReviewDetail,
  WeeklyReviewListItem,
  WeeklyReviewPlanItem,
  WeeklyReviewPositionTierItem,
  WeeklyReviewRealizedEvent,
  WeeklyReviewSignalItem,
} from '../../types/portfolio';

const INPUT_CLASS = 'input-surface input-focus-glow h-10 w-full rounded-xl border bg-transparent px-3 text-sm';
const TEXTAREA_CLASS = 'input-surface input-focus-glow w-full rounded-xl border bg-transparent px-3 py-2 text-sm';

function formatMoney(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return '-';
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

function formatSignedMoney(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return '-';
  const sign = value > 0 ? '+' : '';
  return `${sign}${formatMoney(value)}`;
}

function formatPct(value?: number | null, digits = 2): string {
  if (value == null || Number.isNaN(value)) return '-';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

function pnlClass(value?: number | null): string {
  if (value == null || Number.isNaN(value) || value === 0) return 'text-secondary-text';
  return value > 0 ? 'text-success' : 'text-danger';
}

const TIER_COLORS: Record<string, string> = {
  deep_loss: 'red',
  loss: 'orange',
  profit: 'green',
  unknown: 'default',
};

const SEVERITY_COLORS: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'blue',
};

const ACTION_COLORS: Record<string, string> = {
  add: 'green',
  reduce: 'orange',
  clear: 'red',
  watch: 'blue',
};

/** 周度持仓复盘：自动区（总览/归因/结构/分层/转债/信号/风险）+ 人工区（手记/计划）。 */
export const WeeklyReviewTab: React.FC = () => {
  const { language } = useUiLanguage();
  const text = WEEKLY_REVIEW_TEXT[language];

  const [reviews, setReviews] = useState<WeeklyReviewListItem[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<ParsedApiError | null>(null);
  const [selectedPeriod, setSelectedPeriod] = useState<string>('');
  const [detail, setDetail] = useState<WeeklyReviewDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<ParsedApiError | null>(null);
  const [generating, setGenerating] = useState(false);

  const [noteDraft, setNoteDraft] = useState('');
  const [noteSaving, setNoteSaving] = useState(false);
  const [noteSavedAt, setNoteSavedAt] = useState<string>('');
  const [planAction, setPlanAction] = useState('watch');
  const [planSymbol, setPlanSymbol] = useState('');
  const [planContent, setPlanContent] = useState('');
  const [planBusy, setPlanBusy] = useState(false);

  const loadReviews = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    try {
      const result = await weeklyReviewApi.getReviews({ page: 1, pageSize: 50 });
      setReviews(result.items);
      setSelectedPeriod((current) => current || result.items[0]?.periodStart || '');
    } catch (err) {
      setListError(getParsedApiError(err));
    } finally {
      setListLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (periodStart: string) => {
    if (!periodStart) return;
    setDetailLoading(true);
    setDetailError(null);
    try {
      const result = await weeklyReviewApi.getReviewDetail(periodStart);
      setDetail(result);
      setNoteDraft(result.weeklyNote || '');
      setNoteSavedAt('');
    } catch (err) {
      setDetailError(getParsedApiError(err));
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadReviews();
  }, [loadReviews]);

  useEffect(() => {
    if (selectedPeriod) {
      void loadDetail(selectedPeriod);
    } else {
      setDetail(null);
    }
  }, [selectedPeriod, loadDetail]);

  const generate = useCallback(async () => {
    setGenerating(true);
    try {
      await weeklyReviewApi.generateReview({ push: true });
      await loadReviews();
    } catch (err) {
      setListError(getParsedApiError(err));
    } finally {
      setGenerating(false);
    }
  }, [loadReviews]);

  const saveNote = useCallback(async () => {
    if (!detail) return;
    setNoteSaving(true);
    try {
      const updated = await weeklyReviewApi.updateWeeklyNote(detail.periodStart, noteDraft);
      setDetail(updated);
      setNoteSavedAt(new Date().toLocaleTimeString());
    } catch (err) {
      setDetailError(getParsedApiError(err));
    } finally {
      setNoteSaving(false);
    }
  }, [detail, noteDraft]);

  const addPlan = useCallback(async () => {
    if (!detail || !planContent.trim()) return;
    setPlanBusy(true);
    try {
      await weeklyReviewApi.createPlan(detail.periodStart, {
        action: planAction,
        content: planContent.trim(),
        symbol: planSymbol.trim() || undefined,
      });
      setPlanContent('');
      setPlanSymbol('');
      await loadDetail(detail.periodStart);
      await loadReviews();
    } catch (err) {
      setDetailError(getParsedApiError(err));
    } finally {
      setPlanBusy(false);
    }
  }, [detail, planAction, planContent, planSymbol, loadDetail, loadReviews]);

  const setPlanStatus = useCallback(
    async (plan: WeeklyReviewPlanItem, status: string) => {
      if (!detail) return;
      try {
        await weeklyReviewApi.updatePlan(detail.periodStart, plan.planId, { status });
        await loadDetail(detail.periodStart);
        await loadReviews();
      } catch (err) {
        setDetailError(getParsedApiError(err));
      }
    },
    [detail, loadDetail, loadReviews],
  );

  const removePlan = useCallback(
    async (plan: WeeklyReviewPlanItem) => {
      if (!detail) return;
      try {
        await weeklyReviewApi.deletePlan(detail.periodStart, plan.planId);
        await loadDetail(detail.periodStart);
        await loadReviews();
      } catch (err) {
        setDetailError(getParsedApiError(err));
      }
    },
    [detail, loadDetail, loadReviews],
  );

  const payload = detail?.payload;
  const summary = payload?.summary ?? {};

  const attributionColumns: ColumnsType<WeeklyReviewAttributionItem> = useMemo(
    () => [
      {
        title: text.symbol,
        key: 'symbol',
        render: (_, item) => (
          <div>
            <span className="font-medium text-foreground">{item.symbol}</span>
            {item.name ? <span className="ml-2 text-secondary-text">{item.name}</span> : null}
            {item.newPosition ? (
              <Tag className="ml-2" color="green">{text.newTag}</Tag>
            ) : null}
            {item.exitedPosition ? (
              <Tag className="ml-2" color="default">{text.exitTag}</Tag>
            ) : null}
          </div>
        ),
      },
      { title: text.mvStart, dataIndex: 'mvStart', key: 'mvStart', align: 'right', render: (v) => formatMoney(v) },
      { title: text.buyAmount, dataIndex: 'buyAmount', key: 'buyAmount', align: 'right', render: (v) => formatMoney(v) },
      { title: text.sellAmount, dataIndex: 'sellAmount', key: 'sellAmount', align: 'right', render: (v) => formatMoney(v) },
      { title: text.mvEnd, dataIndex: 'mvEnd', key: 'mvEnd', align: 'right', render: (v) => formatMoney(v) },
      { title: text.weekChange, dataIndex: 'weeklyPriceChangePct', key: 'weekChange', align: 'right', render: (v) => <span className={pnlClass(v)}>{formatPct(v)}</span> },
      {
        title: text.pnl,
        dataIndex: 'pnl',
        key: 'pnl',
        align: 'right',
        defaultSortOrder: 'descend',
        sorter: (a, b) => (a.pnl ?? 0) - (b.pnl ?? 0),
        render: (v) => <span className={`font-medium ${pnlClass(v)}`}>{formatSignedMoney(v)}</span>,
      },
    ],
    [text],
  );

  const tierColumns: ColumnsType<WeeklyReviewPositionTierItem> = useMemo(
    () => [
      {
        title: text.tier,
        dataIndex: 'tier',
        key: 'tier',
        width: 90,
        filters: [
          { text: text.tierDeepLoss, value: 'deep_loss' },
          { text: text.tierLoss, value: 'loss' },
          { text: text.tierProfit, value: 'profit' },
        ],
        onFilter: (value, record) => record.tier === value,
        render: (tier: string) => {
          const label =
            tier === 'deep_loss' ? text.tierDeepLoss : tier === 'loss' ? text.tierLoss : tier === 'profit' ? text.tierProfit : tier;
          return <Tag color={TIER_COLORS[tier] ?? 'default'}>{label}</Tag>;
        },
      },
      {
        title: text.symbol,
        key: 'symbol',
        render: (_, item) => (
          <div>
            <span className="font-medium text-foreground">{item.symbol}</span>
            {item.name ? <span className="ml-2 text-secondary-text">{item.name}</span> : null}
          </div>
        ),
      },
      { title: text.account, dataIndex: 'accountName', key: 'accountName' },
      { title: text.marketValue, dataIndex: 'marketValue', key: 'marketValue', align: 'right', render: (v) => formatMoney(v) },
      {
        title: text.weeklyReturnPct,
        dataIndex: 'pnlPct',
        key: 'pnlPct',
        align: 'right',
        sorter: (a, b) => (a.pnlPct ?? 0) - (b.pnlPct ?? 0),
        render: (v) => <span className={`font-medium ${pnlClass(v)}`}>{formatPct(v, 1)}</span>,
      },
      { title: text.holdingDays, dataIndex: 'holdingDays', key: 'holdingDays', align: 'right', render: (v, record) => (v == null ? '-' : `${v}${record.durationCapped ? `+ ${text.durationCapped}` : ''}`) },
    ],
    [text],
  );

  const cbColumns: ColumnsType<WeeklyReviewCbHeld> = useMemo(
    () => [
      {
        title: text.symbol,
        key: 'symbol',
        render: (_, item) => (
          <div>
            <span className="font-medium text-foreground">{item.symbol}</span>
            {item.name ? <span className="ml-2 text-secondary-text">{item.name}</span> : null}
          </div>
        ),
      },
      { title: text.closePrice, dataIndex: 'close', key: 'close', align: 'right', render: (v) => (v == null ? '-' : v.toFixed(2)) },
      { title: text.priceBand, dataIndex: 'priceBand', key: 'priceBand', align: 'right', render: (v) => v ?? '-' },
      { title: text.premiumRate, dataIndex: 'premiumRate', key: 'premiumRate', align: 'right', render: (v) => (v == null ? '-' : `${v.toFixed(2)}%`) },
      { title: text.daysToMaturity, dataIndex: 'daysToMaturity', key: 'daysToMaturity', align: 'right', render: (v, record) => (v == null ? '-' : <span className={record.maturityWarn ? 'text-warning font-medium' : ''}>{v}</span>) },
      { title: text.redeemCountdown, dataIndex: 'forceRedeemCountdown', key: 'forceRedeemCountdown', render: (v) => v || '-' },
    ],
    [text],
  );

  const realizedColumns: ColumnsType<WeeklyReviewRealizedEvent> = useMemo(
    () => [
      { title: text.tradeDate, dataIndex: 'tradeDate', key: 'tradeDate', width: 110 },
      { title: text.symbol, dataIndex: 'symbol', key: 'symbol' },
      { title: text.quantity, dataIndex: 'quantity', key: 'quantity', align: 'right', render: (v) => String(v) },
      {
        title: text.action,
        key: 'source',
        width: 80,
        render: (_, item) => <Tag color={item.auto ? 'cyan' : 'purple'}>{item.auto ? text.autoTag : text.manualTag}</Tag>,
      },
      {
        title: text.pnl,
        dataIndex: 'realized',
        key: 'realized',
        align: 'right',
        render: (v) => <span className={pnlClass(v)}>{formatSignedMoney(v)}</span>,
      },
    ],
    [text],
  );

  const signalColumns: ColumnsType<WeeklyReviewSignalItem> = useMemo(
    () => [
      {
        title: text.symbol,
        key: 'symbol',
        render: (_, item) => (
          <div>
            <span className="font-medium text-foreground">{item.stockCode}</span>
            {item.stockName ? <span className="ml-2 text-secondary-text">{item.stockName}</span> : null}
          </div>
        ),
      },
      { title: text.action, key: 'action', render: (_, item) => item.actionLabel || item.action },
      { title: text.tradeDate, dataIndex: 'createdDate', key: 'createdDate', width: 110, render: (v) => v ?? '-' },
      { title: text.validUntil, dataIndex: 'expiresDate', key: 'expiresDate', width: 110, render: (v) => v ?? '-' },
      {
        title: text.matched,
        key: 'matched',
        align: 'right',
        render: (_, item) => `${item.matchedBuys ?? 0}B / ${item.matchedSells ?? 0}S`,
      },
      {
        title: text.followed,
        key: 'followed',
        width: 90,
        render: (_, item) => (
          <Tag color={item.followed ? 'green' : 'orange'}>{item.followed ? text.followed : text.notFollowed}</Tag>
        ),
      },
    ],
    [text],
  );

  const planRow = (plan: WeeklyReviewPlanItem, readonly: boolean) => (
    <div key={plan.planId} className="flex items-start gap-2 py-2">
      <Tag color={ACTION_COLORS[plan.action] ?? 'default'} className="mt-0.5 shrink-0">{plan.action}</Tag>
      <div className="min-w-0 flex-1">
        <p className={`text-sm ${plan.status === 'done' ? 'text-secondary-text line-through' : 'text-foreground'}`}>
          {plan.symbol ? <span className="mr-1 font-medium">{plan.symbol}</span> : null}
          {plan.content}
        </p>
      </div>
      {!readonly ? (
        <div className="flex shrink-0 items-center gap-1">
          {plan.status === 'pending' ? (
            <>
              <button
                type="button"
                className="btn-secondary h-7 px-2 text-xs"
                title={text.markDone}
                onClick={() => void setPlanStatus(plan, 'done')}
              >
                <Check className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                className="btn-secondary h-7 px-2 text-xs"
                title={text.markSkipped}
                onClick={() => void setPlanStatus(plan, 'skipped')}
              >
                <X className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                className="btn-secondary h-7 px-2 text-xs"
                title={text.deletePlan}
                onClick={() => void removePlan(plan)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn-secondary h-7 px-2 text-xs"
              title={text.resetPending}
              onClick={() => void setPlanStatus(plan, 'pending')}
            >
              {text.resetPending}
            </button>
          )}
        </div>
      ) : (
        <Tag className="mt-0.5 shrink-0" color={plan.status === 'done' ? 'green' : 'default'}>
          {plan.status === 'done' ? text.planDone : plan.status === 'skipped' ? text.planSkipped : text.planPending}
        </Tag>
      )}
    </div>
  );

  const distributionBlock = (title: string, rows: { label: string; marketValue?: number | null; pct?: number | null }[]) => (
    <div className="min-w-0">
      <p className="mb-2 text-xs font-medium uppercase tracking-wider text-secondary-text">{title}</p>
      <div className="space-y-1.5">
        {rows.slice(0, 10).map((row) => (
          <div key={row.label} className="flex items-center justify-between gap-2 text-sm">
            <span className="truncate text-foreground">{row.label}</span>
            <span className="shrink-0 text-secondary-text">
              {formatMoney(row.marketValue)} · {formatPct(row.pct, 1)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={selectedPeriod || undefined}
            placeholder={text.pickWeek}
            className="min-w-48"
            loading={listLoading}
            onChange={(value) => setSelectedPeriod(value)}
            options={reviews.map((item) => ({
              value: item.periodStart,
              label: `${item.periodStart} ~ ${item.periodEnd}${item.status !== 'completed' ? ' ⚠' : ''}`,
            }))}
          />
          <button type="button" className="btn-secondary h-10 px-3 text-sm" disabled={listLoading} onClick={() => void loadReviews()}>
            <RefreshCw className="h-4 w-4" />
            {text.refresh}
          </button>
          <button type="button" className="btn-primary h-10 px-3 text-sm" disabled={generating} onClick={() => void generate()}>
            <Plus className="h-4 w-4" />
            {generating ? text.generating : text.generate}
          </button>
        </div>
        <p className="text-xs text-secondary-text">{text.subtitle}</p>
      </div>

      {listError ? <ApiErrorAlert error={listError} actionLabel={text.refresh} onAction={() => void loadReviews()} /> : null}
      {detailError && listError == null ? <ApiErrorAlert error={detailError} /> : null}

      {listLoading && reviews.length === 0 ? (
        <Loading />
      ) : reviews.length === 0 && !listError ? (
        <EmptyState title={text.emptyTitle} description={text.emptyDescription} />
      ) : detailLoading && detail == null ? (
        <Loading />
      ) : detail && payload ? (
        <div className="space-y-4">
          {detail.status !== 'completed' ? (
            <InlineAlert variant="warning" message={`${text.statusFailed}${detail.error ? `：${detail.error}` : ''}`} />
          ) : null}

          {/* 总览 */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <StatCard label={text.totalEquityEnd} value={formatMoney(payload.overview.total.equityEnd)} hint={`${payload.period.tradingDays.length} ${text.tradingDays}`} />
            <StatCard
              label={text.weeklyReturn}
              value={<span className={pnlClass(payload.overview.total.weeklyReturnAmount)}>{formatSignedMoney(payload.overview.total.weeklyReturnAmount)}</span>}
              hint={<span className={pnlClass(payload.overview.total.weeklyReturnPct)}>{formatPct(payload.overview.total.weeklyReturnPct)}</span>}
              tone={payload.overview.total.weeklyReturnAmount == null ? 'default' : payload.overview.total.weeklyReturnAmount >= 0 ? 'success' : 'danger'}
            />
            <StatCard label={text.cashRatio} value={formatPct(payload.overview.total.cashRatioPct, 1)} hint={formatMoney(payload.overview.total.cashEnd)} />
            <StatCard
              label={text.realizedWeek}
              value={<span className={pnlClass(payload.realized.weekRealizedTotal)}>{formatSignedMoney(payload.realized.weekRealizedTotal)}</span>}
              hint={`${text.autoRealized} ${formatSignedMoney(payload.realized.autoRealized)} / ${text.manualRealized} ${formatSignedMoney(payload.realized.manualRealized)}`}
            />
            <StatCard label={text.tradeCount} value={String(summary.trade_count ?? 0)} hint={`${text.riskTitle.split(' ')[0]} ${String(summary.risk_count ?? 0)}`} />
          </div>

          {/* 账户总览 */}
          <div className="glass-panel px-4 py-4">
            <h3 className="mb-3 text-sm font-semibold text-foreground">{text.overview}</h3>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-wider text-secondary-text">
                    <th className="py-2 pr-4">{text.account}</th>
                    <th className="py-2 pr-4 text-right">{text.equityStart}</th>
                    <th className="py-2 pr-4 text-right">{text.equityEnd}</th>
                    <th className="py-2 pr-4 text-right">{text.netFlowIn}</th>
                    <th className="py-2 pr-4 text-right">{text.weeklyReturnAmount}</th>
                    <th className="py-2 pr-4 text-right">{text.weeklyReturnPct}</th>
                    <th className="py-2 pr-4 text-right">{text.cashRatio}</th>
                    <th className="py-2 pr-4 text-right">{text.drawdown}</th>
                  </tr>
                </thead>
                <tbody>
                  {payload.overview.accounts.map((account) => (
                    <tr key={account.accountId} className="border-t border-subtle">
                      <td className="py-2 pr-4 font-medium text-foreground">{account.accountName}</td>
                      <td className="py-2 pr-4 text-right text-secondary-text">{formatMoney(account.equityStart)}</td>
                      <td className="py-2 pr-4 text-right text-secondary-text">{formatMoney(account.equityEnd)}</td>
                      <td className="py-2 pr-4 text-right text-secondary-text">{formatSignedMoney(account.netFlowIn)}</td>
                      <td className={`py-2 pr-4 text-right ${pnlClass(account.weeklyReturnAmount)}`}>{formatSignedMoney(account.weeklyReturnAmount)}</td>
                      <td className={`py-2 pr-4 text-right ${pnlClass(account.weeklyReturnPct)}`}>{formatPct(account.weeklyReturnPct)}</td>
                      <td className="py-2 pr-4 text-right text-secondary-text">{formatPct(account.cashRatioPct, 1)}</td>
                      <td className={`py-2 pr-4 text-right ${pnlClass(account.drawdownFromPeakPct)}`}>{formatPct(account.drawdownFromPeakPct, 1)}</td>
                    </tr>
                  ))}
                  <tr className="border-t border-subtle font-semibold">
                    <td className="py-2 pr-4 text-foreground">{text.overview} Σ</td>
                    <td className="py-2 pr-4 text-right text-secondary-text">{formatMoney(payload.overview.total.equityStart)}</td>
                    <td className="py-2 pr-4 text-right text-secondary-text">{formatMoney(payload.overview.total.equityEnd)}</td>
                    <td className="py-2 pr-4 text-right text-secondary-text">{formatSignedMoney(payload.overview.total.netFlowIn)}</td>
                    <td className={`py-2 pr-4 text-right ${pnlClass(payload.overview.total.weeklyReturnAmount)}`}>{formatSignedMoney(payload.overview.total.weeklyReturnAmount)}</td>
                    <td className={`py-2 pr-4 text-right ${pnlClass(payload.overview.total.weeklyReturnPct)}`}>{formatPct(payload.overview.total.weeklyReturnPct)}</td>
                    <td className="py-2 pr-4 text-right text-secondary-text">{formatPct(payload.overview.total.cashRatioPct, 1)}</td>
                    <td className={`py-2 pr-4 text-right ${pnlClass(payload.overview.total.drawdownFromPeakPct)}`}>{formatPct(payload.overview.total.drawdownFromPeakPct, 1)}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* 盈亏归因 */}
          <div className="glass-panel px-4 py-4">
            <div className="mb-1 flex items-baseline justify-between gap-2">
              <h3 className="text-sm font-semibold text-foreground">{text.attribution}</h3>
              <p className="text-xs text-secondary-text">{text.attributionHint}</p>
            </div>
            <Table<WeeklyReviewAttributionItem>
              rowKey="symbol"
              size="small"
              columns={attributionColumns}
              dataSource={payload.attribution.items}
              pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false }}
            />
          </div>

          {/* 已实现 */}
          {payload.realized.weekEvents.length > 0 ? (
            <div className="glass-panel px-4 py-4">
              <div className="mb-3 flex flex-wrap items-center gap-4 text-sm">
                <h3 className="font-semibold text-foreground">{text.realizedTitle}</h3>
                <span className="text-secondary-text">
                  {text.autoRealized} <span className={pnlClass(payload.realized.autoRealized)}>{formatSignedMoney(payload.realized.autoRealized)}</span>
                </span>
                <span className="text-secondary-text">
                  {text.manualRealized} <span className={pnlClass(payload.realized.manualRealized)}>{formatSignedMoney(payload.realized.manualRealized)}</span>
                </span>
              </div>
              <Table<WeeklyReviewRealizedEvent>
                rowKey={(item) => `${item.tradeDate}-${item.symbol}-${item.quantity}`}
                size="small"
                columns={realizedColumns}
                dataSource={payload.realized.weekEvents}
                pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false }}
              />
            </div>
          ) : null}

          {/* 结构体检 */}
          <div className="glass-panel px-4 py-4">
            <h3 className="mb-3 text-sm font-semibold text-foreground">{text.structure}</h3>
            <div className="grid gap-6 md:grid-cols-3">
              {distributionBlock(text.byCategory, payload.structure.byCategory)}
              {distributionBlock(text.byAccount, payload.structure.byAccount)}
              {distributionBlock(text.byIndustry, payload.structure.byIndustry)}
            </div>
            <div className="mt-4 grid gap-4 border-t border-subtle pt-4 md:grid-cols-3">
              <div>
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-secondary-text">{text.concentration}</p>
                <p className="text-sm text-foreground">
                  {text.top1}：{payload.structure.concentration.top1?.name || payload.structure.concentration.top1?.symbol || '-'}
                  <span className={`ml-2 ${payload.structure.concentration.top1Flagged ? 'text-warning font-medium' : 'text-secondary-text'}`}>
                    {formatPct(payload.structure.concentration.top1?.pct, 1)}
                  </span>
                </p>
                <p className="mt-1 text-sm text-secondary-text">
                  {text.top5} {formatPct(payload.structure.concentration.top5Pct, 1)}
                </p>
              </div>
              <div>
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-secondary-text">{text.overlap}</p>
                {payload.structure.crossAccountOverlap.length === 0 ? (
                  <p className="text-sm text-secondary-text">-</p>
                ) : (
                  payload.structure.crossAccountOverlap.map((item) => (
                    <p key={item.symbol} className="text-sm text-foreground">
                      <span className="font-medium">{item.symbol}</span>
                      <span className="ml-2 text-secondary-text">
                        {item.accounts.join(' / ')} · {formatMoney(item.marketValue)}
                      </span>
                    </p>
                  ))
                )}
              </div>
              <div>
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-secondary-text">{text.missingLogic}</p>
                {payload.structure.missingBuyLogic.length === 0 ? (
                  <p className="text-sm text-secondary-text">-</p>
                ) : (
                  <p className="text-sm text-foreground">
                    {payload.structure.missingBuyLogic.map((item) => item.symbol).join('、')}
                    <span className="ml-2 text-secondary-text">（{payload.structure.missingBuyLogic.length}）</span>
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* 持仓分层 */}
          <div className="glass-panel px-4 py-4">
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <h3 className="text-sm font-semibold text-foreground">{text.tierTitle}</h3>
              <Tag color="red">{text.tierDeepLoss} {payload.positionTiers.counts.deep_loss ?? 0}</Tag>
              <Tag color="orange">{text.tierLoss} {payload.positionTiers.counts.loss ?? 0}</Tag>
              <Tag color="green">{text.tierProfit} {payload.positionTiers.counts.profit ?? 0}</Tag>
            </div>
            <Table<WeeklyReviewPositionTierItem>
              rowKey={(item) => `${item.accountId}-${item.symbol}`}
              size="small"
              columns={tierColumns}
              dataSource={payload.positionTiers.items}
              pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false }}
            />
          </div>

          {/* 转债专区 */}
          {payload.cbZone.held.length > 0 ? (
            <div className="glass-panel px-4 py-4">
              <h3 className="mb-3 text-sm font-semibold text-foreground">{text.cbTitle}</h3>
              <Table<WeeklyReviewCbHeld>
                rowKey="symbol"
                size="small"
                columns={cbColumns}
                dataSource={payload.cbZone.held}
                pagination={false}
              />
              {payload.cbZone.events.length > 0 ? (
                <div className="mt-4">
                  <p className="mb-2 text-xs font-medium uppercase tracking-wider text-secondary-text">{text.cbEvents}</p>
                  {payload.cbZone.events.map((event, index) => (
                    <p key={`${event.symbol}-${event.eventDate}-${index}`} className="text-sm text-foreground">
                      <span className="font-medium">{event.symbol}</span>
                      <Tag className="ml-2" color={event.eventType === 'strong_redeem' ? 'red' : 'blue'}>{event.eventType}</Tag>
                      <span className="ml-1 text-secondary-text">{event.eventDetail}</span>
                    </p>
                  ))}
                </div>
              ) : null}
              <div className="mt-4 border-t border-subtle pt-3 text-sm text-secondary-text">
                {text.strategyRuns}：{payload.cbZone.strategyRuns.runCount ?? 0}（rebalance {payload.cbZone.strategyRuns.modes?.rebalance ?? 0}）
                ｜ {text.rebalanceEntries}：{payload.cbZone.strategyRuns.rebalanceEntries ?? 0}
                （{language === 'zh' ? '买' : 'Buy'} {payload.cbZone.strategyRuns.buyEntries ?? 0} / {language === 'zh' ? '卖' : 'Sell'} {payload.cbZone.strategyRuns.sellEntries ?? 0}）
              </div>
            </div>
          ) : null}

          {/* 信号对照 */}
          {payload.signalsVsTrades.items.length > 0 ? (
            <div className="glass-panel px-4 py-4">
              <h3 className="mb-3 text-sm font-semibold text-foreground">{text.signalsTitle}</h3>
              <Table<WeeklyReviewSignalItem>
                rowKey={(item) => `${item.stockCode}-${item.createdDate}`}
                size="small"
                columns={signalColumns}
                dataSource={payload.signalsVsTrades.items}
                pagination={false}
              />
            </div>
          ) : null}

          {/* 风险清单 */}
          {payload.riskList.items.length > 0 ? (
            <div className="glass-panel px-4 py-4">
              <h3 className="mb-3 text-sm font-semibold text-foreground">{text.riskTitle}</h3>
              <div className="space-y-2">
                {payload.riskList.items.map((item, index) => (
                  <div key={index} className="flex items-start gap-2 text-sm">
                    <Tag className="mt-0.5 shrink-0" color={SEVERITY_COLORS[item.severity] ?? 'default'}>{item.severity}</Tag>
                    <span className="text-foreground">{item.message}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {/* 人工区 */}
          <div className="glass-panel px-4 py-4">
            <h3 className="mb-3 text-sm font-semibold text-foreground">{text.manualTitle}</h3>
            <div className="grid gap-6 lg:grid-cols-2">
              <div>
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-secondary-text">{text.weeklyNote}</p>
                <textarea
                  className={`${TEXTAREA_CLASS} min-h-28`}
                  value={noteDraft}
                  placeholder={text.weeklyNotePlaceholder}
                  onChange={(event) => setNoteDraft(event.target.value)}
                />
                <div className="mt-2 flex items-center gap-2">
                  <button type="button" className="btn-secondary h-9 px-3 text-xs" disabled={noteSaving} onClick={() => void saveNote()}>
                    {noteSaving ? text.saving : text.saveNote}
                  </button>
                  {noteSavedAt ? <span className="text-xs text-secondary-text">{text.saved} {noteSavedAt}</span> : null}
                </div>
              </div>
              <div>
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-secondary-text">{text.nextWeekPlans}</p>
                {detail.plans.length === 0 ? (
                  <p className="text-sm text-secondary-text">{text.noPlans}</p>
                ) : (
                  <div className="divide-y divide-subtle">
                    {detail.plans.map((plan) => planRow(plan, false))}
                  </div>
                )}
                <div className="mt-3 space-y-2">
                  <div className="flex gap-2">
                    <Select
                      value={planAction}
                      onChange={setPlanAction}
                      className="w-28 shrink-0"
                      options={[
                        { value: 'add', label: 'add' },
                        { value: 'reduce', label: 'reduce' },
                        { value: 'clear', label: 'clear' },
                        { value: 'watch', label: 'watch' },
                      ]}
                    />
                    <input
                      className={`${INPUT_CLASS} w-32 shrink-0`}
                      placeholder={text.addSymbol}
                      value={planSymbol}
                      onChange={(event) => setPlanSymbol(event.target.value)}
                    />
                  </div>
                  <div className="flex gap-2">
                    <input
                      className={INPUT_CLASS}
                      placeholder={text.addContent}
                      value={planContent}
                      onChange={(event) => setPlanContent(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' && planContent.trim() && !planBusy) {
                          event.preventDefault();
                          void addPlan();
                        }
                      }}
                    />
                    <button type="button" className="btn-primary h-10 shrink-0 px-3 text-sm" disabled={planBusy || !planContent.trim()} onClick={() => void addPlan()}>
                      <Plus className="h-4 w-4" />
                      {text.addPlan}
                    </button>
                  </div>
                </div>
              </div>
            </div>
            {detail.prevPlans.length > 0 ? (
              <div className="mt-6 border-t border-subtle pt-4">
                <p className="mb-2 text-xs font-medium uppercase tracking-wider text-secondary-text">
                  {text.prevWeekPlans}（{detail.prevPeriod?.periodStart} ~ {detail.prevPeriod?.periodEnd}）
                </p>
                <div className="divide-y divide-subtle">
                  {detail.prevPlans.map((plan) => planRow(plan, true))}
                </div>
              </div>
            ) : (
              <p className="mt-4 border-t border-subtle pt-3 text-xs text-secondary-text">{text.noPrevPlans}</p>
            )}
          </div>

          {/* 数据边界 */}
          {payload.limitations && payload.limitations.length > 0 ? (
            <div className="glass-panel px-4 py-3">
              <p className="mb-1 text-xs font-medium uppercase tracking-wider text-secondary-text">{text.limitations}</p>
              {payload.limitations.map((item, index) => (
                <p key={index} className="text-xs text-secondary-text">· {item}</p>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};
