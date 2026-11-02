// Exercise the actual VoiceTest component with controlled media/API adapters.
// These lifecycle checks do not claim physical microphone/speaker verification.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {build} from '../node_modules/esbuild/lib/main.js';

const compiled=await build({entryPoints:[fileURLToPath(new URL('./VoiceTest.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'cjs',loader:{'.css':'empty'},external:['react','lucide-react']});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const packet=index=>new TextEncoder().encode(JSON.stringify({index,audio:btoa('fixture audio')})+'\n');

function setup({reply='Thanks for calling. Good night!',ended=true,failAudio=false,cachedGreeting=false,deferMicrophone=false,deferSession=false}={}){
 let mounted=false,effects=[],grantMicrophone,releaseSession;
 let slots=[],cursor=0,tree,processor,audioProps,streamController,turns=0,sessionCount=0;
 const calls=[],listeners=new Map(),tracks=[],contexts=[];
 const audio={src:'',ended:false,plays:0,pause(){},removeAttribute(){this.src=''},load(){},
  addEventListener(name,fn){listeners.set(name,fn)},removeEventListener(name,fn){if(listeners.get(name)===fn)listeners.delete(name)},
  async play(){this.plays++;this.ended=false;audioProps.onPlay();listeners.get('playing')?.()}};
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return [slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v]},
  useRef(initial){const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i]},useEffect(fn){if(!mounted)effects.push(fn)},
  createElement(type,props,...children){if(type==='audio'){audioProps=props;props.ref.current=audio}return {type,props:props||{},children:children.flat(Infinity)}}};react.default=react;
 class Context{
  constructor(){this.sampleRate=16000;this.currentTime=0;this.state='running';contexts.push(this)}async resume(){}async close(){this.state='closed'}
  createOscillator(){return {frequency:{value:0},connect(){},disconnect(){},start(){},stop(){}}}
  createGain(){return {gain:{value:0,setValueAtTime(){},exponentialRampToValueAtTime(){},linearRampToValueAtTime(){}},connect(){},disconnect(){}}}
  createMediaStreamSource(){return {connect(){},disconnect(){}}}
  createScriptProcessor(){processor={connect(){},disconnect(){}};return processor}
 }
 const fetcher=async(url,options={})=>{
  calls.push({url,options});
  if(url==='/api/voice/greeting'&&cachedGreeting)return Response.json({text:'Hello. How can I help?',version:'private-fixture',audio:btoa('cached welcome wav')});
  if(url==='/api/sessions'){if(deferSession)await new Promise(resolve=>releaseSession=resolve);return Response.json({session_id:'call-'+(++sessionCount),spoken_greeting:'Hello. How can I help?'})}
  if(url.includes('/transcribe'))return Response.json({text:'No thanks.',input_kind:'text'});
  if(url.endsWith('/turns')){turns++;return Response.json({reply,spoken_reply:reply,ended,survey:{status:ended?'declined':'offered'}})}
  if(url.endsWith('/speak/stream')){
   if(JSON.parse(options.body).text.startsWith('Hello.'))return new Response(packet(0));
   if(failAudio)return new Response('',{status:503});
   return new Response(new ReadableStream({start(c){streamController=c}}));
  }
  return Response.json({saved:true});
 };
 const module={exports:{}};
 vm.runInNewContext(compiled.outputFiles[0].text,{module,exports:module.exports,require:n=>n==='react'?react:new Proxy({},{get:()=>()=>null}),Intl,Date,Blob,Response,Float32Array,ArrayBuffer,DataView,Uint8Array,TextDecoder,AbortController,URL,atob,performance,crypto:webcrypto,fetch:fetcher,
  setTimeout:(fn,ms)=>setTimeout(fn,ms===850?0:ms),clearTimeout,location:{origin:'http://localhost'},document:{visibilityState:'visible'},
  navigator:{mediaDevices:{getUserMedia:async()=>{if(deferMicrophone)await new Promise(resolve=>grantMicrophone=resolve);const track={enabled:true,readyState:'live',stop(){this.readyState='ended'}};tracks.push(track);return {getAudioTracks:()=>[track],getTracks:()=>[track]}}}},AudioContext:Context,console:{info(){},warn(){}}});
 const Component=module.exports.default;
 const render=()=>{cursor=0;tree=Component();if(!mounted){mounted=true;for(const fn of effects)fn()}};
 const nodes=n=>!n||typeof n!=='object'?[]:[n,...n.children.flatMap(nodes)];
 const text=n=>n.children.filter(v=>typeof v==='string').join('');
 const button=label=>{render();return nodes(tree).find(n=>n.type==='button'&&text(n).trim()===label)};
 const status=()=>{render();return nodes(tree).find(n=>n.props.role==='status')};
 const wait=async(predicate)=>{for(let i=0;i<200;i++){render();if(predicate())return;await tick()}throw Error('Timed out: '+text(status()))};
 return {audio,calls,tracks,contexts,button,status,text,wait,grantMicrophone:()=>grantMicrophone(),releaseSession:()=>releaseSession(),
  async start(){await button('Start call').props.onClick();await tick()},
  async finishChunk(){audio.ended=true;audioProps.onEnded();await tick()},
  enqueue(index){streamController.enqueue(packet(index))},closeStream(){streamController.close()},
  async speak(){for(let i=0;i<10;i++)processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(1600).fill(.1)}});for(let i=0;i<24;i++)processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(1600)}});await wait(()=>turns===1);await wait(()=>streamController||text(status())==='Call ended')},
  get stopped(){return calls.filter(c=>c.url.endsWith('/stop')).length},
  get messages(){render();return nodes(tree).filter(n=>n.props.className==='voice-utterance assistant').map(n=>text(n.children.find(c=>c?.type==='p')))}
 };
}

test('cached welcome plays before permission/session; late setup respects manual mute',async()=>{
 const s=setup({cachedGreeting:true,deferMicrophone:true,deferSession:true});
 s.button('Start call');await tick(); // page preloads the public welcome
 const starting=s.button('Start call').props.onClick();await s.wait(()=>s.audio.plays===1);
 assert.equal(s.tracks.length,0);assert.equal(s.button('End call').props.disabled,false);
 assert.equal(s.calls.filter(c=>c.url.endsWith('/speak/stream')).length,0);
 s.button('Mute').props.onClick();await s.finishChunk();
 s.releaseSession();s.grantMicrophone();await starting;
 assert.equal(s.tracks[0].enabled,false);assert.match(s.text(s.status()),/Listening/);
 s.button('Unmute').props.onClick();assert.equal(s.tracks[0].enabled,true);
 s.button('End call').props.onClick();
});

test('end while cached welcome plays cancels pending capture and late session',async()=>{
 const s=setup({cachedGreeting:true,deferMicrophone:true,deferSession:true});
 s.button('Start call');await tick();const starting=s.button('Start call').props.onClick();
 await s.wait(()=>s.audio.plays===1&&s.calls.some(c=>c.url==='/api/sessions'));s.button('End call').props.onClick();
 s.releaseSession();s.grantMicrophone();await starting;await tick();
 assert.equal(s.tracks[0].readyState,'ended');assert.equal(s.stopped,1);
 assert.equal(s.text(s.status()),'Call stopped');assert.equal(s.button('Start call').props.disabled,false);
});

test('immediate hangup during welcome does not request capture or a session',async()=>{
 const s=setup({cachedGreeting:true,deferMicrophone:true,deferSession:true});
 s.button('Start call');await tick();const starting=s.button('Start call').props.onClick();
 s.button('End call').props.onClick();await starting;
 assert.equal(s.tracks.length,0);assert.equal(s.calls.some(c=>c.url==='/api/sessions'),false);
 assert.equal(s.text(s.status()),'Call stopped');
});

for(const farewell of ['Good night!','Have a good day!'])test(`disconnect after entire streamed farewell: ${farewell}`,async()=>{
 const s=setup({reply:'Thanks for calling. '+farewell});await s.start();await s.finishChunk();await s.speak();
 assert.equal(s.stopped,0);assert.equal(s.button('Start call').props.disabled,true);
 s.enqueue(0);await s.wait(()=>s.audio.plays===2);s.button('Mute').props.onClick();await s.finishChunk();
 assert.equal(s.stopped,0,'do not disconnect between streamed chunks');assert.equal(s.tracks[0].readyState,'live');assert.equal(s.tracks[0].enabled,false);
 s.enqueue(1);s.closeStream();await s.wait(()=>s.audio.plays===3);assert.equal(s.stopped,0);await s.finishChunk();
 assert.equal(s.text(s.status()),'Call ended · Mic muted');assert.equal(s.stopped,1);assert.equal(s.tracks[0].readyState,'ended');assert.equal(s.contexts[0].state,'closed');
 assert.equal(s.status().props['data-turn-owner'],'off');assert.equal(s.button('End call').props.disabled,true);assert.equal(s.button('Start call').props.disabled,false);
 assert.equal(s.button('Unmute').props['aria-pressed'],true);assert.equal(s.messages.at(-1),'Thanks for calling. '+farewell);
 await s.finishChunk();assert.equal(s.stopped,1,'duplicate ended event cannot reopen or disconnect twice');
 await s.start();assert.equal(s.tracks[1].readyState,'live');assert.equal(s.tracks[1].enabled,false,'manual mute remains authoritative in new call');s.button('End call').props.onClick();
});

test('a nonterminal reply mentioning good night does not hang up',async()=>{
 const s=setup({reply:'You said good night. Would you like to give feedback?',ended:false});await s.start();await s.finishChunk();await s.speak();s.enqueue(0);s.closeStream();await s.wait(()=>s.audio.plays===2);await s.finishChunk();
 assert.equal(s.stopped,0);assert.ok(s.text(s.status()).startsWith('Listening'));assert.equal(s.tracks[0].enabled,true);s.button('End call').props.onClick();
});

test('failed final speech exposes text and still releases the call',async()=>{
 const s=setup({failAudio:true});await s.start();await s.finishChunk();await s.speak();
 assert.equal(s.text(s.status()),'Call ended');assert.equal(s.stopped,1);assert.equal(s.tracks[0].readyState,'ended');assert.equal(s.messages.at(-1),'Thanks for calling. Good night!');
});

test('manual hangup during farewell cancels late chunks without reviving the call',async()=>{
 const s=setup();await s.start();await s.finishChunk();await s.speak();s.enqueue(0);await s.wait(()=>s.audio.plays===2);s.button('End call').props.onClick();
 s.enqueue(1);await tick();await s.finishChunk();assert.equal(s.audio.plays,2);assert.equal(s.stopped,1);assert.equal(s.text(s.status()),'Call stopped');assert.equal(s.tracks[0].readyState,'ended');
});
