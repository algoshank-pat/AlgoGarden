// Replay real voice API results through the actual React voice component.
import {build} from '../dashboard/node_modules/esbuild/lib/main.js';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {webcrypto} from 'node:crypto';
const reportPath=process.argv[2];const report=JSON.parse(await fs.readFile(reportPath,'utf8'));assert.ok(report.passed);
let slots=[],cursor=0,tree,processor,audioProps,index=0;const requests=[],listeners=new Map();
const audio={ended:false,src:'',pause(){},removeAttribute(){this.src=''},load(){},addEventListener(name,fn){listeners.set(name,fn)},removeEventListener(name,fn){if(listeners.get(name)===fn)listeners.delete(name)},async play(){this.ended=false;audioProps.onPlay?.();listeners.get('playing')?.();}};
const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;}];},useRef(v){const i=cursor++;if(!(i in slots))slots[i]={current:v};return slots[i];},useEffect(){},createElement(type,props,...children){if(type==='audio'){audioProps=props;props.ref.current=audio;}return {type,props:props||{},children:children.flat(Infinity)};}};react.default=react;
const track={enabled:true,stop(){}};
class Context{constructor(){this.sampleRate=16000;this.currentTime=0;}async resume(){}async close(){}createOscillator(){return {frequency:{value:0},connect(){},start(){},stop(){}};}createGain(){return {gain:{value:0,setValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){},disconnect(){}};}createMediaStreamSource(){return {connect(){},disconnect(){}};}createScriptProcessor(){processor={connect(){},disconnect(){}};return processor;}}
const fetcher=async(url,options={})=>{
 if(url==='/api/sessions')return Response.json(report.greeting);
 if(url.includes('/transcribe'))return Response.json({text:report.turns[index].heard,input_kind:'text'});
 if(url.endsWith('/turns')){const row=report.turns[index++];assert.equal(JSON.parse(options.body).text,row.heard);return Response.json(row.response)}
 if(url.endsWith('/speak/stream')){const text=JSON.parse(options.body).text;requests.push(text);return new Response(JSON.stringify({audio:'AAAA',index:0})+'\n')}
 return Response.json({saved:true});
};
const compiled=await build({entryPoints:['dashboard/src/VoiceTest.jsx'],bundle:true,write:false,platform:'node',format:'cjs',loader:{'.css':'empty'},external:['react','lucide-react']});
const module={exports:{}};
vm.runInNewContext(compiled.outputFiles[0].text,{module,exports:module.exports,require:n=>n==='react'?react:new Proxy({},{get:()=>()=>null}),Intl,Date,Blob,Response,Float32Array,ArrayBuffer,DataView,Uint8Array,TextDecoder,AbortController,URL,atob,performance,crypto:webcrypto,fetch:fetcher,setTimeout:fn=>setTimeout(fn,0),clearTimeout,navigator:{mediaDevices:{getUserMedia:async()=>({getAudioTracks:()=>[track],getTracks:()=>[track]})}},AudioContext:Context,console});
const Component=module.exports.default;const render=()=>{cursor=0;tree=Component()};
const nodes=n=>!n||typeof n!=='object'?[]:[n,...n.children.flatMap(nodes)];const text=n=>n.children.filter(v=>typeof v==='string').join('');
const assistant=()=>{render();return nodes(tree).filter(n=>n.props.className==='voice-utterance assistant').map(n=>text(n.children.find(c=>c?.type==='p')))};
const wait=async(predicate)=>{for(let n=0;n<200;n++){render();if(predicate())return;await new Promise(r=>setTimeout(r,5))}throw Error('Voice UI replay did not finish turn '+index)};
render();await nodes(tree).find(n=>n.type==='button'&&text(n)==='Start call').props.onClick();await wait(()=>requests.length===1);
for(let n=0;n<report.turns.length;n++){
 await new Promise(r=>setTimeout(r,10));audio.ended=true;audioProps.onEnded();
 for(let i=0;i<10;i++)processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(1600).fill(.1)}});
 for(let i=0;i<20;i++)processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(1600)}});
 await wait(()=>requests.length===n+2);
 assert.equal(assistant().at(-1),report.turns[n].spoken_reply,'Displayed text mismatch at turn '+(n+1));
 assert.equal(requests.at(-1),report.turns[n].piper_text,'Speech request mismatch at turn '+(n+1));
}
assert.equal(assistant()[0],report.greeting.spoken_greeting);
const result={passed:true,turns:index,greeting_matches:true,all_displayed_replies_match_piper:true,method:'Actual React component replaying real API evidence; browser audio adapters simulated'};
await fs.writeFile(path.join(path.dirname(reportPath),'ui-text-verification.json'),JSON.stringify(result,null,2));console.log('PASS voice UI displayed text and speech requests match actual Piper text for all '+index+' turns and greeting.');
