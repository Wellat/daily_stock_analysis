import { Tabs } from 'antd';
import { AppPage, PageHeader } from '../components/common';
import { LiveSignalPanel } from '../components/strategy-lab/LiveSignalPanel';
import { ParameterSearchPanel } from '../components/strategy-lab/ParameterSearchPanel';
import { StrategyResearchPanel } from '../components/strategy-lab/StrategyResearchPanel';

const StrategyLabPage = () => (
  <AppPage>
    <PageHeader eyebrow="Strategy Lab" title="策略实验室" description="策略回测、参数实验与 Portfolio 联动。" />
    <Tabs
      className="mt-4"
      defaultActiveKey="research"
      items={[
        { key: 'research', label: '策略研究', children: <StrategyResearchPanel /> },
        { key: 'search', label: '参数搜索', children: <ParameterSearchPanel /> },
        { key: 'signals', label: '实盘信号', children: <LiveSignalPanel /> },
      ]}
    />
  </AppPage>
);

export default StrategyLabPage;
