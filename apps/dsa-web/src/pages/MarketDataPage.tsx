import { Tabs } from 'antd';
import { AppPage, PageHeader } from '../components/common';
import { ConvertibleBondTab } from '../components/market-data/ConvertibleBondTab';
import { DataSyncPanel } from '../components/market-data/DataSyncPanel';
import { StockTab } from '../components/market-data/StockTab';

const MarketDataPage: React.FC = () => (
  <AppPage>
    <PageHeader eyebrow="Market Data" title="行情数据" description="可转债与股票历史行情、图表、事件浏览与数据同步。" />
    <Tabs
      className="mt-4"
      defaultActiveKey="convertible-bond"
      items={[
        { key: 'convertible-bond', label: '可转债', children: <ConvertibleBondTab /> },
        { key: 'stock', label: '股票', children: <StockTab /> },
        { key: 'sync', label: '数据同步', children: <DataSyncPanel /> },
      ]}
    />
  </AppPage>
);

export default MarketDataPage;
