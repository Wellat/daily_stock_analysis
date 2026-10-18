/* eslint-disable no-irregular-whitespace */
import { useEffect, useState } from 'react';
import { Button, Card, Input, Select, Switch, Table, Tabs, Tag } from 'antd';
import { liveStrategyApi, LIVE_ACCOUNT_OPTIONS, type LiveStrategyConfig, type LiveStrategyDefinition, type LiveStrategyPreview, type LiveStrategyRun, type LiveStrategyDecision, type LiveStrategyOrder, type LiveStrategyBatch, type LiveStrategySyncStatus, type LiveStrategyRunMode } from '../../api/liveStrategy';
import { LiveRunDetail } from './LiveRunDetail';
import { runModeTag, runStatusTag, skipReasonLabel } from './liveRunTags';

const RUN_MODE_OPTIONS: Array<{ value: LiveStrategyRunMode; label: string }> = [
  { value: 'auto', label: '自动' },
  { value: 'rebalance', label: '调仓' },
  { value: 'event_check', label: '事件检查' },
];

export const LiveStrategyPanel: React.FC = () => {
  const [config,setConfig]=useState<LiveStrategyConfig>({strategyId:'low-premium',strategyVersion:'v1',qmtAccount:'testS',enabled:false,symbols:[],parameters:{}});
  const [orderInstances,setOrderInstances]=useState<LiveStrategyConfig[]>([]);
  const [selectedIdRef,setSelectedIdRef]=useState<{value:number|null}>({value:null});
  const [defs,setDefs]=useState<LiveStrategyDefinition[]>([]); const [preview,setPreview]=useState<LiveStrategyPreview|null>(null); const [runs,setRuns]=useState<LiveStrategyRun[]>([]); const [batches,setBatches]=useState<LiveStrategyBatch[]>([]); const [sync,setSync]=useState<LiveStrategySyncStatus|null>(null); const [selected,setSelected]=useState<LiveStrategyRun|null>(null); const [details,setDetails]=useState<{decisions:LiveStrategyDecision[];orders:LiveStrategyOrder[]}>({decisions:[],orders:[]}); const [detailLoading,setDetailLoading]=useState(false); const [message,setMessage]=useState(''); const [runMode,setRunMode]=useState<LiveStrategyRunMode>('auto'); const [activeTab,setActiveTab]=useState('config');
  const refresh=async()=>{const [d,allCfg,r,b,s]=await Promise.all([liveStrategyApi.listStrategies(),liveStrategyApi.listConfigs(),liveStrategyApi.listRuns(),liveStrategyApi.listBatches(),liveStrategyApi.syncStatus()]);setDefs(d);
    const orderIds=new Set(d.filter(x=>x.liveMode==='order').map(x=>x.strategyId));
    const instances=allCfg.filter(c=>orderIds.has(c.strategyId));
    setOrderInstances(instances);
    // 保持当前选择；缺省取已启用（或最新）的实盘实例
    const next=instances.find(c=>c.id===selectedIdRef.value)??instances.find(c=>c.enabled)??instances[0]??null;
    if(next){setSelectedIdRef({value:next.id??null});setConfig(next);}
    setRuns(r);setBatches(b);setSync(s);}; useEffect(()=>{void refresh().catch(()=>undefined);},[]);
  const def=defs.find(d=>d.strategyId===config.strategyId); const act=async(fn:()=>Promise<unknown>,msg:string)=>{try{const x=await fn();if(x&&typeof x==='object'&&'rebalance' in x)setPreview(x as LiveStrategyPreview);if(msg==='配置已保存'&&x&&typeof x==='object'&&'strategyId' in x)setConfig(x as LiveStrategyConfig);setMessage(msg);if(msg!=='配置已保存')await refresh();}catch(e){setMessage(e instanceof Error?e.message:'操作失败');}}; const selectRun=async(r:LiveStrategyRun)=>{setSelected(r);setDetailLoading(true);try{const [decisions,orders]=await Promise.all([liveStrategyApi.listDecisions(r.id),liveStrategyApi.listOrders(r.id)]);setDetails({decisions,orders});}finally{setDetailLoading(false);}};
  const saveCurrent=async()=>{ if(config.id){ await liveStrategyApi.updateInstance(config.id,{name:config.name?.trim()||'实盘策略',description:config.description??null,strategyId:config.strategyId,qmtAccount:config.qmtAccount,enabled:config.enabled,symbols:config.symbols,parameters:config.parameters,scheduleTime:config.scheduleTime??null,rebalanceFrequencyDays:config.rebalanceFrequencyDays??null,eventCheckEnabled:config.eventCheckEnabled??null,dataSyncBeforeRun:config.dataSyncBeforeRun??null,lastTradingDayExitEnabled:config.lastTradingDayExitEnabled??null,lastTradingDayExitBufferDays:config.lastTradingDayExitBufferDays??null}); } else { await liveStrategyApi.saveConfig(config); } };
  const openBatchRun=(batch:LiveStrategyBatch)=>{const run=runs.find(r=>r.id===batch.runId);if(!run)return;void selectRun(run);setActiveTab('runs');};
  const syncItem=(x:LiveStrategySyncStatus['intraday'])=><span><Tag color={x?.status==='completed'?'green':x?.status==='failed'?'red':'orange'}>{x?.status||'未运行'}</Tag>{x?.completedAt&&<small>{new Date(x.completedAt).toLocaleTimeString()}</small>}{x?.errorMessage&&<small className="text-red-600">：{x.errorMessage}</small>}</span>;
  const configTab=<Card>
    {orderInstances.length?(<div className="mb-2 flex flex-wrap items-center gap-2"><span className="text-sm">实盘实例</span><Select className="min-w-52" value={config.id} options={orderInstances.map(i=>({value:i.id,label:`${i.name??('实例'+String(i.id))}（${defs.find(d=>d.strategyId===i.strategyId)?.name??i.strategyId}）`}))} onChange={id=>{const inst=orderInstances.find(i=>i.id===id);if(inst){setSelectedIdRef({value:id});setConfig(inst);}}}/>{config.enabled?<Tag color="success">自动实盘 {config.scheduleTime??'14:35'}</Tag>:<Tag>未启用</Tag>}</div>):(<p className="mb-2 text-sm text-secondary-text">暂无实盘实例：去「策略管理」页从模板创建（保存配置将创建默认实例）。</p>)}
    <div className="grid gap-3 sm:grid-cols-3"><Select value={config.qmtAccount} options={LIVE_ACCOUNT_OPTIONS} onChange={qmtAccount=>setConfig({...config,qmtAccount})}/><Select value={config.strategyId} disabled={Boolean(config.id)} options={defs.filter(d=>d.liveMode==='order').map(d=>({value:d.strategyId,label:d.name}))} onChange={strategyId=>setConfig({...config,strategyId})}/><label>自动实盘 <Switch checked={config.enabled} onChange={enabled=>setConfig({...config,enabled})}/></label></div><p>{def?.description}{config.id?<span className="text-secondary-text">（实例模板创建后不可更换，换模板请在策略管理页新建实例）</span>:''}</p><div className="grid gap-2 sm:grid-cols-4">{(def?.parameters||[]).map(p=>p.type==='boolean'
? <label key={p.key}>{p.label} <Switch checked={Boolean(config.parameters[p.key]??p.default)} onChange={v=>setConfig({...config,parameters:{...config.parameters,[p.key]:v}})}/></label>
: <Input key={p.key} type="number" addonBefore={p.label} value={String(config.parameters[p.key]??p.default??'')} onChange={e=>setConfig({...config,parameters:{...config.parameters,[p.key]:Number(e.target.value)}})}/>)}</div><div className="mt-2 grid gap-2 sm:grid-cols-4"><Input type="number" addonBefore="调仓频率(交易日)" value={String(config.rebalanceFrequencyDays??1)} onChange={e=>setConfig({...config,rebalanceFrequencyDays:Math.max(1,Number(e.target.value)||1)})}/><Input addonBefore="自选池" placeholder="逗号分隔，留空=全市场" value={config.symbols.join(',')} onChange={e=>setConfig({...config,symbols:e.target.value.split(',').map(s=>s.trim()).filter(Boolean)})}/><label>下单前数据同步检查 <Switch checked={config.dataSyncBeforeRun??true} onChange={dataSyncBeforeRun=>setConfig({...config,dataSyncBeforeRun})}/></label><label>非调仓日事件检查 <Switch checked={config.eventCheckEnabled??true} onChange={eventCheckEnabled=>setConfig({...config,eventCheckEnabled})}/></label><Input type="number" addonBefore="最后交易日提前量(交易日)" value={String(config.lastTradingDayExitBufferDays??1)} onChange={e=>setConfig({...config,lastTradingDayExitBufferDays:Math.max(0,Number(e.target.value)||0)})}/><label>最后交易日强制卖出 <Switch checked={config.lastTradingDayExitEnabled??true} onChange={lastTradingDayExitEnabled=>setConfig({...config,lastTradingDayExitEnabled})}/></label><Input addonBefore="自动运行时刻" placeholder="14:35" value={config.scheduleTime??''} onChange={e=>setConfig({...config,scheduleTime:e.target.value})}/></div><div className="mt-3 flex flex-wrap items-center gap-2"><Button onClick={()=>void act(saveCurrent,'配置已保存')}>保存配置</Button><Select className="w-28" value={runMode} options={RUN_MODE_OPTIONS} onChange={m=>setRunMode(m as LiveStrategyRunMode)}/><Button onClick={()=>void act(()=>liveStrategyApi.preview(undefined,runMode,config.id),'预览已生成')}>预览</Button><Button type="primary" disabled={!config.enabled} onClick={()=>void act(()=>liveStrategyApi.run(undefined,runMode,config.id),'订单已生成')}>生成实盘订单</Button>{config.nextRebalanceDate&&<small>下次调仓：{config.nextRebalanceDate}</small>}{message&&<Tag>{message}</Tag>}</div></Card>;
  const previewTab=preview?<>{preview.skipReason&&<Card className="mb-3"><Tag color="orange">未生成调仓</Tag><span className="ml-2 text-secondary-text">{skipReasonLabel(preview.skipReason)}</span></Card>}<Table rowKey={(r)=>`${r.side}-${r.symbol}`} dataSource={preview.rebalance} locale={{emptyText:'无调仓记录'}} columns={[{title:'标的',dataIndex:'symbol',render:(_,r)=>r.symbolName?`${r.symbolName}（${r.symbol}）`:r.symbol},{title:'方向',dataIndex:'side',width:80,render:side=><Tag color={side==='buy'?'success':'error'}>{side==='buy'?'买入':'卖出'}</Tag>},{title:'溢价率',dataIndex:'premiumRate',width:90,render:v=>v!=null?`${v.toFixed(2)}%`:'-'},{title:'数量',dataIndex:'quantity'},{title:'原因',dataIndex:'reason'}]}/>{preview.diagnostics&&<Card className="mt-3"><b>风控诊断</b><pre className="text-xs">{JSON.stringify(preview.diagnostics,null,2)}</pre></Card>}</>:<Card>请先预览</Card>;
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
