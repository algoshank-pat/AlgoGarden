// Actual React VoiceTest + native browser audio. Synthetic microphone and API
// delay fixtures only; no hardware capture or reservation writes.
import React from '../dashboard/node_modules/react/index.js';
import {createRoot} from '../dashboard/node_modules/react-dom/client.js';
import VoiceTest from '../dashboard/src/VoiceTest.jsx';
const realFetch=globalThis.fetch.bind(globalThis),sleep=ms=>new Promise(r=>setTimeout(r,ms));
const evidence={checks:[],backend:[],cues:[],playing:[],limitations:'Native Web Audio graph signal and HTMLAudio playing events; synthetic microphone and API replies, not physical speaker/microphone latency.'};
const check=(name,pass)=>{evidence.checks.push({name,pass:!!pass});if(!pass)throw Error(name)};
const sid='00000000-0000-4000-8000-000000000001';let inputGain,track,turn=0,cueData,replyData;
const button=text=>[...document.querySelectorAll('button')].find(n=>n.textContent.trim()===text);
const owner=()=>document.querySelector('[data-turn-owner]')?.dataset.turnOwner;
const wait=async predicate=>{for(let i=0;i<700;i++){if(predicate())return;await sleep(20)}throw Error('Timed out: '+owner())};
const originalCreate=AudioContext.prototype.createBufferSource;
AudioContext.prototype.createBufferSource=function(){
 const node=originalCreate.call(this),start=node.start.bind(node),stop=node.stop.bind(node),ctx=this;
 const analyser=ctx.createAnalyser(),silent=ctx.createGain();silent.gain.value=0;node.connect(analyser);analyser.connect(silent);silent.connect(ctx.destination);
 let record,timer;
 node.start=(...args)=>{record={at:performance.now(),turn,trackEnabled:track.enabled,signalAt:null};evidence.cues.push(record);start(...args);
  timer=setInterval(()=>{const data=new Float32Array(analyser.fftSize);analyser.getFloatTimeDomainData(data);if(data.some(v=>Math.abs(v)>.001)&&record.signalAt===null)record.signalAt=performance.now()},10);
 };
 node.stop=(...args)=>{if(record)record.stoppedAt=performance.now();return stop(...args)};
 node.addEventListener('ended',()=>{if(record)record.endedAt=performance.now();clearInterval(timer);analyser.disconnect();silent.disconnect()});
 return node;
};
Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
 const ctx=new AudioContext();await ctx.resume();const oscillator=ctx.createOscillator();inputGain=ctx.createGain();inputGain.gain.value=0;
 const destination=ctx.createMediaStreamDestination();oscillator.connect(inputGain);inputGain.connect(destination);oscillator.start();
 track=destination.stream.getTracks()[0];const stop=track.stop.bind(track);track.stop=()=>{stop();ctx.close()};return destination.stream;
}});
globalThis.fetch=async(url,options={})=>{
 const path=String(url);
 if(path==='/api/voice/thinking-cue')return Response.json(cueData);
 if(path==='/api/voice/greeting')return Response.json({...replyData,text:'Welcome. How can I help?'});
 if(path==='/api/sessions')return Response.json({session_id:sid,greeting:'Welcome. How can I help?',spoken_greeting:'Welcome. How can I help?'});
 if(path.includes('/transcribe'))return Response.json({text:'What are your opening hours?',input_kind:'text'});
 if(path.endsWith('/turns')){
  turn++;const record={turn,at:performance.now()};evidence.backend.push(record);
  if(turn>1)await sleep(3500);record.replyAt=performance.now();
  return Response.json({reply:'One moment.',spoken_reply:'One moment.',ended:false,request_id:JSON.parse(options.body).request_id});
 }
 if(path.endsWith('/speak/stream'))return new Response(new ReadableStream({async start(c){
  c.enqueue(new TextEncoder().encode(JSON.stringify({audio:replyData.audio,index:0})+'\n'));
  if(turn===2){await sleep(1700);c.enqueue(new TextEncoder().encode(JSON.stringify({audio:replyData.audio,index:1})+'\n'))}
  c.close();
 }}));
 if(path.startsWith('/api/'))return Response.json({saved:true});
 return realFetch(url,options);
};
document.addEventListener('playing',event=>{if(event.target.tagName==='AUDIO')evidence.playing.push({at:performance.now(),turn,trackEnabled:track?.enabled})},true);
cueData=await realFetch('/cue.json').then(r=>r.json());replyData=cueData;
createRoot(document.querySelector('#root')).render(<VoiceTest/>);
const utterance=async()=>{inputGain.gain.value=.12;await sleep(400);inputGain.gain.value=0};
document.querySelector('#run').onclick=async()=>{
 document.querySelector('#run').disabled=true;
 try{
  button('Start call').click();await wait(()=>owner()==='customer');
  await utterance();await wait(()=>turn===1&&owner()==='customer');
  check('Fast reply has no filler',evidence.cues.length===0);
  await utterance();await wait(()=>evidence.cues.length===1);
  button('Mute').click();await wait(()=>turn===2&&owner()==='customer');
  const cue=evidence.cues[0],backend=evidence.backend[1];cue.delayMs=cue.at-backend.at;
  check('Slow reply plays cue after two seconds',cue.delayMs>=1950&&cue.delayMs<2600);
  check('Cached phrase produced native Web Audio signal',cue.signalAt!==null);
  check('Only one filler for the slow turn',evidence.cues.length===1);
  check('Processing mic disabled during filler',!cue.trackEnabled);
  check('Manual mute survives filler and both response chunks',!track.enabled&&document.querySelector('[data-manual-muted]').dataset.manualMuted==='true');
  check('All response chunks played with microphone disabled',evidence.playing.every(e=>e.trackEnabled!==true));
  check('Cue finished or was stopped before response audio',Math.min(cue.endedAt||Infinity,cue.stoppedAt||Infinity)<=evidence.playing.find(e=>e.turn===2).at);
  button('Unmute').click();check('Unmute restores listening after complete reply',track.enabled);
  await utterance();await wait(()=>evidence.cues.length===2);button('End call').click();await sleep(2000);
  check('End call stops cue and rejects late response',!!evidence.cues[1].stoppedAt&&evidence.playing.filter(e=>e.turn===3).length===0&&owner()==='off');
  evidence.pass=evidence.checks.every(c=>c.pass);
 }catch(error){evidence.error=error.message;evidence.pass=false;button('End call')?.click()}
 document.querySelector('#result').textContent=JSON.stringify(evidence,null,2);
 await realFetch('/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(evidence)});
};
