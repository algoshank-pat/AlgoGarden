// Render the real chat/voice components with a controlled clock and simulated devices.
import {build} from '../dashboard/node_modules/esbuild/lib/main.js';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {conversationTimestamp} from '../dashboard/src/conversationTimestamp.js';
const checks=[];
for(const [iso,expected] of [
 ['2026-10-29T17:09:00Z','29th Oct 2026 12:09 PM'],
 ['2026-10-30T04:59:00Z','29th Oct 2026 11:59 PM'],
 ['2026-10-30T05:00:00Z','30th Oct 2026 12:00 AM'],
 ['2026-11-11T18:09:00Z','11th Nov 2026 12:09 PM'],
 ['2026-11-12T18:09:00Z','12th Nov 2026 12:09 PM'],
 ['2026-11-13T18:09:00Z','13th Nov 2026 12:09 PM'],
 ['2026-10-21T17:09:00Z','21st Oct 2026 12:09 PM'],
 ['2026-10-22T17:09:00Z','22nd Oct 2026 12:09 PM'],
 ['2026-10-23T17:09:00Z','23rd Oct 2026 12:09 PM'],
 ['2026-11-01T06:30:00Z','1st Nov 2026 1:30 AM'],
 ['2026-11-01T07:30:00Z','1st Nov 2026 1:30 AM']]){
 assert.equal(conversationTimestamp(iso),expected);checks.push({check:iso,expected,passed:true});
}
for(const channel of ['Chat','VoiceTest']){
 let clock=Date.parse('2026-10-29T17:09:00Z'),slots=[],cursor=0,tree,processor,audioProps;
 class Clock extends Date{constructor(...args){super(...(args.length?args:[clock]));}static now(){return clock;}}
 const audio={ended:false,pause(){},removeAttribute(){},load(){},async play(){audio.ended=false;audioProps?.onPlay?.();}};
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];},useRef(initial){const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect(){},createElement(type,props,...children){if(type==='audio'){audioProps=props;props.ref.current=audio;}return {type,props:props||{},children:children.flat(Infinity)};}};react.default=react;
 const track={enabled:true,stop(){}};
 class Context{constructor(){this.sampleRate=16000;this.currentTime=0;}async resume(){}async close(){}createOscillator(){return {frequency:{value:0},connect(){},start(){},stop(){}};}createGain(){return {gain:{value:0,setValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){},disconnect(){}};}createMediaStreamSource(){return {connect(){},disconnect(){}};}createScriptProcessor(){processor={connect(){},disconnect(){}};return processor;}}
 const fetcher=async(path,options={})=>{
  if(path==='/api/restaurant')return Response.json({name:'Algo Garden',timezone:'America/Chicago',menu:[],specials:[]});
  if(path==='/api/sessions')return Response.json({session_id:'a6bbf910-2f62-4402-88f3-db04af9fe17e',greeting:'Welcome to Algo Garden.'});
  if(path.includes('/transcribe')){if(path.includes('?'))clock=Date.parse('2026-10-29T17:10:00Z');return Response.json({text:'What are your opening hours?',input_kind:'text'});}
  if(path.endsWith('/turns')){clock=Date.parse('2026-10-29T17:11:00Z');return Response.json({reply:'Lunch begins at eleven.',ended:false});}
  if(path.endsWith('/speak/stream'))return new Response(JSON.stringify({audio:'AAAA',index:0})+'\n');
  return Response.json({saved:true});
 };
 const compiled=await build({entryPoints:[`dashboard/src/${channel}.jsx`],bundle:true,write:false,platform:'node',format:'cjs',loader:{'.css':'empty'},external:['react','lucide-react']});
 const module={exports:{}};
 vm.runInNewContext(compiled.outputFiles[0].text,{module,exports:module.exports,require:name=>name==='react'?react:new Proxy({},{get:()=>()=>null}),Date:Clock,Intl,console,Blob,Response,Float32Array,ArrayBuffer,DataView,Uint8Array,TextDecoder,AbortController,URL,atob,performance,crypto:webcrypto,fetch:fetcher,setTimeout:(cb)=>setTimeout(cb,0),clearTimeout,navigator:{mediaDevices:{getUserMedia:async()=>({getAudioTracks:()=>[track],getTracks:()=>[track]})}},AudioContext:Context});
 const Component=module.exports.default;
 const render=()=>{cursor=0;tree=Component();};
 const nodes=node=>!node||typeof node!=='object'?[]:[node,...node.children.flatMap(nodes)];
 const text=node=>node.children.filter(x=>typeof x==='string').join('');
 const times=()=>{render();return nodes(tree).filter(n=>n.type==='time').map(n=>({iso:n.props.dateTime,label:text(n)}));};
 const wait=async(predicate)=>{for(let i=0;i<100;i++){render();if(predicate())return;await new Promise(r=>setTimeout(r,5));}throw Error(channel+' did not finish');};
 render();
 if(channel==='Chat'){
  nodes(tree).find(n=>n.props['aria-label']==='Chat with the restaurant').props.onClick();
  await wait(()=>times().length===1);
  clock=Date.parse('2026-10-29T17:10:00Z');
  nodes(tree).find(n=>n.type==='input'&&n.props.id==='customer-message').props.onChange({target:{value:'What are your opening hours?'}});render();
  await nodes(tree).find(n=>n.type==='form').props.onSubmit({preventDefault(){}});
  await wait(()=>times().length===3);
 }else{
  await nodes(tree).find(n=>n.type==='button'&&text(n)==='Start call').props.onClick();
  await wait(()=>times().length===1);
  await new Promise(r=>setTimeout(r,10));audio.ended=true;audioProps.onEnded();
  for(let i=0;i<10;i++)processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(1600).fill(.1)}});
  for(let i=0;i<8;i++)processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(1600)}});
  await wait(()=>times().length===3);
 }
 const result=times();
 assert.deepEqual(result.map(x=>x.label),['29th Oct 2026 12:09 PM','29th Oct 2026 12:10 PM','29th Oct 2026 12:11 PM']);
 clock=Date.parse('2026-10-29T18:30:00Z');assert.deepEqual(times(),result,'Rerender must retain original timestamps');
 checks.push({check:channel+' greeting, customer and agent message timestamps; stable after rerender',passed:true,messages:result});
}
await fs.mkdir('results/timestamps',{recursive:true});await fs.writeFile('results/timestamps/report.json',JSON.stringify({passed:true,method:'Real component rendering, controlled clock, mocked network and audio devices',checks},null,2));
console.log(`PASS ${checks.length} timestamp checks, including both real UI components.`);
