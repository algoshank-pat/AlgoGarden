import fs from 'node:fs';
import assert from 'node:assert/strict';
import {timelineFor,pretty,seconds} from './src/auditModel.js';

const path=new URL('../results/audit-flow/acceptance/report.json',import.meta.url);
const report=JSON.parse(fs.readFileSync(path,'utf8'));
assert.equal(report.scenarios.length,6);
const expectations=[];
for(const scenario of report.scenarios){
 const turn=scenario.call.turns[scenario.turn_index],rows=timelineFor(turn);
 assert.equal(turn.result.routing.intent,'reservation');
 assert(turn.result.structured.tools_called.includes(scenario.tool));
 const measured=rows.filter(r=>r.name===scenario.tool&&!r.marker);
 assert.equal(measured.length,1);
 assert.equal(measured[0].failed,false);
 assert.equal(measured[0].duration,measured[0].endEvent.duration_ms);
 for(const row of rows.filter(r=>!r.marker)){
  assert(Number.isFinite(row.duration)&&row.duration>=0);
  assert(Math.abs(row.duration-row.endEvent.duration_ms)<0.001);
 }
 expectations.push({channel:scenario.channel,action:scenario.action,session_id:scenario.session_id,
  turn_index:scenario.turn_index,route:'Reservations',tools:turn.result.structured.tools_called.map(pretty),
  total:seconds(scenario.call.turns.reduce((n,t)=>n+(t.result.latency_ms||0),0)),
  duration:seconds(turn.result.latency_ms),
  waterfall:rows.map(r=>({name:pretty(r.name),duration:r.marker?null:seconds(r.duration)}))});
 console.log(`PASS: ${scenario.channel} ${scenario.action}; ${rows.length} timeline rows match trace values`);
}
fs.writeFileSync(new URL('../results/audit-flow/acceptance/browser-expectations.json',import.meta.url),JSON.stringify(expectations,null,2));
