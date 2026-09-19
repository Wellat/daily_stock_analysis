import { Tabs } from 'antd';
import { AppPage, PageHeader } from '../components/common';
import { LowPremiumTrackPanel } from '../components/strategy-lab/LowPremiumTrackPanel';
import { ParameterSearchPanel } from '../components/strategy-lab/ParameterSearchPanel';
import { StrategyResearchPanel } from '../components/strategy-lab/StrategyResearchPanel';

const StrategyLabPage = () => (
  <AppPage>
    <PageHeader eyebrow="Strategy Lab" title="策略实验室" description="策略回测、参数搜索与低溢价跟踪。" />
    <Tabs
      className="mt-4"
      defaultActiveKey="research"
      items={[
        { key: 'research', label: '策略研究', children: <StrategyResearchPanel /> },
        { key: 'search', label: '参数搜索', children: <ParameterSearchPanel /> },
        { key: 'premium-track', label: '低溢价跟踪', children: <LowPremiumTrackPanel /> },
      ]}
    />
  </AppPage>
);

export default StrategyLabPage;
