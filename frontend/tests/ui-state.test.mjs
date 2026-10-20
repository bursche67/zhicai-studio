import test from 'node:test'
import assert from 'node:assert/strict'
import {pageFromHash, readState, parseSuppliers, validationMessage, queryTimingReport} from '../src/ui-state.js'

test('all four pages are addressable and invalid hashes have a safe fallback', () => {
  for (const p of ['upload', 'items', 'analysis', 'graph']) assert.equal(pageFromHash('#' + p), p)
  assert.equal(pageFromHash('#/analysis'), 'analysis')
  assert.equal(pageFromHash('#unknown'), 'upload')
})
test('saved filters survive reload and unavailable storage does not break navigation', () => {
  assert.deepEqual(readState({getItem: () => '{"scene":4,"supplier":"甲"}'}), {scene:4, supplier:'甲'})
  for (const value of ['invalid', 'null', '[]']) assert.deepEqual(readState({getItem: () => value}), {})
  assert.deepEqual(readState({getItem: () => {throw Error('storage denied')}}), {})
})
test('empty, single and repeated supplier input is rejected before a request', () => {
  for (const input of ['', '\n；', '甲', '甲；甲']) assert.throws(() => parseSuppliers(input), /供应商/)
})
test('two or more suppliers preserve order across newline and Chinese semicolon', () => {
  assert.deepEqual(parseSuppliers(' 甲 \n乙；丙;\n'), ['甲','乙','丙'])
  assert.throws(() => parseSuppliers(Array.from({length:21}, (_,i) => '公司'+i).join('\n')), /20/)
})
test('Pydantic supplier errors are presented as a Chinese instruction', () => {
  const details=[{type:'too_short', loc:['body','suppliers'], msg:'List should have at least 2 items'}]
  assert.match(validationMessage(details), /不同供应商/)
  assert.doesNotMatch(validationMessage(details), /too_short|List should/)
})

test('five-scene timing requires one consistent run and takes first observations',()=>{
  const records=[1,2,3,4,5].map(scene=>({run_id:'run',mode:'model',sample:false,scene,ms:scene*10}))
  assert.equal(queryTimingReport(records.slice(0,4),'model',false,'run').average_ms,null)
  const noise=[{...records[0],ms:1},{...records[0],scene:6},{...records[0],scene:2,ms:NaN},{...records[0],mode:'rule'},{...records[0],run_id:'another'},{...records[0],sample:true}]
  const result=queryTimingReport([...records,...noise],'model',false,'run')
  assert.equal(result.average_ms,30)
  assert.equal(result.completed_scenes,5)
  assert.equal(result.scenes[0].ms,10)
  assert.equal(result.reference_score,false)
  assert.equal(queryTimingReport([null,{},...records],'model',false,'run').average_ms,30)
  assert.equal(queryTimingReport({},'model',false,'run').average_ms,null)
})
