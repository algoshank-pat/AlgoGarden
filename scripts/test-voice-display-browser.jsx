// Browser integration fixture: actual VoiceTest/HTMLAudio playback, synthetic
// input stream and API responses. No hardware microphone or reservation writes.
import React from '../dashboard/node_modules/react/index.js';
import {createRoot} from '../dashboard/node_modules/react-dom/client.js';
import VoiceTest from '../dashboard/src/VoiceTest.jsx';
const realFetch=globalThis.fetch.bind(globalThis),sleep=ms=>new Promise(r=>setTimeout(r,ms));
const output=document.querySelector('#result'),evidence={checks:[],playing:[],display:[],api:[],speech:[],limitations:'Synthetic input and fixture API; native HTMLAudio playing events, not physical speaker measurements.'};
const sid='00000000-0000-4000-8000-000000000001';
let inputGain,inputContext,failed=false,turn=0,playing=0,wav;
const assistant=()=>[...document.querySelectorAll('.voice-utterance.assistant p')].map(n=>n.textContent);
const status=()=>document.querySelector('.voice-call-status')?.textContent||'';
const wait=async(check)=>{for(let i=0;i<500;i++){if(check())return;await sleep(20)}throw Error('Timed out; status: '+status())};
const check=(name,ok)=>{evidence.checks.push({name,pass:!!ok});if(!ok)throw Error(name)};
Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
 inputContext=new AudioContext();await inputContext.resume();
 const oscillator=inputContext.createOscillator();inputGain=inputContext.createGain();inputGain.gain.value=0;
 const destination=inputContext.createMediaStreamDestination();oscillator.connect(inputGain);inputGain.connect(destination);oscillator.start();
 const track=destination.stream.getTracks()[0],stop=track.stop.bind(track);track.stop=()=>{stop();inputContext.close()};
 return destination.stream;
}});
globalThis.fetch=async(url,options={})=>{
 const path=String(url),body=options.body&&typeof options.body==='string'?JSON.parse(options.body):{};
 if(path==='/api/sessions')return Response.json({session_id:sid,greeting:'Welcome. How can I help?',spoken_greeting:'Welcome. How can I help?'});
 if(path.includes('/transcribe'))return Response.json({text:'October thirty first.',input_kind:'text'});
 if(path.endsWith('/turns')){
  turn++;const reply=failed?'Your reservation is confirmed.':"That date is in the past. What future date works for you?";
  evidence.api.push({at:performance.now(),text:reply});
  return Response.json({reply,spoken_reply:reply,ended:false,structured:{fixture:true},request_id:body.request_id});
 }
 if(path.endsWith('/speak/stream')){
  evidence.speech.push({at:performance.now(),text:body.text});
  if(failed)return new Response('',{status:503});
  // Intentionally delay synthesis; one response has a late second chunk.
  return new Response(new ReadableStream({async start(c){
   await sleep(850);c.enqueue(new TextEncoder().encode(JSON.stringify({audio:wav,index:0})+'\n'));
   if(turn===1){await sleep(1300);c.enqueue(new TextEncoder().encode(JSON.stringify({audio:wav,index:1})+'\n'))}
   c.close();
  }}));
 }
 if(path.startsWith('/api/'))return Response.json({saved:true});
 return realFetch(url,options);
};
document.addEventListener('playing',event=>{if(event.target.tagName==='AUDIO'){playing++;evidence.playing.push({at:performance.now(),number:playing})}},true);
let seen=0;
new MutationObserver(()=>{const messages=assistant();while(seen<messages.length)evidence.display.push({at:performance.now(),text:messages[seen++]})}).observe(document.querySelector('#root'),{childList:true,subtree:true});
createRoot(document.querySelector('#root')).render(<VoiceTest/>);
async function utterance(){inputGain.gain.value=.12;await sleep(400);inputGain.gain.value=0;}
document.querySelector('#run').onclick=async()=>{
 document.querySelector('#run').disabled=true;
 try{
  wav=await realFetch('/sample.txt').then(r=>r.text());
  document.querySelector('.voice-action').click();
  await wait(()=>evidence.speech.length===1);await sleep(300);
  check('Greeting hidden before native playback',assistant().length===0);
  await wait(()=>assistant().length===1);
  check('Greeting appears on native playing',playing===1);
  await wait(()=>status().startsWith('Listening'));
  await utterance();await wait(()=>evidence.api.length===1);await sleep(300);
  check('API reply stays hidden while synthesis is pending',assistant().length===1);
  await wait(()=>assistant().length===2);
  check('Reply appears at first native playback',playing===2);
  document.querySelector('.mute-toggle').click();
  await wait(()=>playing===3);await wait(()=>status().startsWith('Listening'));
  check('Second chunk does not duplicate transcript',assistant().length===2);
  check('Manual mute survives all chunks',document.querySelector('.mute-toggle').getAttribute('aria-pressed')==='true');
  document.querySelector('.mute-toggle').click();
  failed=true;await utterance();await wait(()=>document.querySelector('.voice-error')?.textContent.includes('shown as text'));
  check('Audio failure exposes confirmed reply with explicit text fallback',assistant().at(-1)==='Your reservation is confirmed.'&&playing===3);
  failed=false;await utterance();await wait(()=>evidence.api.length===3);await sleep(200);
  document.querySelector('.hangup').click();await sleep(1600);
  check('End call blocks late speech and late transcript',playing===3&&assistant().length===3);
  check('Normal replies never precede native playing',evidence.display.slice(0,2).every((d,i)=>d.at>=evidence.playing[i].at));
  evidence.passed=true;
 }catch(error){evidence.passed=false;evidence.error=String(error)}
 output.textContent=JSON.stringify(evidence,null,2);
 await realFetch('/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(evidence)});
};
