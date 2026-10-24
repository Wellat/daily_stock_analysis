import type { DecisionSignalItem } from './decisionSignals';

export type PortfolioCostMethod = 'fifo' | 'avg';
export type PortfolioSide = 'buy' | 'sell';
export type PortfolioCashDirection = 'in' | 'out';
export type PortfolioCorporateActionType = 'cash_dividend' | 'split_adjustment';

export interface PortfolioAccountItem {
  id: number;
  ownerId?: string | null;
  name: string;
  broker?: string | null;
  market: 'cn' | 'hk' | 'us' | 'jp' | 'kr' | 'tw';
  baseCurrency: string;
  isActive: boolean;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface PortfolioAccountListResponse {
  accounts: PortfolioAccountItem[];
}

export interface PortfolioAccountCreateRequest {
  name: string;
  broker?: string;
  market: 'cn' | 'hk' | 'us' | 'jp' | 'kr' | 'tw';
  baseCurrency: string;
  ownerId?: string;
}

export interface PortfolioPositionItem {
  symbol: string;
  symbolName?: string | null;
  market: string;
  currency: string;
  industry?: string | null;
  category?: string | null;
  buyLogic?: string | null;
  buyLogicText?: string | null;
  stopLossPrice?: number | null;
  note?: string | null;
  quantity: number;
  avgCost: number;
  totalCost: number;
  lastPrice: number;
  marketValueBase: number;
  unrealizedPnlBase: number;
  unrealizedPnlPct?: number | null;
  valuationCurrency: string;
  priceSource?: 'realtime_quote' | 'history_close' | 'missing' | string;
  priceProvider?: string | null;
  priceDate?: string | null;
  priceStale?: boolean;
  priceAvailable?: boolean;
  dataQuality?: 'ok' | 'partial' | string;
  limitations?: string[];
}

export type PortfolioPositionCategory = 'stock' | 'etf' | 'fund' | 'convertible_bond';
export type PortfolioBuyLogic = 'dividend' | 'index_tracking' | 'automated' | 'undefined';

export interface PortfolioPositionMetaUpdateRequest {
  accountId: number;
  symbol: string;
  industry?: string;
  category?: PortfolioPositionCategory | string;
  buyLogic?: PortfolioBuyLogic | string;
  buyLogicText?: string;
  stopLossPrice?: number | null;
  note?: string;
}

export interface PortfolioPositionMetaItem {
  accountId: number;
  symbol: string;
  market: string;
  industry?: string | null;
  category?: string | null;
  buyLogic?: string | null;
  buyLogicText?: string | null;
  stopLossPrice?: number | null;
  note?: string | null;
}

export interface PortfolioPositionMetaAutofillResponse {
  accountsScanned: number;
  positionsScanned: number;
  industryCandidates: number;
  industryFilled: number;
  failed: string[];
}

export interface PortfolioTrendPoint {
  date: string;
  totalCash: number;
  totalMarketValue: number;
  totalEquity: number;
  realizedPnl: number;
  unrealizedPnl: number;
  totalPnl: number;
}

export interface PortfolioTrendResponse {
  accountId?: number | null;
  costMethod: PortfolioCostMethod | string;
  currency: string;
  fromDate: string;
  toDate: string;
  backfilled: number;
  truncated: boolean;
  items: PortfolioTrendPoint[];
}

export interface PortfolioPositionAnalysisRequest {
  accountId?: number;
  analysisPhase?: 'auto' | 'premarket' | 'intraday' | 'postmarket';
  force?: boolean;
}

export interface PortfolioAccountSnapshot {
  accountId: number;
  accountName: string;
  ownerId?: string | null;
  broker?: string | null;
  market: string;
  baseCurrency: string;
  asOf: string;
  costMethod: PortfolioCostMethod;
  totalCash: number;
  totalMarketValue: number;
  totalEquity: number;
  realizedPnl: number;
  unrealizedPnl: number;
  feeTotal: number;
  taxTotal: number;
  fxStale: boolean;
  dataQuality?: 'ok' | 'partial' | string;
  limitations?: string[];
  positions: PortfolioPositionItem[];
}

export interface PortfolioSnapshotResponse {
  asOf: string;
  costMethod: PortfolioCostMethod;
  currency: string;
  accountCount: number;
  totalCash: number;
  totalMarketValue: number;
  totalEquity: number;
  realizedPnl: number;
  unrealizedPnl: number;
  feeTotal: number;
  taxTotal: number;
  fxStale: boolean;
  dataQuality?: 'ok' | 'partial' | string;
  limitations?: string[];
  accounts: PortfolioAccountSnapshot[];
}

export interface PortfolioConcentrationItem {
  symbol: string;
  marketValueBase: number;
  weightPct: number;
  isAlert: boolean;
}

export interface PortfolioSectorConcentrationItem {
  sector: string;
  marketValueBase: number;
  weightPct: number;
  symbolCount: number;
  isAlert: boolean;
}

export interface PortfolioDrawdownBlock {
  seriesPoints: number;
  maxDrawdownPct: number;
  currentDrawdownPct: number;
  alert: boolean;
  fxStale: boolean;
}

export interface PortfolioStopLossItem {
  accountId: number;
  symbol: string;
  avgCost: number;
  lastPrice: number;
  lossPct: number;
  nearThresholdPct: number;
  isTriggered: boolean;
}

export interface PortfolioDecisionSignalRiskItem {
  accountId?: number | null;
  symbol: string;
  market: string;
  signal: Partial<DecisionSignalItem>;
}

export interface PortfolioDecisionSignalRiskBlock {
  available: boolean;
  total: number;
  actions: {
    sell?: number;
    reduce?: number;
    alert?: number;
    [key: string]: number | undefined;
  };
  items: PortfolioDecisionSignalRiskItem[];
}

export interface PortfolioRiskResponse {
  asOf: string;
  accountId?: number | null;
  costMethod: PortfolioCostMethod;
  currency: string;
  thresholds: Record<string, number>;
  concentration: {
    totalMarketValue: number;
    topWeightPct: number;
    alert: boolean;
    topPositions: PortfolioConcentrationItem[];
  };
  sectorConcentration: {
    totalMarketValue: number;
    topWeightPct: number;
    alert: boolean;
    topSectors: PortfolioSectorConcentrationItem[];
    coverage: Record<string, number>;
    errors: string[];
  };
  drawdown: PortfolioDrawdownBlock;
  stopLoss: {
    nearAlert: boolean;
    triggeredCount: number;
    nearCount: number;
    items: PortfolioStopLossItem[];
  };
  decisionSignalRisk?: PortfolioDecisionSignalRiskBlock;
}

export interface PortfolioTradeCreateRequest {
  accountId: number;
  symbol: string;
  tradeDate: string;
  side: PortfolioSide;
  quantity: number;
  price: number;
  fee?: number;
  tax?: number;
  market?: 'cn' | 'hk' | 'us' | 'jp' | 'kr' | 'tw';
  currency?: string;
  tradeUid?: string;
  note?: string;
}

export interface PortfolioCashLedgerCreateRequest {
  accountId: number;
  eventDate: string;
  direction: PortfolioCashDirection;
  amount: number;
  currency?: string;
  note?: string;
}

export interface PortfolioCorporateActionCreateRequest {
  accountId: number;
  symbol: string;
  effectiveDate: string;
  actionType: PortfolioCorporateActionType;
  market?: 'cn' | 'hk' | 'us' | 'jp' | 'kr' | 'tw';
  currency?: string;
  cashDividendPerShare?: number;
  splitRatio?: number;
  note?: string;
}

export interface PortfolioEventCreatedResponse {
  id: number;
}

export interface PortfolioDeleteResponse {
  deleted: number;
}

export interface PortfolioTradeListItem {
  id: number;
  accountId: number;
  tradeUid?: string | null;
  symbol: string;
  market: string;
  currency: string;
  tradeDate: string;
  side: PortfolioSide;
  quantity: number;
  price: number;
  fee: number;
  tax: number;
  note?: string | null;
  createdAt?: string | null;
}

export interface PortfolioTradeListResponse {
  items: PortfolioTradeListItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface PortfolioCashLedgerListItem {
  id: number;
  accountId: number;
  eventDate: string;
  direction: PortfolioCashDirection;
  amount: number;
  currency: string;
  note?: string | null;
  createdAt?: string | null;
}

export interface PortfolioCashLedgerListResponse {
  items: PortfolioCashLedgerListItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface PortfolioCorporateActionListItem {
  id: number;
  accountId: number;
  symbol: string;
  market: string;
  currency: string;
  effectiveDate: string;
  actionType: PortfolioCorporateActionType;
  cashDividendPerShare?: number | null;
  splitRatio?: number | null;
  note?: string | null;
  createdAt?: string | null;
}

export interface PortfolioCorporateActionListResponse {
  items: PortfolioCorporateActionListItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface PortfolioImportTradeItem {
  tradeDate: string;
  symbol: string;
  side: PortfolioSide;
  quantity: number;
  price: number;
  fee: number;
  tax: number;
  tradeUid?: string | null;
  dedupHash: string;
  currency?: string | null;
}

export interface PortfolioImportParseResponse {
  broker: string;
  recordCount: number;
  skippedCount: number;
  errorCount: number;
  records: PortfolioImportTradeItem[];
  errors: string[];
}

export interface PortfolioImportCommitResponse {
  accountId: number;
  recordCount: number;
  insertedCount: number;
  duplicateCount: number;
  failedCount: number;
  dryRun: boolean;
  errors: string[];
}

export interface PortfolioImportBrokerItem {
  broker: string;
  aliases: string[];
  displayName?: string;
}

export interface PortfolioImportBrokerListResponse {
  brokers: PortfolioImportBrokerItem[];
}

export interface PortfolioFxRefreshResponse {
  asOf: string;
  accountCount: number;
  refreshEnabled?: boolean;
  disabledReason?: string | null;
  pairCount: number;
  updatedCount: number;
  staleCount: number;
  errorCount: number;
}

export interface PortfolioQmtSyncRequest {
  qmtAccount?: string;
  accountId?: number;
  dryRun?: boolean;
}

export interface PortfolioQmtSyncEventItem {
  side: 'buy' | 'sell';
  symbol: string;
  symbolName?: string | null;
  quantity: number;
  price?: number | null;
  tradeUid: string;
  status: 'inserted' | 'dry_run' | 'duplicate' | 'failed';
  error?: string | null;
}

export interface PortfolioQmtSyncAccountResult {
  qmtAccount: string;
  accountId?: number | null;
  accountName?: string | null;
  accountCreated: boolean;
  positionCount: number;
  events: PortfolioQmtSyncEventItem[];
  insertedCount: number;
  duplicateCount: number;
  failedCount: number;
  cashEntries: number;
  error?: string | null;
}

export interface PortfolioQmtSyncResponse {
  dryRun: boolean;
  accounts: PortfolioQmtSyncAccountResult[];
}

export type WeeklyReviewPlanAction = 'add' | 'reduce' | 'clear' | 'watch';
export type WeeklyReviewPlanStatus = 'pending' | 'done' | 'skipped';

export interface WeeklyReviewPlanItem {
  planId: number;
  action: WeeklyReviewPlanAction | string;
  symbol?: string | null;
  content: string;
  status: WeeklyReviewPlanStatus | string;
}

export interface WeeklyReviewListItem {
  reviewId: number;
  periodStart: string;
  periodEnd: string;
  status: string;
  feishuSent: boolean;
  hasNote: boolean;
  planCounts: { pending?: number; done?: number; skipped?: number; total?: number };
  summary: Record<string, unknown>;
  error?: string | null;
}

export interface WeeklyReviewListResponse {
  items: WeeklyReviewListItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface WeeklyReviewOverviewAccount {
  accountId: number;
  accountName: string;
  equityStart?: number | null;
  equityEnd?: number | null;
  netFlowIn?: number | null;
  weeklyReturnAmount?: number | null;
  weeklyReturnPct?: number | null;
  cashEnd?: number | null;
  cashRatioPct?: number | null;
  realizedDeltaWeek?: number | null;
  drawdownFromPeakPct?: number | null;
  dataStartInWeek?: boolean;
}

export interface WeeklyReviewOverview {
  accounts: WeeklyReviewOverviewAccount[];
  total: Omit<WeeklyReviewOverviewAccount, 'accountId' | 'accountName' | 'dataStartInWeek'>;
}

export interface WeeklyReviewAttributionItem {
  symbol: string;
  name?: string | null;
  mvStart?: number | null;
  mvEnd?: number | null;
  buyAmount?: number | null;
  sellAmount?: number | null;
  pnl?: number | null;
  weeklyPriceChangePct?: number | null;
  accounts?: string[];
  newPosition?: boolean;
  exitedPosition?: boolean;
}

export interface WeeklyReviewAttribution {
  items: WeeklyReviewAttributionItem[];
  topContributors: WeeklyReviewAttributionItem[];
  topDraggers: WeeklyReviewAttributionItem[];
}

export interface WeeklyReviewRealizedEvent {
  accountId: number;
  symbol: string;
  tradeDate: string;
  quantity: number;
  realized?: number | null;
  auto?: boolean;
}

export interface WeeklyReviewRealized {
  weekEvents: WeeklyReviewRealizedEvent[];
  weekRealizedTotal?: number | null;
  autoRealized?: number | null;
  manualRealized?: number | null;
  oversellDetected?: boolean;
}

export interface WeeklyReviewDistributionItem {
  label: string;
  marketValue?: number | null;
  pct?: number | null;
}

export interface WeeklyReviewStructure {
  positionCount: number;
  totalMarketValue?: number | null;
  byCategory: WeeklyReviewDistributionItem[];
  byAccount: WeeklyReviewDistributionItem[];
  byIndustry: WeeklyReviewDistributionItem[];
  concentration: {
    top1?: { symbol: string; name?: string | null; pct?: number | null } | null;
    top5Pct?: number | null;
    top1Flagged?: boolean;
  };
  cashRatioPct?: number | null;
  crossAccountOverlap: { symbol: string; name?: string | null; accounts: string[]; marketValue?: number | null }[];
  missingBuyLogic: { accountId: number; symbol: string; name?: string | null }[];
}

export interface WeeklyReviewPositionTierItem {
  accountId: number;
  accountName: string;
  symbol: string;
  name?: string | null;
  quantity: number;
  marketValue?: number | null;
  pnlPct?: number | null;
  tier: 'deep_loss' | 'loss' | 'profit' | 'unknown' | string;
  industry?: string | null;
  holdingDays?: number | null;
  durationCapped?: boolean;
}

export interface WeeklyReviewPositionTiers {
  items: WeeklyReviewPositionTierItem[];
  counts: Record<string, number>;
  deepLossThresholdPct?: number;
}

export interface WeeklyReviewCbHeld {
  symbol: string;
  name?: string | null;
  factorDate?: string | null;
  close?: number | null;
  premiumRate?: number | null;
  priceBand?: string | null;
  remainingSize?: number | null;
  maturityDate?: string | null;
  daysToMaturity?: number | null;
  maturityWarn?: boolean;
  forceRedeemCountdown?: string | null;
}

export interface WeeklyReviewCbZone {
  held: WeeklyReviewCbHeld[];
  events: { symbol: string; name?: string | null; eventDate: string; eventType: string; eventDetail?: string | null }[];
  strategyRuns: {
    runCount?: number;
    modes?: Record<string, number>;
    rebalanceEntries?: number;
    buyEntries?: number;
    sellEntries?: number;
  };
  tradesSplit: {
    auto: { count?: number; buyAmount?: number | null; sellAmount?: number | null };
    manual: { count?: number; buyAmount?: number | null; sellAmount?: number | null };
    autoAccountNames?: string[];
  };
  realizedSplit: { autoRealized?: number | null; manualRealized?: number | null };
}

export interface WeeklyReviewSignalItem {
  stockCode: string;
  stockName?: string | null;
  action: string;
  actionLabel?: string | null;
  createdDate?: string | null;
  expiresDate?: string | null;
  matchedBuys?: number;
  matchedSells?: number;
  followed?: boolean;
}

export interface WeeklyReviewSignals {
  items: WeeklyReviewSignalItem[];
  matchedTotal?: number;
}

export interface WeeklyReviewRiskItem {
  type: string;
  severity: 'high' | 'medium' | 'low' | string;
  symbol?: string | null;
  message: string;
}

export interface WeeklyReviewPayload {
  period: { periodStart: string; periodEnd: string; tradingDays: string[] };
  summary: Record<string, unknown>;
  overview: WeeklyReviewOverview;
  attribution: WeeklyReviewAttribution;
  realized: WeeklyReviewRealized;
  structure: WeeklyReviewStructure;
  positionTiers: WeeklyReviewPositionTiers;
  cbZone: WeeklyReviewCbZone;
  signalsVsTrades: WeeklyReviewSignals;
  riskList: { items: WeeklyReviewRiskItem[] };
  limitations?: string[];
}

export interface WeeklyReviewDetail {
  reviewId: number;
  periodStart: string;
  periodEnd: string;
  status: string;
  feishuSent: boolean;
  weeklyNote: string;
  error?: string | null;
  plans: WeeklyReviewPlanItem[];
  prevPeriod?: { periodStart: string; periodEnd: string } | null;
  prevPlans: WeeklyReviewPlanItem[];
  payload: WeeklyReviewPayload;
}

export interface WeeklyReviewGenerateRequest {
  periodStart?: string;
  push?: boolean;
}

export interface WeeklyReviewGenerateResponse {
  reviewId?: number | null;
  periodStart: string;
  periodEnd: string;
  skipped?: string | null;
  feishuSent: boolean;
  summary: Record<string, unknown>;
}

export interface WeeklyReviewDeleteResponse {
  deleted: boolean;
  planId: number;
}

export type PortfolioTrendPattern = 'bull' | 'bear' | 'mixed' | 'unknown';

export interface PortfolioIndicatorCbExtra {
  premiumRate?: number | null;
  factorDate?: string | null;
  priceBand?: string | null;
  remainingSize?: number | null;
  daysToMaturity?: number | null;
  forceRedeemCountdown?: string | null;
}

export interface PortfolioIndicatorItem {
  symbol: string;
  lastBarDate?: string | null;
  lastClose?: number | null;
  ma5?: number | null;
  ma10?: number | null;
  ma20?: number | null;
  biasMa5Pct?: number | null;
  biasMa10Pct?: number | null;
  biasMa20Pct?: number | null;
  trendPattern: PortfolioTrendPattern | string;
  streakDays?: number | null;
  weekChangePct?: number | null;
  monthChangePct?: number | null;
  pos52wPct?: number | null;
  volumeRatio?: number | null;
  sparkline: { date: string; close: number }[];
  holdingDaysByAccount: Record<string, number>;
  cb?: PortfolioIndicatorCbExtra | null;
}

export interface PortfolioIndicatorResponse {
  asOf: string;
  items: PortfolioIndicatorItem[];
}
