export const pages = ['upload', 'items', 'analysis', 'graph']
export const stateKey = 'zhicai-ui-state-v1'

const taskDateFormat = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'})
export function groupTaskHistory(jobs) {
  const groups = new Map()
  const sorted = [...jobs].sort((a,b)=>(Date.parse(b.created_at)||0)-(Date.parse(a.created_at)||0))
  for (const job of sorted) {
    const date = new Date(job.created_at || NaN)
    const key = Number.isNaN(date.getTime()) ? 'unknown' : taskDateFormat.format(date)
    if (!groups.has(key)) groups.set(key,{key,label:key==='unknown'?'时间未记录':key,jobs:[],activeCount:0})
    const group=groups.get(key)
    group.jobs.push(job)
    if (['queued','running'].includes(job.status)) group.activeCount++
  }
  return [...groups.values()]
}

export function taskClock(value) {
  const date = new Date(value || NaN)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'})
}

export function statusLabel(value) {
  return {superseded:'已合并至已有任务',queued:'排队中',running:'处理中',parsing:'解析中',extracting:'模型抽取中',completed:'已完成',duplicate:'已复用已有结果',partial:'部分完成',failed:'处理失败',interrupted:'已中断，可重试',needs_attention:'待处理'}[value] || value
}

export function pageFromHash(hash) {
  const value = String(hash || '').replace(/^#\/?/, '')
  return pages.includes(value) ? value : 'upload'
}

export function readState(storage) {
  try {
    const value = JSON.parse(storage.getItem(stateKey) || '{}')
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch { return {} }
}

export function parseSuppliers(value) {
  const names = value.split(/[\n;；]/).map(x => x.trim()).filter(Boolean)
  if (names.length < 2) throw new Error('请至少填写两个不同供应商，每行一个，或用分号分隔。')
  if (names.length > 20) throw new Error('一次最多查询20个供应商。')
  if (new Set(names).size !== names.length) throw new Error('供应商名称重复，请填写至少两个不同供应商。')
  return names
}

export function validationMessage(detail) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    if (detail.some(e => e.loc?.includes('suppliers'))) return '请填写2至20个不同供应商，每行一个，或用分号分隔。'
    return detail.map(e => `${e.loc?.filter(x => x !== 'body' && x !== 'query').join(' / ') || '输入'}：${e.msg || '格式不正确'}`).join('；')
  }
  return '请求未完成，请检查输入后重试。'
}

export function queryTimingReport(records, mode, sample, runId) {
  const first = new Map()
  for (const r of Array.isArray(records) ? records : []) {
    if (!r || typeof r !== 'object') continue
    if (r.run_id !== runId || r.mode !== mode || r.sample !== sample || !Number.isInteger(r.scene) || r.scene < 1 || r.scene > 5 || !Number.isFinite(r.ms) || r.ms < 0) continue
    if (!first.has(r.scene)) first.set(r.scene,r)
  }
  const scenes = [...first.values()].sort((a,b)=>a.scene-b.scene)
  return {run_id:runId,mode,sample,completed_scenes:scenes.length,missing_scenes:[1,2,3,4,5].filter(n=>!first.has(n)),scenes,average_ms:scenes.length===5?scenes.reduce((n,r)=>n+r.ms,0)/5:null,reference_score:false,method:'同一轮、同一数据范围，五场景各取首次请求到完全渲染耗时；不足五场景不计算均值。查询正确性需另与业务基准核对。'}
}
