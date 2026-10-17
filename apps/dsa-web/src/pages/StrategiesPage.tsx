import type React from 'react';
import { AppPage, PageHeader } from '../components/common';
import { StrategyManagementPanel } from '../components/strategy-management/StrategyManagementPanel';

const StrategiesPage: React.FC = () => (
  <AppPage>
    <PageHeader
      eyebrow="策略总览"
      title="策略管理"
      description="全部内置策略的品种、实盘能力与运行状态总览；实盘开关与交易日自动运行时刻在此管理，参数配置仍集中在实盘页。"
    />
    <StrategyManagementPanel className="mt-4" />
  </AppPage>
);

export default StrategiesPage;
