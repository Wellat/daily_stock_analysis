/* eslint-disable no-irregular-whitespace */
import { useEffect, useState } from 'react';
import { Button, Card, Input, Select, Switch, Table, Tabs, Tag } from 'antd';
import { liveStrategyApi, type LiveStrategyConfig, type LiveStrategyDefinition, type LiveStrategyPreview, type LiveStrategyRun, type LiveStrategyDecision, type LiveStrategyOrder, type LiveStrategyBatch, type LiveStrategySyncStatus, type LiveStrategyRunMode } from '../../api/liveStrategy';
import { LiveRunDetail } from './LiveRunDetail';
import { runModeTag, runStatusTag, skipReasonLabel } from './liveRunTags';

const RUN_MODE_OPTIONS: Array<{ value: LiveStrategyRunMode; label: string }> = [
  { value: 'auto', label: '自动' },
  { value: 'rebalance', label: '调仓' },
  { value: 'event_check', label: '事件检查' },
];

export const LiveStrategyPanel: React.FC = () => {
  const [config,setConfig]=useState<LiveStrategyConfig>({strategyId:'low-premium',strategyVersion:'v1',qmtAccount:'testS',enabled:false,symbols:[],parameters:{}});
  const [defs,setDefs]=useState<LiveStrategyDefinition[]>([]); const [preview,setPreview]=useState<LiveStrategyPreview|null>(null); const [runs,setRuns]=useState<LiveStrategyRun[]>([]); const [batches,setBatches]=useState<LiveStrategyBatch[]>([]); const [sync,setSync]=useState<LiveStrategySyncStatus|null>(null); const [selected,setSelected]=useState<LiveStrategyRun|null>(null); const [details,setDetails]=useState<{decisions:LiveStrategyDecision[];orders:LiveStrategyOrder[]}>({decisions:[],orders:[]}); const [detailLoading,setDetailLoading]=useState(false); const [message,setMessage]=useState(''); const [runMode,setRunMode]=useState<LiveStrategyRunMode>('auto'); const [activeTab,setActiveTab]=useState('config');
  const refresh=async()=>{const [c,d,r,b,s]=await Promise.all([liveStrategyApi.getConfig(),liveStrategyApi.listStrategies(),liveStrategyApi.listRuns(),liveStrategyApi.listBatches(),liveStrategyApi.syncStatus()]);setConfig(c);setDefs(d);setRuns(r);setBatches(b);setSync(s);}; useEffect(()=>{void refresh().catch(()=>undefined);},[]);
  const def=defs.find(d=>d.strategyId===config.strategyId); const act=async(fn:()=>Promise<unknown>,msg:string)=>{try{const x=await fn();if(x&&typeof x==='object'&&'rebalance' in x)setPreview(x as LiveStrategyPreview);if(msg==='配置已保存'&&x&&typeof x==='object'&&'strategyId' in x)setConfig(x as LiveStrategyConfig);setMessage(msg);if(msg!=='配置已保存')await refresh();}catch(e){setMessage(e instanceof Error?e.message:'操作失败');}}; const selectRun=async(r:LiveStrategyRun)=>{setSelected(r);setDetailLoading(true);try{const [decisions,orders]=await Promise.all([liveStrategyApi.listDecisions(r.id),liveStrategyApi.listOrders(r.id)]);setDetails({decisions,orders});}finally{setDetailLoading(false);}};
  const openBatchRun=(batch:LiveStrategyBatch)=>{const run=runs.find(r=>r.id===batch.runId);if(!run)return;void selectRun(run);setActiveTab('runs');};
  const syncItem=(x:LiveStrategySyncStatus['intraday'])=><span><Tag color={x?.status==='completed'?'green':x?.status==='failed'?'red':'orange'}>{x?.status||'未运行'}</Tag>{x?.completedAt&&<small>{new Date(x.completedAt).toLocaleTimeString()}</small>}{x?.errorMessage&&<small className="text-red-600">：{x.errorMessage}</small>}</span>;
  const configTab=<Card><div className="grid gap-3 sm:grid-cols-3"><Select value={config.qmtAccount} options={[{value:'testS',label:'testS'},{value:'135129739',label:'135129739'}]} onChange={qmtAccount=>setConfig({...config,qmtAccount})}/><Select value={config.strategyId} options={defs.map(d=>({value:d.strategyId,label:d.name}))} onChange={strategyId=>setConfig({...config,strategyId})}/><label>自动实盘 <Switch checked={config.enabled} onChange={enabled=>setConfig({...config,enabled})}/></label></div><p>{def?.description}</p><div className="grid gap-2 sm:grid-cols-4">{(def?.parameters||[]).map(p=><Input key={p.key} type="number" addonBefore={p.label} value={String(config.parameters[p.key]??p.default??'')} onChange={e=>setConfig({...config,parameters:{...config.parameters,[p.key]:Number(e.target.value)}})}/>)}</div><div className="mt-2 grid gap-2 sm:grid-cols-4"><Input type="number" addonBefore="调仓频率(交易日)" value={String(config.rebalanceFrequencyDays??1)} onChange={e=>setConfig({...config,rebalanceFrequencyDays:Math.max(1,Number(e.target.value)||1)})}/><Input addonBefore="自选池" placeholder="逗号分隔，留空=全市场" value={config.symbols.join(',')} onChange={e=>setConfig({...config,symbols:e.target.value.split(',').map(s=>s.trim()).filter(Boolean)})}/><label>下单前数据同步检查 <Switch checked={config.dataSyncBeforeRun??true} onChange={dataSyncBeforeRun=>setConfig({...config,dataSyncBeforeRun})}/></label></div><div className="mt-3 flex flex-wrap items-center gap-2"><Button onClick={()=>void act(()=>liveStrategyApi.saveConfig(config),'配置已保存')}>保存配置</Button><Select className="w-28" value={runMode} options={RUN_MODE_OPTIONS} onChange={m=>setRunMode(m as LiveStrategyRunMode)}/><Button onClick={()=>void act(()=>liveStrategyApi.preview(undefined,runMode),'预览已生成')}>预览</Button><Button type="primary" disabled={!config.enabled} onClick={()=>void act(()=>liveStrategyApi.run(undefined,runMode),'订单已生成')}>生成实盘订单</Button>{config.nextRebalanceDate&&<small>下次调仓：{config.nextRebalanceDate}</small>}{message&&<Tag>{message}</Tag>}</div></Card>;
  const previewTab=preview?<>{preview.skipReason&&<Card className="mb-3"><Tag color="orange">未生成调仓</Tag><span className="ml-2 text-secondary-text">{skipReasonLabel(preview.skipReason)}</span></Card>}<Table rowKey={(r)=>`${r.side}-${r.symbol}`} dataSource={preview.rebalance} locale={{emptyText:'无调仓记录'}} columns={[{title:'标的',dataIndex:'symbol'},{title:'方向',dataIndex:'side'},{title:'数量',dataIndex:'quantity'},{title:'原因',dataIndex:'reason'}]}/>{preview.diagnostics&&<Card className="mt-3"><b>风控诊断</b><pre className="text-xs">{JSON.stringify(preview.diagnostics,null,2)}</pre></Card>}</>:<Card>请先预览</Card>;
  const runsTab=<><Table size="small" rowKey="id" dataSource={runs} onRow={r=>({onClick:()=>void selectRun(r),style:{cursor:'pointer'}})} pagination={{pageSize:10,hideOnSinglePage:true}} columns={[{title:'日期',dataIndex:'tradeDate',width:110},{title:'模式',dataIndex:'mode',width:100,render:(v:string)=>runModeTag(v)},{title:'策略',dataIndex:'strategyId',width:120,render:(v:string|undefined)=>v||'-'},{title:'状态',dataIndex:'status',width:90,render:(v:string)=>runStatusTag(v)},{title:'决策数',dataIndex:'decisionCount',width:80,align:'right',render:(v:number|undefined)=>v??'-'},{title:'订单数',dataIndex:'orderCount',width:80,align:'right',render:(v:number|undefined)=>v??'-'},{title:'完成时间',dataIndex:'completedAt',width:160,render:(v:string|null|undefined)=>v||'-'}]}/>{selected&&<LiveRunDetail run={selected} decisions={details.decisions} orders={details.orders} batch={batches.find(b=>b.runId===selected.id)??null} loading={detailLoading} onClose={()=>setSelected(null)}/>}</>;
  const batchProgress = (b: LiveStrategyBatch) => {
    const o = b.orders;
    if (!o || o.total === 0) return <span className="text-xs text-secondary-text">无订单</span>;
    const done = o.filled === o.total;
    return (
      <span className="flex items-center gap-1">
        {done ? <Tag color="success">已完成</Tag> : (o.rejected > 0 || o.cancelled > 0) ? <Tag color="error">有失败</Tag> : <Tag color="processing">进行中</Tag>}
        <span className="text-xs text-secondary-text">{o.filled}/{o.total} 成交{o.rejected ? `，${o.rejected} 失败` : ''}</span>
      </span>
    );
  };
  const batchesTab = (
    <Table size="small" rowKey="id" dataSource={batches} pagination={{ pageSize: 10, hideOnSinglePage: true }} locale={{ emptyText: '暂无批次' }}
      onRow={b => ({ onClick: () => openBatchRun(b), style: { cursor: 'pointer' } })}
      columns={[
        { title: '创建时间', dataIndex: 'createdAt', width: 160, render: (v: string | null | undefined) => v || '-' },
        { title: '批次', dataIndex: 'batchUid', width: 130, render: (v: string) => `${v.slice(0, 8)}…` },
        { title: '关联运行', width: 190, render: (_: unknown, b: LiveStrategyBatch) => <span className="flex items-center gap-1">{b.tradeDate || '-'}{runModeTag(b.mode ?? undefined)}</span> },
        { title: '账户', dataIndex: 'qmtAccount', width: 110 },
        { title: '订单进度', width: 200, render: (_: unknown, b: LiveStrategyBatch) => batchProgress(b) },
        { title: '批次状态', dataIndex: 'status', width: 90, render: (v: string) => (v === 'pending' ? <Tag>待跟踪</Tag> : <Tag color="processing">{v}</Tag>) },
      ]} />);
  return <><Card className="mb-3"><div className="flex justify-between"><b>数据同步状态（{sync?.tradeDate||'-'}）</b><Button size="small" onClick={()=>void refresh()}>刷新</Button></div><div className="mt-2 grid grid-cols-2">盘中：{syncItem(sync?.intraday)}　盘后：{syncItem(sync?.afterClose)}</div></Card><Tabs activeKey={activeTab} onChange={setActiveTab} items={[{key:'config',label:'策略配置',children:configTab},{key:'preview',label:'调仓预览',children:previewTab},{key:'runs',label:'运行记录',children:runsTab},{key:'batches',label:'调仓批次',children:batchesTab}]} /> </>;
};
