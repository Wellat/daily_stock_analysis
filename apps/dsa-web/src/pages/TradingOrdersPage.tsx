import { Tabs } from 'antd';
import { AppPage, PageHeader } from '../components/common';
import { PositionsTab } from '../components/trading/PositionsTab';
import { TradingOrdersTab } from '../components/trading/TradingOrdersTab';
import { LiveStrategyPanel } from '../components/trading/LiveStrategyPanel';

const TradingOrdersPage: React.FC = () => (
  <AppPage>
    <PageHeader
      eyebrow="QMT 实盘"
      title="实盘"
      description="可转债实盘交易指令与账户持仓，交易指令由 QMT 拉取执行并回写结果，持仓由 QMT 每日收盘后上报。"
    />
    <Tabs
      className="mt-4"
      defaultActiveKey="orders"
      items={[
        { key: 'strategy', label: '策略运行', children: <LiveStrategyPanel /> },
        { key: 'orders', label: '交易记录', children: <TradingOrdersTab /> },
        { key: 'positions', label: '持仓', children: <PositionsTab /> },
      ]}
    />
  </AppPage>
);

export default TradingOrdersPage;
