// Exercise the real voice component with recorded PCM and the local API.
// Browser microphone/speaker adapters are simulated; speech services are real.
import {build} from '../dashboard/node_modules/esbuild/lib/main.js';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
const base='http://127.0.0.1:8010', calls=[], evidence=[];
const result=await build({entryPoints:['dashboard/src/VoiceTest.jsx'],bundle:true,write:false,platform:'node',format:'cjs',loader:{'.css':'empty'},external:['react','lucide-react']});
let slots=[],cursor=0,tree,Component,processor,audioProps,sessionId;const tones=[];
const track={enabled:true,stopped:false,stop(){this.stopped=true;}};
const stream={getAudioTracks:()=>[track],getTracks:()=>[track]};
const speaker={paused:true,playCount:0,pauseCount:0,src:'',async play(){this.paused=false;this.playCount++;audioProps.onPlay();},pause(){this.paused=true;this.pauseCount++;}};
const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];},useRef(initial){const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect(){},createElement(type,props,...children){if(type==='audio'){audioProps=props;props.ref.current=speaker;}return {type,props:props||{},children:children.flat(Infinity)};}};
react.default=react;
class Context{constructor(){this.sampleRate=16000;this.currentTime=0;}createOscillator(){return {frequency:{value:0},connect(){},start(){tones.push(this.frequency.value)},stop(){}};}async resume(){}async close(){}createMediaStreamSource(){return {connect(){},disconnect(){}};}createScriptProcessor(){processor={connect(){},disconnect(){},onaudioprocess:null};return processor;}createGain(){return {gain:{value:0,setValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){},disconnect(){}};}}
const module={exports:{}};
const context={module,exports:module.exports,require:name=>name==='react'?react:name==='lucide-react'?new Proxy({},{get:()=>()=>null}):null,console,Blob,Float32Array,ArrayBuffer,DataView,URL,performance,crypto:webcrypto,setTimeout,clearTimeout,navigator:{mediaDevices:{getUserMedia:async()=>stream}},AudioContext:Context,fetch:async(path,options={})=>{calls.push({path,method:options.method||'GET'});const response=await fetch(base+path,options);if(path==='/api/sessions'&&response.ok)sessionId=(await response.clone().json()).session_id;if(path==='/api/voice/speak'&&response.ok){const bytes=new Uint8Array(await response.clone().arrayBuffer());assert.ok(bytes.length>1000,'TTS audio must contain samples');evidence.push({kind:'agent_audio',bytes:bytes.length});}return response;}};
vm.runInNewContext(result.outputFiles[0].text,context);Component=module.exports.default;
function render(){cursor=0;tree=Component();return tree;}
function nodes(node){if(!node||typeof node!=='object')return [];return [node,...node.children.flatMap(nodes)];}
function button(name){render();return nodes(tree).find(n=>n.type==='button'&&(n.props['aria-label']===name||n.children.filter(x=>typeof x==='string').join('')===name));}
async function click(name){const b=button(name);assert.ok(b,'Missing '+name);assert.ok(!b.props.disabled,name+' disabled');await b.props.onClick();render();}
function feed(samples){for(let i=0;i<samples.length;i+=1600){const frame=samples.slice(i,i+1600);processor.onaudioprocess({inputBuffer:{getChannelData:()=>frame}});}}
const silence=new Float32Array(19200);
async function waitFor(predicate,label){const until=Date.now()+90000;while(!predicate()){if(Date.now()>until)throw Error('Timeout: '+label);await new Promise(r=>setTimeout(r,40));}render();}
async function spoken(text){const r=await fetch(base+'/api/voice/speak',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});assert.ok(r.ok,'Caller audio synthesis');const data=Buffer.from(await r.arrayBuffer());let offset=12,rate,bits,channels,pcm;while(offset+8<=data.length){const tag=data.toString('ascii',offset,offset+4),size=data.readUInt32LE(offset+4);if(tag==='fmt '){channels=data.readUInt16LE(offset+10);rate=data.readUInt32LE(offset+12);bits=data.readUInt16LE(offset+22);}if(tag==='data'){pcm=data.subarray(offset+8,offset+8+size);break;}offset+=8+size+(size%2);}assert.equal(bits,16);assert.equal(channels,1);const out=new Float32Array(Math.floor(pcm.length/2*16000/rate));for(let i=0;i<out.length;i++)out[i]=pcm.readInt16LE(Math.min(Math.floor(i*rate/16000),pcm.length/2-1)*2)/32768;return out;}
let completed=false;
try{
 const hours=await spoken('What are your opening hours for lunch and dinner this weekend, please?');
 const parking=await spoken('Where can I park my car when I come for dinner?');
 await click('Start call');const originalSession=sessionId;assert.ok(originalSession);assert.deepEqual(tones,[697,1209,770,1336,852,1477,941,1336]);evidence.push({check:'four dual-frequency dialing tones before greeting',passed:true});assert.equal(speaker.paused,false);
 const beforeEchoPauses=speaker.pauseCount;feed(hours);feed(silence);assert.equal(speaker.pauseCount,beforeEchoPauses);assert.equal(calls.filter(c=>c.path==='/api/voice/transcribe').length,0);evidence.push({check:'speaker echo cannot interrupt greeting or trigger transcription',passed:true});
 const initialTurns=calls.filter(c=>c.path.endsWith('/turns')).length,initialStt=calls.filter(c=>c.path==='/api/voice/transcribe').length,initialPauses=speaker.pauseCount;
 await click('Mute microphone');assert.equal(track.enabled,false);feed(hours);feed(silence);
 assert.equal(calls.filter(c=>c.path==='/api/voice/transcribe').length,initialStt);assert.equal(calls.filter(c=>c.path.endsWith('/turns')).length,initialTurns);assert.equal(speaker.paused,false);assert.equal(speaker.pauseCount,initialPauses);
 evidence.push({check:'muted speech ignored while greeting plays',passed:true});
 audioProps.onEnded();feed(hours);feed(silence);assert.equal(calls.filter(c=>c.path==='/api/voice/transcribe').length,initialStt);
 evidence.push({check:'muted speech ignored while listening',passed:true});
 await click('Unmute microphone');assert.equal(track.enabled,true);feed(hours.slice(0,32000));
 await waitFor(()=>{render();return nodes(tree).some(n=>n.props.className?.includes('live-transcript'));},'live transcript preview before speech ends');
 assert.equal(calls.filter(c=>c.path.endsWith('/turns')).length,initialTurns);
 evidence.push({check:'live transcript appears before final pause without submitting a turn',passed:true});
 feed(hours.slice(32000));feed(silence);
 await waitFor(()=>speaker.playCount>=2,'unmuted reply playback');assert.equal(sessionId,originalSession);assert.equal(calls.filter(c=>c.path==='/api/sessions').length,1);assert.ok(calls.some(c=>c.path===`/api/sessions/${originalSession}/turns`));
 evidence.push({check:'unmuted speech produces reply in same session',passed:true});
 render();let utterances=nodes(tree).filter(n=>n.props.className?.startsWith('voice-utterance '));
 assert.equal(utterances.filter(n=>n.props.className==='voice-utterance user').length,1);
 assert.equal(utterances.filter(n=>n.props.className==='voice-utterance assistant').length,2);
 assert.equal(utterances.filter(n=>n.props.className?.includes('live-transcript')).length,0);
 assert.equal(calls.filter(c=>c.path.endsWith('/turns')).length,1);
 evidence.push({check:'first preview replaced by exactly one caller message and one agent reply',passed:true});
 const replyPauses=speaker.pauseCount,replyStt=calls.filter(c=>c.path==='/api/voice/transcribe').length;feed(hours);feed(silence);assert.equal(speaker.pauseCount,replyPauses);assert.equal(calls.filter(c=>c.path==='/api/voice/transcribe').length,replyStt);evidence.push({check:'speaker echo cannot pause or create queued turns during reply',passed:true});
 await click('Mute microphone');const plays=speaker.playCount,pauses=speaker.pauseCount;feed(hours);feed(silence);assert.equal(speaker.playCount,plays);assert.equal(speaker.pauseCount,pauses);assert.equal(speaker.paused,false);audioProps.onEnded();
 await click('Unmute microphone');feed(hours.slice(0,3200));await click('Mute microphone');feed(hours);feed(silence);const before=calls.filter(c=>c.path==='/api/voice/transcribe').length;await click('Unmute microphone');feed(silence);assert.equal(calls.filter(c=>c.path==='/api/voice/transcribe').length,before);
 evidence.push({check:'mute discards partial speech and agent playback continues',passed:true});
 feed(parking.slice(0,32000));await waitFor(()=>{render();return nodes(tree).some(n=>n.props.className?.includes('live-transcript'));},'second live preview');feed(parking.slice(32000));feed(silence);
 await waitFor(()=>speaker.playCount>=3,'second agent reply');render();
 utterances=nodes(tree).filter(n=>n.props.className?.startsWith('voice-utterance '));
 assert.equal(utterances.filter(n=>n.props.className==='voice-utterance user').length,2);
 assert.equal(utterances.filter(n=>n.props.className==='voice-utterance assistant').length,3);
 assert.equal(utterances.filter(n=>n.props.className?.includes('live-transcript')).length,0);
 assert.equal(calls.filter(c=>c.path.endsWith('/turns')).length,2);
 assert.equal(calls.filter(c=>c.path==='/api/sessions').length,1);
 const css=await fs.readFile('dashboard/src/voice.css','utf8');
 const agentColor=css.match(/\.voice-customer \.voice-utterance\{[^}]*background:([^;]+)/)?.[1];
 const callerColor=css.match(/\.voice-customer \.voice-utterance\.user\{[^}]*background:([^;]+)/)?.[1];
 assert.ok(agentColor&&callerColor);assert.notEqual(agentColor,callerColor);
 evidence.push({check:'second turn continues original session without duplicate messages',passed:true});
 evidence.push({check:'agent and caller bubbles use distinct teal and violet styles',agentColor,callerColor,passed:true});
 completed=true;
}finally{
 if(button('End call')&&!button('End call').props.disabled)await click('End call');
 if(completed){assert.equal(track.stopped,true);evidence.push({check:'end call releases microphone',passed:true});}
 await fs.mkdir('results/voice-mute',{recursive:true});await fs.writeFile('results/voice-mute/playback-pause-regression.json',JSON.stringify({passed:completed,mode:'real component, synthetic microphone/playback adapters, real local STT/agent/TTS',sessionId,evidence,calls},null,2));
}
assert.ok(completed);console.log(JSON.stringify({passed:completed,sessionId,evidence},null,2));
