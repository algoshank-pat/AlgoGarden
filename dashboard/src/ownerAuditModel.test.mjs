import test from 'node:test';
import assert from 'node:assert/strict';
import {ownerAudit} from './ownerAuditModel.js';
const turn=(caller_text,intent='other',reply='Thank you. Goodbye.')=>({caller_text,result:{routing:{intent},reply},events:[]});
for(const channel of ['chat','voice']){
 test(`${channel}: goodbye is not an answered enquiry`,()=>{
  const result=ownerAudit({channel,intent:'other',turns:[turn('Goodbye.')],review:'New'});
  assert.equal(result.outcome,'No request');assert.equal(result.request,'No business request');assert.equal(result.requestTurn,undefined);assert.equal(result.heading,'Ended without a request');
 });
 test(`${channel}: greeting and closing preserve the actual question and answer`,()=>{
  const question=turn('What time do you open?','hours','We open at 11 am.');
  const result=ownerAudit({channel,turns:[turn('Hello'),question,turn('Goodbye.')],review:'New'});
  assert.equal(result.request,'Opening hours');assert.equal(result.requestTurn,question);assert.equal(result.responseTurn,question);assert.equal(result.outcome,'Answered');
 });
 test(`${channel}: unclassified request is not claimed answered`,()=>{
  assert.equal(ownerAudit({channel,turns:[turn('Something went wrong with my visit')]}).outcome,'Needs review');
 });
 test(`${channel}: closing does not replace a successful booking`,()=>{
  const call={channel,turns:[turn('A table for two tomorrow','reservation'),turn('Goodbye')],events:[{name:'book_table.completed',details:{booked:true,reservation:{id:'x',party_size:2,date:'2026-11-03',time:'19:00'}}}]};
  assert.equal(ownerAudit(call).outcome,'Booked');
 });
}
