import { useState } from 'react';
import { Tabs } from 'antd';
import { useSearchParams } from 'react-router-dom';
import { AppPage, PageHeader } from '../components/common';
import { ConvertibleBondTab } from '../components/market-data/ConvertibleBondTab';
import { DataSyncPanel } from '../components/market-data/DataSyncPanel';
import { StockTab } from '../components/market-data/StockTab';

type MarketDataTabKey = 'convertible-bond' | 'stock' | 'sync';

const MarketDataPage: React.FC = () => {
  // 支持 /market-data?tab=convertible-bond&symbol=113042 直达（策略看板等入口新开标签页跳转）
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const [activeTab, setActiveTab] = useState<MarketDataTabKey>(
    tabParam === 'stock' || tabParam === 'sync' ? tabParam : 'convertible-bond',
  );

  return (
    <AppPage>
      <PageHeader eyebrow="Market Data" title="行情数据" description="可转债与股票历史行情、图表、事件浏览与数据同步。" />
      <Tabs
        className="mt-4"
        activeKey={activeTab}
        onChange={(key) => setActiveTab(key as MarketDataTabKey)}
        items={[
          { key: 'convertible-bond', label: '可转债', children: <ConvertibleBondTab /> },
          { key: 'stock', label: '股票', children: <StockTab /> },
          { key: 'sync', label: '数据同步', children: <DataSyncPanel /> },
        ]}
      />
    </AppPage>
  );
};

export default MarketDataPage;
