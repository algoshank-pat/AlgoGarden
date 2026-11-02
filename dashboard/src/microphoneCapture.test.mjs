import {test} from 'node:test';import assert from 'node:assert/strict';
import {openMicrophone} from './microphoneCapture.mjs';
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
function fixture(){const track={stopped:false,stop(){this.stopped=true}};const stream={getTracks:()=>[track]};const context={state:'suspended',resume(){this.state='running';return Promise.resolve()},close(){this.state='closed';return Promise.resolve()}};return {track,stream,context,createContext:()=>context}}
test('real permission is requested and context resumed in the click activation',async()=>{
 const f=fixture();let requested=false;const result=await openMicrophone({...f,mediaDevices:{getUserMedia(){requested=true;assert.equal(f.context.state,'running');return f.stream}}});
 assert.ok(requested);assert.equal(result.stream,f.stream);assert.equal(f.track.stopped,false);
});
test('cancel pending capture closes context; late grant stops track and never revives capture',async()=>{
 const f=fixture(),gate=deferred(),controller=new AbortController();const pending=openMicrophone({...f,signal:controller.signal,mediaDevices:{getUserMedia:()=>gate.promise}});
 controller.abort();await assert.rejects(pending,{name:'AbortError'});assert.equal(f.context.state,'closed');gate.resolve(f.stream);await new Promise(r=>setTimeout(r,0));assert.equal(f.track.stopped,true);
});
test('pending prompt times out actionably, and late approval is disposed',async()=>{
 const f=fixture(),gate=deferred();const pending=openMicrophone({...f,timeoutMs:5,origin:'http://127.0.0.1:5174',mediaDevices:{getUserMedia:()=>gate.promise}});
 await assert.rejects(pending,/still pending.*5174/);gate.resolve(f.stream);await new Promise(r=>setTimeout(r,0));assert.equal(f.track.stopped,true);assert.equal(f.context.state,'closed');
});
for(const [name,pattern] of [['NotAllowedError',/not granted/],['NotFoundError',/No microphone/],['NotReadableError',/close other apps/]])test(name+' has an actionable error',async()=>{
 const f=fixture();await assert.rejects(openMicrophone({...f,mediaDevices:{getUserMedia:()=>Promise.reject(Object.assign(Error('failure'),{name}))}}),pattern);assert.equal(f.context.state,'closed');
});
test('aborted or failed old attempt cannot close a later successful capture',async()=>{
 const old=fixture(),next=fixture(),gate=deferred(),controller=new AbortController();const pending=openMicrophone({...old,signal:controller.signal,mediaDevices:{getUserMedia:()=>gate.promise}});controller.abort();await assert.rejects(pending,{name:'AbortError'});
 const good=await openMicrophone({...next,mediaDevices:{getUserMedia:()=>next.stream}});gate.resolve(old.stream);await new Promise(r=>setTimeout(r,0));assert.equal(old.track.stopped,true);assert.equal(good.context.state,'running');assert.equal(next.track.stopped,false);
});
