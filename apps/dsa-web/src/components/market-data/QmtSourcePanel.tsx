import { useCallback, useEffect, useState } from 'react';
import { Button, Modal, Select, Spin, Switch, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiErrorAlert } from '../common';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { qmtQuotesApi } from '../../api/qmtQuotes';
import type {
  QmtDailyBar,
  QmtStatus,
  QmtSubscriptionOrigin,
  QmtSubscriptionSymbol,
} from '../../types/qmtQuotes';
import { SL_PANEL_CLASS } from '../strategy-lab/utils';

// 心跳后端有 10s 缓存，30s 轮询足够保持状态鲜活
const STATUS_POLL_INTERVAL_MS = 30_000;

const ORIGIN_META: Record<QmtSubscriptionOrigin, { label: string; color: string }> = {
  holdings: { label: '持仓', color: 'blue' },
  alert: { label: '告警', color: 'orange' },
  manual: { label: '手动', color: 'green' },
  stale: { label: '残留', color: 'default' },
};

// 排障视图字段中文名（未映射的键原样展示）
const QUOTE_FIELD_LABELS: Record<string, string> = {
  code: '代码',
  last: '最新价（元）',
  open: '开盘（元）',
  high: '最高（元）',
  low: '最低（元）',
  lastClose: '昨收（元）',
  volume: '成交量（手）',
  pvolume: '成交量（股/份/张）',
  amount: '成交额（元）',
  bid1: '买一（元）',
  ask1: '卖一（元）',
  bidPrice: '五档买价',
  askPrice: '五档卖价',
  bidVol: '五档买量（手）',
  askVol: '五档卖量（手）',
  timetag: '快照时刻',
  written_at: '写入时刻',
};

type ViewerState = {
  type: 'quote' | 'daily';
  symbol: string;
  loading: boolean;
  alive: boolean;
  found: boolean;
  data: Record<string, unknown> | null;
  error: ParsedApiError | null;
};

export const QmtSourcePanel: React.FC = () => {
  const [status, setStatus] = useState<QmtStatus | null>(null);
  const [symbols, setSymbols] = useState<QmtSubscriptionSymbol[]>([]);
  const [manualInput, setManualInput] = useState<string[]>([]);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [viewer, setViewer] = useState<ViewerState | null>(null);

  const refresh = useCallback(async (showError = true) => {
    try {
      const [nextStatus, nextDetail] = await Promise.all([
        qmtQuotesApi.getStatus(),
        qmtQuotesApi.getSubscription(),
      ]);
      setStatus(nextStatus);
      setSymbols(nextDetail.symbols ?? []);
      setManualInput(nextDetail.manualSymbols ?? []);
      if (showError) setError(null);
    } catch (exc) {
      // 轮询静默失败不打断面板；手动操作后展示错误
      if (showError) setError(getParsedApiError(exc));
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => { void refresh(false); }, STATUS_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const updateSetting = async (key: 'enabled' | 'autoIncludeHoldings' | 'includeAlertSymbols', value: boolean) => {
    setActionLoading(`setting-${key}`);
    try {
      const nextStatus = await qmtQuotesApi.updateSettings({ [key]: value });
      setStatus(nextStatus);
      setError(null);
      await refresh(false);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setActionLoading(null);
    }
  };

  const saveManualSymbols = async () => {
    setActionLoading('manual');
    try {
      await qmtQuotesApi.updateManualSymbols(manualInput);
      setSyncMessage(null);
      setError(null);
      await refresh();
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setActionLoading(null);
    }
  };

  const runSync = async () => {
    setActionLoading('sync');
    setSyncMessage(null);
    try {
      const result = await qmtQuotesApi.syncSubscription();
      setSyncMessage(
        result.changed
          ? `订阅已更新：新增 ${result.added.length} 个、移除 ${result.removed.length} 个，共 ${result.total} 个标的`
          : `订阅列表无变化（共 ${result.total} 个标的）`,
      );
      setError(null);
      await refresh(false);
    } catch (exc) {
      setError(getParsedApiError(exc));
    } finally {
      setActionLoading(null);
    }
  };

  const openViewer = async (type: 'quote' | 'daily', symbol: string) => {
    setViewer({ type, symbol, loading: true, alive: false, found: false, data: null, error: null });
    try {
      const result = type === 'quote'
        ? await qmtQuotesApi.getQuoteData(symbol)
        : await qmtQuotesApi.getDailyData(symbol);
      setViewer({
        type,
        symbol,
        loading: false,
        alive: result.alive,
        found: result.found,
        data: (result.data as Record<string, unknown> | null) ?? null,
        error: null,
      });
    } catch (exc) {
      setViewer({
        type,
        symbol,
        loading: false,
        alive: false,
        found: false,
        data: null,
        error: getParsedApiError(exc),
      });
    }
  };

  const columns: ColumnsType<QmtSubscriptionSymbol> = [
    { title: '标的', dataIndex: 'symbol', width: 150, render: (_, item) => (
      <div>
        <div className="font-medium">{item.name ? `${item.name}` : <span className="text-muted-text">--</span>}</div>
        <div className="text-xs text-muted-text">{item.symbol}</div>
      </div>
    ) },
    { title: '来源', key: 'origins', render: (_, item) => (
      <span>
        {item.origins.map((origin) => (
          <Tag key={origin} color={ORIGIN_META[origin]?.color}>{ORIGIN_META[origin]?.label ?? origin}</Tag>
        ))}
      </span>
    ) },
    { title: '操作', key: 'actions', width: 170, render: (_, item) => (
      <span className="flex gap-1">
        <Button
          size="small"
          aria-label={`查看实时数据 ${item.symbol}`}
          onClick={() => { void openViewer('quote', item.symbol); }}
        >
          实时数据
        </Button>
        <Button
          size="small"
          aria-label={`查看K线数据 ${item.symbol}`}
          onClick={() => { void openViewer('daily', item.symbol); }}
        >
          K线数据
        </Button>
      </span>
    ) },
  ];

  const quoteRows = (): { key: string; label: string; value: string }[] => {
    const data = viewer?.data;
    if (!data) return [];
    return Object.entries(data).map(([key, value]) => ({
      key,
      label: QUOTE_FIELD_LABELS[key] ?? key,
      value: Array.isArray(value) ? value.join(', ') : String(value ?? '--'),
    }));
  };

  const dailyBars = (): QmtDailyBar[] => {
    const data = viewer?.data as { bars?: QmtDailyBar[] } | null;
    return data?.bars ?? [];
  };

  const dailyMeta = (): string => {
    const data = viewer?.data as { dividend_type?: string; updated_at?: string } | null;
    const parts: string[] = [];
    if (data?.dividend_type) parts.push(`复权: ${data.dividend_type}`);
    if (data?.updated_at) parts.push(`写入时刻: ${data.updated_at}`);
    parts.push(`${dailyBars().length} 根`);
    return parts.join(' · ');
  };

  const viewerContent = () => {
    if (!viewer) return null;
    if (viewer.loading) return <div className="flex justify-center py-8"><Spin /></div>;
    if (viewer.error) return <ApiErrorAlert error={viewer.error} />;
    if (!viewer.alive) return <p className="py-6 text-center text-secondary-text">QMT 行情桥心跳离线，Redis 中暂无数据。</p>;
    if (!viewer.found) return <p className="py-6 text-center text-secondary-text">Redis 中无该标的数据（QMT 未写入此 key：无效/退市代码，或刚加入订阅还未回填）。</p>;
    if (viewer.type === 'quote') {
      return (
        <Table
          rowKey="key"
          size="small"
          columns={[
            { title: '字段', dataIndex: 'label', width: 170 },
            { title: '值', dataIndex: 'value', render: (value: string) => (
              <span className="break-all">{value}</span>
            ) },
          ]}
          dataSource={quoteRows()}
          pagination={false}
        />
      );
    }
    return (
      <div>
        <p className="mb-2 text-xs text-secondary-text">{dailyMeta()}（原始数据：日期 YYYYMMDD、成交量单位为手）</p>
        <Table
          rowKey="date"
          size="small"
          scroll={{ y: 360 }}
          pagination={false}
          columns={[
            { title: '日期', dataIndex: 'date', width: 100 },
            { title: '开盘', dataIndex: 'open', width: 90 },
            { title: '最高', dataIndex: 'high', width: 90 },
            { title: '最低', dataIndex: 'low', width: 90 },
            { title: '收盘', dataIndex: 'close', width: 90 },
            { title: '成交量（手）', dataIndex: 'volume', width: 110 },
            { title: '成交额（元）', dataIndex: 'amount', render: (value: number) => value?.toLocaleString?.() ?? String(value) },
          ]}
          dataSource={dailyBars()}
        />
      </div>
    );
  };

  const heartbeatTag = () => {
    if (!status?.enabled) return <Tag>已停用</Tag>;
    if (!status.redisAvailable) return <Tag color="warning">缺 redis 依赖</Tag>;
    if (status.alive) {
      return (
        <Tooltip title={status.heartbeat || '心跳正常'}>
          <Tag color="success">心跳正常</Tag>
        </Tooltip>
      );
    }
    return <Tag color="error">离线</Tag>;
  };

  return (
    <div className={SL_PANEL_CLASS}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-foreground">本地行情源（QMT 行情桥）</h2>
        <Button size="small" loading={actionLoading === 'sync'} onClick={() => { void runSync(); }}>
          立即同步订阅
        </Button>
      </div>
      <p className="mt-1 text-xs text-secondary-text">
        通过本机 Redis 读取 QMT 推送的实时快照与日K，告警巡检优先使用本地源（契约见 docs/qmt-quotes-link-01.md）。
      </p>

      {status ? (
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          {heartbeatTag()}
          <span className="text-secondary-text">{status.redisUrl}</span>
          <span>订阅 {status.subscribedCount}</span>
          <span className="text-secondary-text">（持仓 {status.holdingsCount} · 告警 {status.alertCount} · 手动 {status.manualSymbols.length}）</span>
        </div>
      ) : null}

      {status ? (
        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
          <label className="flex items-center gap-2">
            <Switch
              aria-label="启用 QMT 本地源"
              size="small"
              checked={status.enabled}
              loading={actionLoading === 'setting-enabled'}
              onChange={(checked) => { void updateSetting('enabled', checked); }}
            />
            启用 QMT 本地源
          </label>
          <label className="flex items-center gap-2">
            <Switch
              aria-label="自动纳入持仓标的"
              size="small"
              checked={status.autoIncludeHoldings}
              loading={actionLoading === 'setting-autoIncludeHoldings'}
              onChange={(checked) => { void updateSetting('autoIncludeHoldings', checked); }}
            />
            自动纳入持仓标的
          </label>
          <label className="flex items-center gap-2">
            <Switch
              aria-label="自动纳入告警标的"
              size="small"
              checked={status.includeAlertSymbols}
              loading={actionLoading === 'setting-includeAlertSymbols'}
              onChange={(checked) => { void updateSetting('includeAlertSymbols', checked); }}
            />
            自动纳入告警标的
          </label>
        </div>
      ) : null}

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          手动追加订阅（回车添加，仅沪深 6 位码）
          <Select
            aria-label="手动追加订阅标的"
            mode="tags"
            className="mt-1 w-full"
            value={manualInput}
            onChange={setManualInput}
            placeholder="如 600519.SH, 510050.SH"
            open={false}
            suffixIcon={null}
          />
        </label>
        <div className="flex items-end gap-2">
          <Button
            loading={actionLoading === 'manual'}
            onClick={() => { void saveManualSymbols(); }}
          >
            保存手动订阅
          </Button>
          {syncMessage ? <span className="text-xs text-secondary-text">{syncMessage}</span> : null}
        </div>
      </div>

      {error ? <ApiErrorAlert error={error} className="mt-3" /> : null}

      <Modal
        open={viewer !== null}
        title={viewer ? `${viewer.type === 'quote' ? '实时快照' : '日K数据'} · ${viewer.symbol}` : ''}
        footer={null}
        width={viewer?.type === 'daily' ? 760 : 560}
        onCancel={() => setViewer(null)}
      >
        {viewerContent()}
      </Modal>

      <Table
        rowKey="symbol"
        size="small"
        className="mt-3"
        columns={columns}
        dataSource={symbols}
        pagination={{ pageSize: 8, showTotal: (count) => `共 ${count} 条` }}
        locale={{ emptyText: '暂无订阅标的' }}
      />
    </div>
  );
};
