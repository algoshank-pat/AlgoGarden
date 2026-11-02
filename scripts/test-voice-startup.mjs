// Replay real voice API results through the actual React voice component.
import {build} from '../dashboard/node_modules/esbuild/lib/main.js';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {webcrypto} from 'node:crypto';
const report={greeting:{session_id:'demo-session',spoken_greeting:'Thanks for calling Masala Mozzarella. How can I help?',greeting:'Welcome'}};let warmupPending=false,playing=false;const order=[];
let slots=[],cursor=0,tree,processor,audioProps,index=0;const requests=[],listeners=new Map();
const audio={ended:false,src:'',pause(){},removeAttribute(){this.src=''},load(){},addEventListener(name,fn){listeners.set(name,fn)},removeEventListener(name,fn){if(listeners.get(name)===fn)listeners.delete(name)},async play(){playing=true;order.push('playing');this.ended=false;audioProps.onPlay?.();listeners.get('playing')?.();}};
const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;}];},useRef(v){const i=cursor++;if(!(i in slots))slots[i]={current:v};return slots[i];},useEffect(){},createElement(type,props,...children){if(type==='audio'){audioProps=props;props.ref.current=audio;}return {type,props:props||{},children:children.flat(Infinity)};}};react.default=react;
const track={enabled:true,stop(){}};
class Context{constructor(){this.sampleRate=16000;this.currentTime=0;this.state='running';}async resume(){}async close(){}createOscillator(){return {frequency:{value:0},connect(){},start(){},stop(){}};}createGain(){return {gain:{value:0,setValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){},disconnect(){}};}createMediaStreamSource(){return {connect(){},disconnect(){}};}createScriptProcessor(){processor={connect(){},disconnect(){}};return processor;}}
const fetcher=async(url,options={})=>{order.push(url);if(url==='/api/voice/warmup'){assert.ok(playing,'Warmup blocked greeting');warmupPending=true;return new Promise(()=>{});}
 if(url==='/api/sessions')return Response.json(report.greeting);
 if(url.includes('/transcribe'))return Response.json({text:report.turns[index].heard,input_kind:'text'});
 if(url.endsWith('/turns')){const row=report.turns[index++];assert.equal(JSON.parse(options.body).text,row.heard);return Response.json(row.response)}
 if(url.endsWith('/speak/stream')){const text=JSON.parse(options.body).text;requests.push(text);return new Response(JSON.stringify({audio:'AAAA',index:0})+'\n')}
 return Response.json({saved:true});
};
const compiled=await build({entryPoints:['dashboard/src/VoiceTest.jsx'],bundle:true,write:false,platform:'node',format:'cjs',loader:{'.css':'empty'},external:['react','lucide-react']});
const module={exports:{}};
vm.runInNewContext(compiled.outputFiles[0].text,{module,exports:module.exports,require:n=>n==='react'?react:new Proxy({},{get:()=>()=>null}),Intl,Date,Blob,Response,Float32Array,ArrayBuffer,DataView,Uint8Array,TextDecoder,AbortController,URL,atob,performance,crypto:webcrypto,fetch:fetcher,setTimeout:(fn,ms)=>setTimeout(fn,ms===850?0:ms),clearTimeout,location:{origin:'http://localhost'},document:{visibilityState:'visible'},navigator:{mediaDevices:{getUserMedia:async()=>({getAudioTracks:()=>[track],getTracks:()=>[track]})}},AudioContext:Context,console});
const Component=module.exports.default;const render=()=>{cursor=0;tree=Component()};
const nodes=n=>!n||typeof n!=='object'?[]:[n,...n.children.flatMap(nodes)];const text=n=>n.children.filter(v=>typeof v==='string').join('');
const assistant=()=>{render();return nodes(tree).filter(n=>n.props.className==='voice-utterance assistant').map(n=>text(n.children.find(c=>c?.type==='p')))};
const wait=async(predicate)=>{for(let n=0;n<200;n++){render();if(predicate())return;await new Promise(r=>setTimeout(r,5))}throw Error('Voice UI replay did not finish turn '+index)};
render();
const start=nodes(tree).find(n=>n.type==='button'&&text(n)==='Start call').props.onClick();
await Promise.race([start,new Promise((_,reject)=>setTimeout(()=>reject(Error('Startup blocked on stalled warmup')),2000))]);
assert.ok(playing);assert.ok(warmupPending);assert.ok(order.indexOf('playing')<order.indexOf('/api/voice/warmup'));
render();assert.ok(nodes(tree).some(n=>n.type==='p'&&text(n).includes('Masala Mozzarella')));
const end=nodes(tree).find(n=>n.type==='button'&&text(n)==='End call');await end.props.onClick();
console.log('PASS: actual VoiceTest component starts greeting before a permanently stalled warmup; Start and End remain responsive.');
