import assert from 'node:assert/strict';
import fs from 'node:fs';
import {ownerAudit,reservationLabel} from './src/ownerAuditModel.js';

const root=new URL('../results/owner-audit-tests/',import.meta.url);
const source=JSON.parse(fs.readFileSync(new URL('source.json',root),'utf8'));
const results=[];
for(const scenario of source.scenarios){
 const call=source.calls.find(c=>c.id===scenario.session_id);
 const summary=ownerAudit(call,source.history);
 const expectedOutcome={booking:'Booked',update:'Updated',cancel:'Cancelled'}[scenario.action];
 const eventName=scenario.action==='booking'?'book_table.completed':'change_reservation.completed';
 const mutation=call.events.find(e=>e.name===eventName&&(e.details.output?.booked||e.details.output?.changed));
 assert(mutation,'Successful mutation event missing');
 const output=mutation.details.output,row=output.reservation||output;
 assert.equal(summary.outcome,expectedOutcome);
 assert.equal(summary.result.id,row.id);
 assert.equal(summary.result.party_size,row.party_size);
 assert.equal(summary.result.date,row.date);
 assert.equal(summary.result.time,row.time);
 assert.equal(summary.contact.name,row.name);
 assert.equal(summary.attention,false,'Successful conversation incorrectly flagged for follow-up');
 const bookingEvent=source.history.find(e=>e.booking_id===row.id&&(scenario.action==='booking'?e.kind==='created':e.payload.after?.version===row.version));
 assert(bookingEvent,'Authoritative reservation event missing');
 const final=bookingEvent.payload.after||bookingEvent.payload;
 assert.equal(final.party_size,row.party_size);
 assert.equal(final.time,row.time);
 let before=null,after=null;
 if(scenario.action==='update'){
  const milestone=summary.milestones.find(m=>m.title==='Reservation updated');
  assert.deepEqual(milestone.before,bookingEvent.payload.before);
  assert.deepEqual(milestone.after,bookingEvent.payload.after);
  before=reservationLabel(milestone.before);after=reservationLabel(milestone.after);
 }
 if(scenario.action==='cancel'){
  assert.equal(row.status,'cancelled');
  assert(summary.milestones.some(m=>m.title==='Customer confirmation received'&&m.detail.includes('cancellation')));
 }
 assert(summary.milestones.some(m=>m.title==='Customer confirmation received'));
 results.push({...scenario,passed:true,outcome:summary.outcome,heading:summary.heading,
  guest:row.name,details:reservationLabel(row),before,after,attention:false,
  milestones:summary.milestones.map(m=>m.title),expected_review:call.review,
  reservation_url:`/owner/reservations?view=live&date=${row.date}`});
 console.log(`PASS: ${scenario.channel} ${scenario.action} — summary, history, confirmation, follow-up`);
}
const pending={intent:'reservation',turns:[{result:{routing:{intent:'reservation'}}}],events:[]};
assert.equal(ownerAudit(pending).outcome,'Not completed');
assert.equal(ownerAudit({...pending,events:[{name:'transfer_to_human.completed',details:{}}]}).attention,true);
assert.equal(ownerAudit({...pending,events:[{name:'receptionist.turn.failed',details:{}}]}).attention,true);
fs.writeFileSync(new URL('expectations.json',root),JSON.stringify({passed:true,negative_checks:['unconfirmed booking is not reported as saved','staff handoff requires follow-up','failed request requires review'],scenarios:results},null,2));
