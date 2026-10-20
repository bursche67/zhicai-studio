<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick, defineComponent, h } from 'vue'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import {pageFromHash, readState, parseSuppliers, validationMessage, stateKey, statusLabel, queryTimingReport, groupTaskHistory, taskClock} from './ui-state'
echarts.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

let saved = {}
try { saved = readState(sessionStorage) } catch {}
const page = ref(pageFromHash(location.hash)), mode = ref(['rule','model'].includes(saved.mode)?saved.mode:'model'), sample = ref(saved.sample===true), health = ref({})
const error = ref(''), busy = ref(false), jobs = ref([]), job = ref(null), selectedFiles = ref([]), association = ref('')
const taskHistoryOpen=ref(true), jobDocumentsOpen=ref(false)
try {taskHistoryOpen.value=sessionStorage.getItem('zhicai-task-history-open')!=='false'} catch {}
watch(taskHistoryOpen,value=>{try{sessionStorage.setItem('zhicai-task-history-open',String(value))}catch{}})
const taskHistory=computed(()=>groupTaskHistory(jobs.value))
const activeTaskCount=computed(()=>jobs.value.filter(j=>['running','queued'].includes(j.status)).length)
const jobPanel=ref(null), projectPanel=ref(null), analysisPanel=ref(null), revealedPanel=ref(''), actionMessage=ref('')
const hasReturnTarget=ref(false)
let returnButton=null, projectRequest=0
async function revealPanel(panel, name, trigger=null){
  if(trigger instanceof HTMLElement){returnButton=trigger;hasReturnTarget.value=true}
  await nextTick()
  if(!panel.value)return
  revealedPanel.value=name
  panel.value.querySelector('h2')?.focus({preventScroll:true})
  panel.value.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'})
}
function returnToAction(){returnButton?.focus({preventScroll:true});returnButton?.scrollIntoView({behavior:'smooth',block:'center'})}
async function openJob(id,event){const trigger=event?.currentTarget;await act(async()=>{actionMessage.value='正在加载任务详情…';await viewJob(id);if(page.value!=='upload')return;await revealPanel(jobPanel,'job',trigger);actionMessage.value='已打开任务详情'})}
async function openProject(id,event){const trigger=event?.currentTarget;await act(async()=>{const n=++projectRequest;actionMessage.value='正在加载项目与原文证据…';const data=await request('/projects/'+id);if(n!==projectRequest||page.value!=='items')return;detail.value=data;await revealPanel(projectPanel,'project',trigger);actionMessage.value='已打开项目与原文证据'})}
const accountBalance=ref(null), balanceLoading=ref(false), balanceError=ref('')
const oldBudgetFailure=computed(()=>accountBalance.value?.internal_budget?.enabled===false&&job.value?.documents?.some(d=>d.errors?.some(e=>e.error?.includes('预算预留额度不足'))))
function accountMoney(row,field='total_balance'){return (row.currency==='CNY'?'¥':'US$')+Number(row[field]).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:4})}
function balanceTime(value){return value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'尚未成功查询'}
async function refreshBalance(manual=false){
  if(balanceLoading.value)return
  balanceLoading.value=true;balanceError.value=''
  try{accountBalance.value=await request('/model-balance'+(manual?'?refresh=true':''),{cache:'no-store'})}
  catch(e){balanceError.value=e.message;if(accountBalance.value)accountBalance.value={...accountBalance.value,status:'unavailable',is_available:null,stale:!!accountBalance.value.checked_at}}
  finally{balanceLoading.value=false}
}
const keyword = ref(typeof saved.keyword==='string'?saved.keyword:''), category = ref(typeof saved.category==='string'?saved.category:''), brand = ref(typeof saved.brand==='string'?saved.brand:''), items = ref({items:[],total:0}), currentPage = ref(Number.isInteger(saved.currentPage)&&saved.currentPage>0?saved.currentPage:1), detail = ref(null)
const scene = ref([1,2,3,4,5].includes(saved.scene)?saved.scene:1), buyer = ref(typeof saved.buyer==='string'?saved.buyer:''), supplier = ref(typeof saved.supplier==='string'?saved.supplier:''), suppliers = ref(typeof saved.suppliers==='string'?saved.suppliers:''), orgs = ref([]), result = ref(null), elapsed = ref(null)
const graphEl = ref(null), graphFilter = ref(typeof saved.graphFilter==='string'?saved.graphFilter:''), graphInfo = ref(null)
const analysisOptions = ref(null), candidateSupplier = ref('')
const qualityInfo=ref(null), capabilityInfo=ref(null), sourceMapText=ref(''), evaluationFile=ref(null), evaluationResult=ref(null)
const rangeFilters=ref(Object.fromEntries(['unit_price_min','unit_price_max','total_price_min','total_price_max','quantity_min','quantity_max'].map(k=>[k,typeof saved.rangeFilters?.[k]==='string'?saved.rangeFilters[k]:''])))
const rangeLabels={unit_price_min:'单价下限（元）',unit_price_max:'单价上限（元）',total_price_min:'总价下限（元）',total_price_max:'总价上限（元）',quantity_min:'数量下限',quantity_max:'数量上限'}
const timingRun=ref(crypto.randomUUID()), timingSummary=ref(null), mappingFeedback=ref('')
let qualityRequest=0,evaluationRequest=0
const winnerOptions = computed(()=>(analysisOptions.value?.suppliers||[]).filter(o=>o.award_project_count>0))
let chart, poll, balancePoll, resizeObserver
let ready = false, graphRequest = 0, resultRequest = 0, itemsRequest = 0, orgsRequest = 0
const scenes = ['长期合作供应商','采购单位投标主体与组合','供应商共同竞标主体','共同合作采购单位','共同参与项目与结果']
const pageDescriptions={upload:'导入、抽取与处理进度，集中管理。',items:'检索商品与项目，回到原文核对细节。',analysis:'五种分析视角，理解采购与供应关系。',graph:'以企业和项目为线索，探索真实的业务网络。'}
const iconPaths={upload:['M12 16V4m-4 4 4-4 4 4','M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4'],items:['M10.5 18a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15Z','m16 16 5 5'],analysis:['M4 20V10m8 10V4m8 16v-7'],graph:['M9 5h6m3 3v8m-3 3H9M6 16V8','M6 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm12 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM6 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm12 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z'],arrow:['M5 12h14m-5-5 5 5-5 5'],shield:['m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z','m8 12 3 3 5-6']}
const AppIcon=defineComponent({props:{name:String},setup:props=>()=>h('svg',{viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':1.65,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',class:'app-icon'},(iconPaths[props.name]||iconPaths.upload).map(d=>h('path',{d})))})
const params = computed(()=>new URLSearchParams({mode:mode.value,sample:String(sample.value)}))
const modelStatus = computed(()=>!health.value.model_configured?'模型尚未配置，模型抽取不会产生虚构结果。':health.value.model_remote&&!health.value.remote_processing_enabled?'模型已配置；远端批量调用关闭，需明确预算后开启。':'模型已配置；可创建抽取任务。')
const modelBlocked = computed(()=>mode.value==='model' && health.value.model_processing_available!==true)
let polling = false
const exportUrl = computed(()=>'/api/export/standard.xlsx?'+params.value+(job.value ? '&job_id='+job.value.id : ''))

async function request(path, options={}) {
  let response
  try { response = await fetch('/api'+path,options) }
  catch { throw new Error('网络连接失败，当前操作结果尚未确认。请检查连接并刷新；上传请先查看任务列表，避免重复提交。') }
  if (!response.ok) {
    let body; try {body=await response.json()} catch {body={detail:response.statusText}}
    throw new Error(validationMessage(body.detail))
  }
  return response.json()
}
async function act(fn) {error.value='';busy.value=true;try {await fn()}catch(e){error.value=e.message;actionMessage.value='操作未完成：'+e.message;await nextTick();document.querySelector('[role="alert"]')?.scrollIntoView({block:'center',behavior:'smooth'})}finally{busy.value=false}}
function money(v){return v==null?'未披露':(Number(v)/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})+' 元'}
const labels={supplier:'供应商',supplier_a:'主体A',supplier_b:'主体B',cooperation_count:'合作项目数',frequency:'参与项目数',known_amount_cents:'已知金额',missing_amount_count:'金额缺失数',known_award_amount_cents:'项目总成交额',missing_award_amount_count:'成交额缺失项目数',buyer:'采购单位',project_count:'项目数',project_name:'项目名称',name:'名称',number:'项目编号',project_amount:'项目预算',award_amount:'中标金额',package_code:'包号',package_name:'采购包',result:'竞标结果',brand:'品牌',category:'品目',specification:'规格型号',quantity:'数量',unit_price:'单价',total_price:'总价',review_status:'通过审查',bid_price:'投标报价',score:'综合得分',loss_reason:'未中标原因',product_supplier:'产品供应商',consortium:'联合体',consortium_award_amount:'联合体成交额',consortium_score:'联合体得分'}
const resultLabels={won:'中标',lost:'未中标',unknown:'未知',invalid:'未通过审查',cancelled:'取消'}
const ResultTable=defineComponent({props:{rows:{type:Array,default:()=>[]},columns:Array},setup(props){return()=>{
  if(!props.rows.length)return h('p',{class:'empty'},'暂无结果')
  const keys=props.columns||Object.keys(props.rows[0]).filter(k=>!['id','identity_key','buyer_id','mode','is_sample','warnings','project_id','supplier_id'].includes(k))
  const display=(key,v)=>v==null?'—':(key.endsWith('_cents')||['project_amount','award_amount','unit_price','total_price','bid_price','consortium_award_amount'].includes(key))?money(v):key==='result'?resultLabels[v]||v:typeof v==='object'?JSON.stringify(v):String(v)
  return h('div',{class:'table-wrap'},h('table',[h('thead',h('tr',keys.map(k=>h('th',labels[k]||k)))),h('tbody',props.rows.map(r=>h('tr',keys.map(k=>h('td',display(k,r[k]))))))]))
}}})

async function viewJob(id){const previousId=job.value?.id;job.value=await request('/imports/'+id);if(job.value.reused_job_id)job.value=await request('/imports/'+job.value.reused_job_id);if(previousId!==job.value.id)jobDocumentsOpen.value=false}
async function refreshJobs(){jobs.value=await request('/imports');if(job.value)await viewJob(job.value.id)}
async function upload(){await act(async()=>{
  if(modelBlocked.value)throw new Error(health.value.model_unavailable_reason||'模型暂不可用，请选择规则基线；已有模型结果仍可查询。')
  if(!selectedFiles.value.length)throw new Error('请选择HTML或ZIP文件')
  const form=new FormData();for(const f of selectedFiles.value)form.append('files',f)
  form.append('mode',mode.value);form.append('sample',String(sample.value));if(association.value.trim())form.append('association',association.value)
  const created=await request('/imports',{method:'POST',body:form});await viewJob(created.id);await refreshJobs();await revealPanel(jobPanel,'job');actionMessage.value='上传已接收，已定位到任务进度'
})}
async function loadItems(){await act(async()=>{const requestId=++itemsRequest;const p=new URLSearchParams(params.value);p.set('q',keyword.value);p.set('category',category.value);p.set('brand',brand.value);p.set('page',String(currentPage.value));for(const [k,v] of Object.entries(rangeFilters.value))if(String(v).trim())p.set(k,String(v).trim());const data=await request('/items?'+p);if(requestId===itemsRequest)items.value=data})}
async function loadQuality(){await act(async()=>{const id=++qualityRequest;const [data,cap]=await Promise.all([request('/quality?'+params.value),request('/capabilities')]);if(id!==qualityRequest)return;qualityInfo.value=data;capabilityInfo.value=cap})}
async function saveSourceMap(){await act(async()=>{mappingFeedback.value='';const scope=params.value.toString();let body;try{body=JSON.parse(sourceMapText.value)}catch{throw new Error('来源映射必须是合法JSON，不能从UUID猜造网址。')}const mapped=await request('/sources/map?'+scope,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(scope===params.value.toString()){mappingFeedback.value='已保存 '+mapped.mapping_count+' 条来源映射；更新 '+mapped.updated_document_versions+' 份存储版本。';qualityInfo.value=await request('/quality?'+scope)}})}
async function runEvaluation(){await act(async()=>{const id=++evaluationRequest;evaluationResult.value=null;if(!evaluationFile.value)throw new Error('请先选择已人工核验的金标JSON');const form=new FormData();form.append('gold',evaluationFile.value);form.append('mode',mode.value);form.append('sample',String(sample.value));const data=await request('/evaluation',{method:'POST',body:form});if(id===evaluationRequest)evaluationResult.value=data})}
function downloadJson(value,name){const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();URL.revokeObjectURL(url)}
async function loadOrgs(){const requestId=++orgsRequest;const data=await request('/analysis/options?'+params.value);if(requestId!==orgsRequest)return;analysisOptions.value=data;orgs.value=[...data.buyers,...data.suppliers];if(!buyer.value&&data.buyers.length)buyer.value=data.buyers[0].name;if(!supplier.value&&winnerOptions.value.length)supplier.value=winnerOptions.value[0].name}
function addSupplier(){if(!candidateSupplier.value)return;const names=suppliers.value.split(/[\n;；]/).map(x=>x.trim()).filter(Boolean);if(!names.includes(candidateSupplier.value))names.push(candidateSupplier.value);suppliers.value=names.join('\n');candidateSupplier.value='';error.value=''}
async function runScene(){await act(async()=>{
  const requestId=++resultRequest
  const p=new URLSearchParams(params.value);let url,options={}
  if(scene.value===1||scene.value===2){if(!buyer.value.trim())throw new Error('填写采购单位全称');p.set('buyer',buyer.value.trim());url=scene.value===1?'cooperation':'buyer-bidders'}
  else if(scene.value===3){if(!supplier.value.trim())throw new Error('填写供应商全称');p.set('supplier',supplier.value.trim());url='supplier-competitors'}
  else {const names=parseSuppliers(suppliers.value);url=scene.value===4?'common-buyers':'common-projects';options={method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({suppliers:names})}}
  const measuredRun=timingRun.value;const measuredScene=scene.value;const measuredMode=mode.value;const measuredSample=sample.value;const started=performance.now();const data=await request('/analysis/'+url+'?'+p,options);if(requestId!==resultRequest)return;result.value=data;await nextTick();if(page.value!=='analysis')return;await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))
  elapsed.value=+(performance.now()-started).toFixed(1)
  await revealPanel(analysisPanel,'analysis');actionMessage.value='查询完成，已定位到分析结果'
  try {const records=JSON.parse(localStorage.getItem('zhicai-query-timings')||'[]');records.push({run_id:measuredRun,scene:measuredScene,ms:elapsed.value,mode:measuredMode,sample:measuredSample,query:Object.fromEntries(p),suppliers:options.body?JSON.parse(options.body).suppliers:null,at:new Date().toISOString()});localStorage.setItem('zhicai-query-timings',JSON.stringify(records.slice(-100)));timingSummary.value=queryTimingReport(records,mode.value,sample.value,timingRun.value)} catch {}
})}
async function loadGraph(){await act(async()=>{
  const requestId=++graphRequest
  const p=new URLSearchParams(params.value);if(graphFilter.value)p.set('organization',graphFilter.value)
  const data=await request('/graph?'+p);if(requestId!==graphRequest)return;graphInfo.value=data;await nextTick();if(!graphEl.value||page.value!=='graph')return
  chart?.dispose();chart=echarts.init(graphEl.value);const categories=['主体','项目','采购包','商品']
  chart.setOption({animation:false,color:['#7195bd','#343d4d','#8c91b4','#a8b8ce'],tooltip:{renderMode:'richText',formatter:p=>p.data.relation||p.name,backgroundColor:'#fff',borderColor:'#e8e8ed',textStyle:{color:'#34343a',fontSize:12}},legend:{data:categories,textStyle:{color:'#85858e',fontSize:11}},series:[{type:'graph',layout:'force',roam:true,label:{show:true,fontSize:11},force:{repulsion:260,edgeLength:100,layoutAnimation:false},categories:categories.map(name=>({name})),data:data.nodes.map(n=>({...n,category:categories.indexOf(n.category),symbolSize:n.category==='项目'?38:26})),links:data.links.map(e=>({...e})),edgeLabel:{show:true,formatter:p=>p.data.relation},lineStyle:{color:'#c6ceda',width:1.2},edgeSymbol:['none','arrow']}]})
  resizeObserver?.disconnect();resizeObserver=new ResizeObserver(()=>{if(page.value==='graph'&&graphEl.value?.clientWidth)chart?.resize()});resizeObserver.observe(graphEl.value)
})}
function newTimingRun(){timingRun.value=crypto.randomUUID();timingSummary.value=null}
function downloadTimings(){let records=[];try{records=JSON.parse(localStorage.getItem('zhicai-query-timings')||'[]')}catch{};timingSummary.value=queryTimingReport(records,mode.value,sample.value,timingRun.value);downloadJson(timingSummary.value,'browser-five-scenario-timings.json')}
async function loadPage(){if(page.value==='items')await loadItems();if(page.value==='analysis'){await act(loadOrgs);if(scene.value<=2&&buyer.value&&!result.value)await runScene()}if(page.value==='graph')await loadGraph()}
function restoreHash(){page.value=pageFromHash(location.hash)}
watch([mode,sample],()=>{++projectRequest;returnButton=null;hasReturnTarget.value=false;revealedPanel.value='';actionMessage.value=''})
watch(page,async value=>{++projectRequest;hasReturnTarget.value=false;revealedPanel.value='';actionMessage.value='';returnButton=null;window.scrollTo(0,0);error.value='';if(location.hash!=='#'+value)history.replaceState(null,'','#'+value);if(ready)await loadPage()})
watch([mode,sample],async()=>{++qualityRequest;++evaluationRequest;mappingFeedback.value='';qualityInfo.value=null;evaluationResult.value=null;timingSummary.value=null;timingRun.value=crypto.randomUUID();++resultRequest;++graphRequest;++itemsRequest;++orgsRequest;job.value=null;result.value=null;elapsed.value=null;detail.value=null;items.value={items:[],total:0};orgs.value=[];analysisOptions.value=null;buyer.value='';supplier.value='';suppliers.value='';candidateSupplier.value='';graphInfo.value=null;chart?.clear();currentPage.value=1;if(ready)await loadPage()})
watch(scene,()=>{++resultRequest;result.value=null;elapsed.value=null;error.value=''})
watch([mode,sample,keyword,category,brand,currentPage,scene,buyer,supplier,suppliers,graphFilter],()=>{try{sessionStorage.setItem(stateKey,JSON.stringify({mode:mode.value,sample:sample.value,rangeFilters:rangeFilters.value,keyword:keyword.value,category:category.value,brand:brand.value,currentPage:currentPage.value,scene:scene.value,buyer:buyer.value,supplier:supplier.value,suppliers:suppliers.value,graphFilter:graphFilter.value}))}catch{}})
watch(rangeFilters,()=>{try{sessionStorage.setItem(stateKey,JSON.stringify({...readState(sessionStorage),rangeFilters:rangeFilters.value}))}catch{}},{deep:true})
onMounted(async()=>{window.addEventListener('hashchange',restoreHash);if(!location.hash)history.replaceState(null,'','#'+page.value);await act(async()=>{health.value=await request('/health');if(!saved.mode&&!health.value.model_processing_available)mode.value='rule';await refreshJobs()});ready=true;void refreshBalance();balancePoll=setInterval(()=>{if(page.value==='upload')void refreshBalance()},60000);await loadPage();poll=setInterval(async()=>{if(page.value!=='upload'||polling||!jobs.value.some(j=>['queued','running'].includes(j.status)))return;polling=true;try{await refreshJobs()}catch(e){error.value=e.message}finally{polling=false}},4000)})
onUnmounted(()=>{window.removeEventListener('hashchange',restoreHash);++graphRequest;++resultRequest;clearInterval(poll);clearInterval(balancePoll);chart?.dispose();resizeObserver?.disconnect()})
</script>

<template>
  <div class="layout">
    <div v-if="actionMessage" class="action-feedback" role="status" aria-live="polite">{{actionMessage}}<button type="button" aria-label="关闭操作提示" @click="actionMessage=''">×</button></div>
    <aside><div class="brand-lockup"><div class="brand-symbol"><AppIcon name="graph"/></div><div><div class="brand-mark">知采<span>Studio</span></div><p class="aside-sub">行业数据智能分析</p></div></div>
      <div class="nav-caption">工作空间</div>
      <nav aria-label="功能页面"><button type="button" v-for="[id,title,num] in [['upload','数据上传与处理','01'],['items','实体检索','02'],['analysis','业务关系分析','03'],['graph','关系可视化','04']]" :key="id" :class="{active:page===id}" :aria-current="page===id?'page':undefined" @click="page=id"><AppIcon :name="id"/><span>{{title}}</span><small>{{num}}</small></button></nav>
      <div class="aside-footer"><AppIcon name="shield"/><div>从数据，到洞察。<small>原文留存 · 结果可追溯</small></div></div>
    </aside>
    <main>
      <div class="workspace-bar"><span>知采工作空间 <span class="breadcrumb-slash">/</span> 采购数据平台</span><span class="connection-state"><i :class="{online:health.status==='ok'}"></i>{{health.status==='ok'?'服务已连接':'连接状态待确认'}}</span></div>
      <header><div><div class="eyebrow">PROCUREMENT INTELLIGENCE</div><h1>{{{upload:'数据上传与处理',items:'实体检索',analysis:'业务关系分析',graph:'关系可视化'}[page]}}</h1><p class="page-description">{{pageDescriptions[page]}}</p></div>
        <div class="context"><label>结果来源 <select v-model="mode"><option value="model">国产大模型</option><option value="rule">规则基线</option></select></label><label class="checkbox"><input v-model="sample" type="checkbox">自建样例</label></div>
      </header>
      <div class="notice scope-notice"><AppIcon name="shield"/><span>{{sample?'当前查看自建测试样例，不代表真实业务数据效果。':'当前查看非样例导入数据。'}} {{mode==='rule'?'规则基线能力有限，不代表模型结果。':modelStatus}}</span></div>
      <div v-if="error" role="alert" class="error">{{error}}</div>
      <section v-show="page==='upload'" aria-label="数据上传与处理">
        <div class="panel model-account-panel" aria-label="DeepSeek账户余额">
          <div class="panel-heading"><div><div class="section-kicker">MODEL ACCOUNT</div><h2>DeepSeek 账户余额</h2></div><button type="button" :disabled="balanceLoading" @click="refreshBalance(true)">{{balanceLoading?'查询中…':'刷新余额'}}</button></div>
          <div v-if="accountBalance?.balance_infos?.length" class="account-balances"><div v-for="row in accountBalance.balance_infos" :key="row.currency"><span class="muted">{{accountBalance.stale?'上次查询余额':'官方可用余额'}} · {{row.currency}}</span><strong>{{accountMoney(row)}}</strong><span class="muted">充值余额 {{accountMoney(row,'topped_up_balance')}} · 赠送余额 {{accountMoney(row,'granted_balance')}}</span></div></div>
          <p v-else class="account-unknown">{{balanceLoading?'正在读取官方账户余额…':'当前余额未知'}}</p>
          <p class="account-state"><span v-if="accountBalance?.status==='ok'">{{accountBalance.is_available?'官方账户可调用':'官方账户余额不足'}}</span><span v-if="accountBalance?.internal_budget?.enabled===false">内部预算预留限制已取消</span><span v-else-if="accountBalance?.internal_budget?.enabled">内部预算预留限制仍启用</span></p>
          <p v-if="balanceError||accountBalance?.error" class="notice" role="status">{{balanceError||accountBalance.error}}{{accountBalance?.stale?' 下方数值为上次查询结果，请以刷新后的官方余额为准。':''}}</p>
          <p class="muted">核对时间：{{balanceTime(accountBalance?.checked_at)}}。每60秒更新，可手动刷新。显示金额余额，不估算剩余token；余额查询不会发起模型抽取。</p>
          <p v-if="oldBudgetFailure" class="notice">下方任务含有旧的预算拦截记录。当前内部限制已取消，可点击“重试任务”处理未成功条目；已有成功结果会复用。</p>
        </div>
        <div class="panel import-panel"><div class="panel-heading"><div><div class="section-kicker">IMPORT DATA</div><h2>导入公告与附件</h2></div><span class="quiet-tag">原始文件保留</span></div><p class="panel-description">支持 HTML 公告、批量 HTML ZIP 及同 UUID 附件 ZIP。无法确定归属的附件会提示待关联。</p>
          <label class="upload-zone"><input type="file" multiple accept=".html,.htm,.zip,.pdf,.docx,.xlsx,.doc,.png,.jpg" aria-label="选择公告与附件" @change="selectedFiles=Array.from($event.target.files)"><span class="upload-symbol"><AppIcon name="upload"/></span><strong>{{selectedFiles.length?'已选择 '+selectedFiles.length+' 个文件':'选择公告，开始构建数据。'}}</strong><span class="upload-description">{{selectedFiles.length?selectedFiles.map(f=>f.name).join('、'):'点击选择文件 · 单文件不超过 100 MB'}}</span><span class="file-types">HTML <span>·</span> ZIP <span>·</span> 关联附件</span></label>
          <details><summary>附件关联清单（可选）</summary><textarea v-model="association" rows="4" placeholder='{"groups":[{"html":"notice.html","attachments":["notice.zip/附件.pdf"]}]}'></textarea></details>
          <p v-if="modelBlocked" class="notice">模型抽取暂不可用，已禁用提交。可切换“规则基线”验证流程，或继续检索已有模型结果；配置与预算确认后再启用模型。</p>
          <div class="actions"><button class="primary" :disabled="busy||modelBlocked" @click="upload">{{busy?'提交中…':modelBlocked?'模型暂不可用':'开始解析与抽取'}}</button><a :href="exportUrl">下载标准三表 XLSX</a><a :href="'/api/export/json?'+params">下载结构化JSON</a></div>
        </div>
        <div class="panel task-history-panel">
          <div class="panel-heading"><div><div class="section-kicker">ACTIVITY · TIMELINE</div><h2>处理任务 <span class="count-label">{{jobs.length}}</span></h2></div><button id="task-history-toggle" type="button" :aria-expanded="taskHistoryOpen" aria-controls="task-history-list" @click="taskHistoryOpen=!taskHistoryOpen">{{taskHistoryOpen?'收起记录':'展开记录'}} <span aria-hidden="true">{{taskHistoryOpen?'−':'＋'}}</span></button></div>
          <div class="task-history-summary"><span>{{activeTaskCount?'正在处理 / 排队 '+activeTaskCount+' 个批次':'暂无进行中的批次'}}</span><span>最近 {{jobs.length}} 条 · 北京时间 · 由新到旧</span><button v-if="!taskHistoryOpen&&activeTaskCount" type="button" class="table-action" :disabled="busy" @click="openJob(jobs.find(j=>['running','queued'].includes(j.status)).id,$event)">查看当前进度 →</button></div>
          <div v-show="taskHistoryOpen" id="task-history-list" class="task-timeline">
            <details v-for="(group,index) in taskHistory" :key="group.key" class="task-day" :open="index===0||group.activeCount>0">
              <summary><span class="timeline-date">{{group.label}}</span><span class="quiet-tag">{{group.jobs.length}} 个批次</span><span v-if="group.activeCount" class="timeline-active">{{group.activeCount}} 个进行中</span><span class="timeline-toggle">展开 / 收起</span></summary>
              <div class="table-wrap"><table><thead><tr><th>创建时间</th><th>任务</th><th>方案</th><th>状态</th><th>处理耗时</th><th>操作</th></tr></thead><tbody><tr v-for="j in group.jobs" :key="j.id" :class="{'selected-task':job?.id===j.id}"><td class="task-clock"><time :datetime="j.created_at">{{taskClock(j.created_at)}}</time></td><td class="task-id">{{j.id.slice(0,12)}}</td><td>{{j.mode==='rule'?'规则基线':'模型'}}</td><td><span class="badge" :data-status="j.status">{{statusLabel(j.status)}}</span></td><td>{{j.elapsed_seconds==null?'—':j.elapsed_seconds.toFixed(2)+' 秒'}}</td><td><button class="table-action" :disabled="busy" @click="openJob(j.id,$event)" aria-controls="job-detail">{{job?.id===j.id?'当前详情':'详情'}} <AppIcon name="arrow"/></button></td></tr></tbody></table></div>
            </details>
            <p v-if="!jobs.length" class="empty">还没有处理任务。选择一份公告，即可开始。</p>
          </div>
        </div>
        <div v-if="job" id="job-detail" ref="jobPanel" class="panel detail-panel" :class="{'revealed-panel':revealedPanel==='job'}"><button v-if="hasReturnTarget" type="button" class="return-action" @click="returnToAction">返回刚才的位置 ↑</button><h2 tabindex="-1">任务详情 · {{statusLabel(job.status)}}</h2><div v-if="job.status==='queued'" class="notice" role="status">文件已上传并保存。前方有 {{job.queue_ahead}} 个任务，单队列将依次处理，请勿重复提交。<template v-if="job.active_job"><br>正在处理批次 {{job.active_job.id.slice(0,12)}}<span v-if="job.active_job.current_document">：{{job.active_job.current_document}}</span> <button @click="openJob(job.active_job.id,$event)">查看正在处理的任务</button></template></div><p v-if="job.status==='running'" class="notice" role="status">后台正在处理，本页每4秒更新。已保存成功 {{(job.document_status_counts?.completed||0)+(job.document_status_counts?.duplicate||0)}} 条，部分成功 {{job.document_status_counts?.partial||0}} 条；失败 {{job.document_status_counts?.failed||0}} 条（重试时含上轮待重试记录）。<br>当前处理：{{job.documents.find(d=>['parsing','extracting'].includes(d.status))?.name||'准备文件或保存结果中'}}</p><p>公告/待处理条目 {{job.document_count}}；平均耗时 {{job.average_seconds_per_notice?.toFixed(3)??'—'}} 秒。方案 {{job.mode}} · {{job.sample?'自建样例':'非样例'}}。</p><p v-if="job.error" class="error">{{job.error}}</p><button :disabled="['running','queued'].includes(job.status)" @click="act(async()=>{const retried=await request('/imports/'+job.id+'/retry',{method:'POST'});await viewJob(retried.id);await refreshJobs();await revealPanel(jobPanel,'job');actionMessage='重试已提交，已定位到任务进度'})">重试任务</button><a :href="'/api/export/standard.xlsx?mode='+job.mode+'&sample='+job.sample+'&job_id='+job.id">下载此任务结果</a><a :href="'/api/imports/'+job.id+'/quality'" download="task-quality-and-timing.json">下载此任务质量与耗时报告</a><details class="task-documents" :open="jobDocumentsOpen" @toggle="jobDocumentsOpen=$event.target.open"><summary>逐文件处理记录 <span class="quiet-tag">{{job.documents.length}} 条</span><span class="timeline-toggle">{{jobDocumentsOpen?'点击收起':'点击展开'}}</span></summary><div class="table-wrap"><table><thead><tr><th>源文件 / UUID</th><th>状态</th><th>问题</th></tr></thead><tbody><tr v-for="d in job.documents" :key="d.id"><td>{{d.name}}<small>{{d.source_uuid}}</small></td><td>{{statusLabel(d.status)}}</td><td>{{d.errors.map(e=>e.error).join('；')||'—'}}</td></tr></tbody></table></div></details></div>
        <div class="panel"><div class="panel-heading"><div><div class="section-kicker">VALIDATION</div><h2>数据质量检查</h2></div><button :disabled="busy" @click="loadQuality">检查当前数据</button></div><p class="muted">只检查已入库结果，不调用模型。字段非空率不等于正确率，缺失值不会自动补成答案。</p><template v-if="qualityInfo"><div class="metrics"><div><strong>{{qualityInfo.notices_with_results}}</strong>有结果公告</div><div><strong>{{qualityInfo.item_count}}</strong>商品记录</div><div><strong>{{qualityInfo.missing_url_notices}}</strong>缺少原始URL的公告</div></div><div class="table-wrap"><table><thead><tr><th>七字段</th><th>非空记录</th><th>记录总数</th><th>非空率（非精度）</th></tr></thead><tbody><tr v-for="(f,key) in qualityInfo.seven_fields" :key="key"><td>{{f.label}}</td><td>{{f.non_null}}</td><td>{{f.total}}</td><td>{{f.rate==null?'—':(f.rate*100).toFixed(1)+'%'}}</td></tr></tbody></table></div><details><summary>标准三表蓝色字段缺失检查</summary><p class="muted">蓝色列为评测最小字段集，原文未披露仍允许留空；缺失统计不是错误判定。灰色评分等字段仍保留展示。</p><pre>{{JSON.stringify(qualityInfo.blue_field_missing_counts,null,2)}}</pre><p class="muted">数量非正整数待核验 {{qualityInfo.nonconforming_quantity_rows.length}} 行，保留原值。</p></details><p class="muted" v-if="capabilityInfo">DOC转换：{{capabilityInfo.doc_conversion?'工具已配置':'未配置LibreOffice'}} · 图片/扫描PDF：{{capabilityInfo.ocr_executable?'已检测OCR工具，中文语言包仍需核验':'未配置OCR工具'}}。真实附件效果需另行验收。</p><button @click="downloadJson(qualityInfo,'data-quality-report.json')">下载质量检查报告</button></template><details><summary>补充已核实的公告URL映射</summary><p class="muted">只填写你能核实的原始地址。按当前结果来源与样例范围保存；不自动抓取网页，不从UUID猜造URL。格式：entries数组，每条含uuid和url。</p><textarea v-model="sourceMapText" rows="5" aria-label="公告来源URL映射JSON" placeholder='{"entries":[{"uuid":"公告UUID","url":"https://原始公告地址"}]}'></textarea><button :disabled="busy||!sourceMapText.trim()" @click="saveSourceMap">保存来源映射</button><p v-if="mappingFeedback" role="status" class="muted">{{mappingFeedback}}</p></details><details><summary>使用人工核验金标评测提取准确性</summary><p class="muted">选择包含reviewed=true、dataset_sha256、evaluation_uuids和items七字段的人工核验JSON。系统仅比较同数据版本的已有结果，不发起模型请求；未核验金标会拒绝评分。</p><input type="file" accept=".json" aria-label="人工核验金标JSON" @change="evaluationFile=$event.target.files[0]||null"><div class="actions"><button :disabled="busy||!evaluationFile" @click="runEvaluation">运行本地精度评测</button></div><template v-if="evaluationResult"><p>准确率 {{(evaluationResult.overall.accuracy*100).toFixed(2)}}% · 精确率 {{(evaluationResult.overall.precision*100).toFixed(2)}}% · 召回率 {{(evaluationResult.overall.recall*100).toFixed(2)}}% · 综合值 {{(evaluationResult.overall.composite*100).toFixed(2)}}%</p><p class="muted">{{evaluationResult.method}}。综合值=0.4×准确率+0.3×精确率+0.3×召回率；本地口径，仅供本地质量分析。</p><button @click="downloadJson(evaluationResult,'extraction-evaluation.json')">下载评测报告</button></template></details></div>
      </section>
      <section v-show="page==='items'" aria-label="实体检索">
        <div class="panel"><div class="filters"><input v-model="keyword" placeholder="检索七字段、项目、采购单位或产品供应商" @keyup.enter="currentPage=1;loadItems()"><input v-model="category" placeholder="品目"><input v-model="brand" placeholder="品牌"><button class="primary" @click="currentPage=1;loadItems()">检索</button><a :href="'/api/export/standard.xlsx?'+params">下载标准结果</a></div><details><summary>数值筛选 · 单价、总价与数量</summary><div class="range-filters"><label v-for="(title,key) in rangeLabels" :key="key">{{title}}<input v-model="rangeFilters[key]" inputmode="decimal" :aria-label="title" placeholder="不限"></label></div><div class="actions"><button @click="currentPage=1;loadItems()">应用筛选</button><button @click="Object.keys(rangeFilters).forEach(k=>rangeFilters[k]='');currentPage=1;loadItems()">清空数值筛选</button></div></details></div>
        <div class="panel"><h2>标的物 <span class="muted">{{items.total}} 条</span></h2><div class="table-wrap"><table><thead><tr><th>产品服务名称</th><th>品目</th><th>品牌</th><th>规格型号</th><th>单价</th><th>数量</th><th>总价</th><th>来源</th></tr></thead><tbody><tr v-for="i in items.items" :key="i.id"><td>{{i.name||'—'}}<small>{{i.project_name}}</small></td><td>{{i.category||'—'}}</td><td>{{i.brand||'—'}}</td><td>{{i.specification||'—'}}</td><td>{{money(i.unit_price)}}</td><td>{{i.quantity??'—'}} {{i.unit}}</td><td>{{money(i.total_price)}}</td><td><button  :disabled="busy" @click="openProject(i.project_id,$event)" aria-controls="project-detail">证据与项目</button><details><summary>原文</summary><p v-for="(e,n) in i.evidence" :key="n">{{e.source}} · {{e.locator}}<br>{{e.quote}}</p></details></td></tr></tbody></table></div><p v-if="!items.items.length" class="empty">没有匹配的抽取记录，请先导入数据或调整筛选</p><div class="actions"><button :disabled="currentPage<=1" @click="currentPage--;loadItems()">上一页</button><span>第 {{currentPage}} 页</span><button :disabled="currentPage*20>=items.total" @click="currentPage++;loadItems()">下一页</button></div></div>
        <div v-if="detail" id="project-detail" ref="projectPanel" class="panel detail-panel" :class="{'revealed-panel':revealedPanel==='project'}"><button v-if="hasReturnTarget" type="button" class="return-action" @click="returnToAction">返回刚才的位置 ↑</button><h2 tabindex="-1">{{detail.name}}</h2><p>{{detail.buyer}} · {{detail.number}}</p><p v-for="w in detail.warnings" :key="w" class="notice">{{w}}</p><ResultTable :rows="detail.bids" :columns="['package_code','package_name','supplier','result','review_status','bid_price','score','loss_reason','award_amount','consortium','consortium_award_amount','consortium_score']"/><h3>采购包与产品供应角色</h3><ResultTable :rows="detail.items" :columns="['package_code','package_name','name','brand','product_supplier','specification','quantity','unit_price','total_price']"/><h3>原始来源</h3><p v-for="source in detail.sources" :key="source.id" class="muted">{{source.source_uuid}} · {{source.name}}<br><a v-if="source.source_url&&/^https?:\/\//.test(source.source_url)" :href="source.source_url" target="_blank" rel="noopener noreferrer">打开声明的公告来源</a><span v-else>原始URL未提供，不从UUID推断</span></p><details><summary>完整项目记录与来源</summary><pre>{{JSON.stringify(detail,null,2)}}</pre></details></div>
      </section>
      <section v-show="page==='analysis'" aria-label="业务关系分析"><div class="panel benchmark-panel"><div class="panel-heading"><div><div class="section-kicker">PERFORMANCE</div><h2>五场景查询计时</h2></div><button @click="newTimingRun">新建一轮</button></div><p class="muted">同一轮、同一数据范围，各场景取首次请求至渲染完成耗时。先新建一轮，再依次查询1—5；不使用API耗时代替。</p><p>{{timingSummary?.completed_scenes||0}} / 5 场景 · {{timingSummary?.average_ms==null?'五场景完成后计算均值':timingSummary.average_ms.toFixed(1)+' ms 平均响应'}} <span class="muted">浏览器本轮观测，仅供本地性能分析</span></p><button @click="downloadTimings">下载本轮计时报告</button></div>
        <div class="panel"><h2>五大业务场景</h2><div class="scene-tabs"><button v-for="(title,n) in scenes" :key="n" :class="{chosen:scene===n+1}" @click="scene=n+1">{{n+1}}. {{title}}</button></div>
          <div v-if="analysisOptions" class="notice">当前范围：{{analysisOptions.stats.project_count}} 个项目；{{analysisOptions.stats.bid_project_count}} 个项目含投标主体，{{analysisOptions.stats.award_project_count}} 个项目含中标关系。{{mode==='rule'?'规则候选商品不代表投标关系已完整抽取。':''}}</div>
          <div v-if="scene<=2" class="filters" style="margin:16px 0"><label for="buyer-choice">从已有采购单位选择</label><select id="buyer-choice" v-model="buyer" @change="error='';result=null;buyer&&runScene()" style="max-width:100%;flex:1"><option value="">请选择采购单位</option><option v-for="o in analysisOptions?.buyers||[]" :key="o.id" :value="o.name">{{o.name}}（{{o.project_count}}个项目，{{o.bid_project_count}}个含投标记录）</option></select></div>
          <div v-else-if="scene===3" class="filters" style="margin:16px 0"><label for="supplier-choice">从已有中标供应商选择</label><select id="supplier-choice" v-model="supplier" @change="error='';result=null;supplier&&runScene()" style="max-width:100%;flex:1"><option value="">请选择供应商</option><option v-for="o in winnerOptions" :key="o.id" :value="o.name">{{o.name}}（{{o.award_project_count}}个中标项目）</option></select></div>
          <div v-else class="filters" style="margin:16px 0"><label for="supplier-add">添加已有中标供应商</label><select id="supplier-add" v-model="candidateSupplier" style="max-width:100%;flex:1"><option value="">请选择供应商</option><option v-for="o in winnerOptions" :key="o.id" :value="o.name">{{o.name}}</option></select><button type="button" :disabled="!candidateSupplier" @click="addSupplier">添加到查询</button></div>
          <div class="filters"><input v-if="scene<=2" v-model="buyer" list="org-list" placeholder="采购单位全称"><input v-else-if="scene===3" v-model="supplier" list="org-list" placeholder="中标供应商全称"><textarea v-else v-model="suppliers" rows="3" placeholder="至少两个不同供应商，每行一个或用分号分隔"></textarea><datalist id="org-list"><option v-for="o in orgs" :key="o.id" :value="o.name"/></datalist><button class="primary" :disabled="busy" @click="runScene">{{busy?'查询中…':'查询分析'}}</button></div><p class="muted">频次按唯一项目计数；共同投标不等于共同中标，未知金额留空。金额以每个结果注明的口径为准。</p>
          <p v-if="analysisOptions&&!analysisOptions.stats.project_count" class="notice">当前范围暂无已入库项目，请先导入数据或调整结果来源。</p>
          <p v-if="scene>=3&&analysisOptions&&!winnerOptions.length" class="notice">当前范围没有已抽取的中标供应商，暂时无法提供供应商选项；这不代表原公告没有供应商。</p>
          <button v-if="!sample" type="button" @click="mode='rule';sample=true">查看明确标记的自建样例</button>
        </div>
        <div v-if="result" ref="analysisPanel" class="panel detail-panel" :class="{'revealed-panel':revealedPanel==='analysis'}"><h2 tabindex="-1">查询结果 <span class="muted">请求至渲染 {{elapsed??'…'}} ms</span></h2><p>{{result.amount_basis||result.frequency_basis}}</p>
          <template v-if="scene===1"><h3>中标供应商</h3><ResultTable :rows="result.winners"/><h3>产品供应商</h3><ResultTable :rows="result.product_suppliers"/></template>
          <template v-if="scene===2"><h3>TOP5竞标主体</h3><ResultTable :rows="result.top5"/><h3>共同投标主体组合</h3><ResultTable :rows="result.pairs"/></template>
          <template v-if="scene===3"><h3>TOP5共同竞标主体</h3><ResultTable :rows="result.top5"/><h3>全部共同竞标主体</h3><ResultTable :rows="result.all"/></template>
          <template v-if="scene===4"><p v-if="!result.buyers.length" class="empty">无共同合作采购单位</p><article v-for="r in result.buyers" :key="r.buyer"><h3>{{r.buyer}} · {{r.project_count}} 个项目</h3><p>项目成交额合计 {{money(r.known_amount_cents)}}；缺失 {{r.missing_amount_count}} 项</p><ResultTable :rows="r.suppliers"/></article></template>
          <template v-if="scene===5"><div class="metrics"><div><strong>{{result.project_count}}</strong>共同参与项目</div><div><strong>{{money(result.known_award_amount_cents)}}</strong>已知项目总成交额</div><div><strong>{{result.missing_award_amount_count}}</strong>成交额缺失项目</div></div><p class="muted">另列预算合计：{{money(result.known_amount_cents)}}；预算缺失 {{result.missing_amount_count}} 个项目。两个口径分别统计，不互相补值。</p><article v-for="r in result.projects" :key="r.id"><h3>{{r.name||r.number||r.id}}</h3><p>{{r.buyer}} · 预算 {{money(r.project_amount)}} · 总成交额 {{money(r.award_amount)}}</p><ResultTable :rows="r.results"/></article></template>
          <div class="actions"><button @click="downloadTimings">导出浏览器耗时记录</button></div>
        </div><div v-else class="panel empty">{{busy?'正在读取当前范围的主体与关系数据…':'从上方已有主体中选择后查询；场景4和5至少选择两个不同供应商。'}}</div>
      </section>
      <section v-show="page==='graph'" aria-label="关系可视化"><div class="panel"><div class="filters"><input v-model="graphFilter" placeholder="主体全称，留空查看全部" @keyup.enter="loadGraph"><button class="primary" @click="loadGraph">加载关系图</button></div><p class="muted">SQLite关系适配层 · 最多200节点 · 项目—采购包—商品分层，区分投标、中标和产品供应关系</p><p v-if="graphInfo?.truncated" class="notice">数据已截断，请缩小主体范围。</p><p v-if="graphInfo&&!graphInfo.nodes.length" class="empty">当前范围暂无关系数据</p><div ref="graphEl" class="graph"></div></div></section>
      <footer>所有结果来自当前数据库。样例、规则基线与模型方案独立存储；未经实测的指标不作为竞赛成果。</footer>
    </main>
  </div>
</template>
