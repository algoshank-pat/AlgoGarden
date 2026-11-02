import {test} from 'node:test';
import assert from 'node:assert/strict';
import {SpeechPlayback,responseSilenceMs,deferSpeechForCaller} from './voicePlayback.mjs';
import {VoiceEndpoint,SpeechBargeIn} from './voiceEndpoint.mjs';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const packet=index=>new TextEncoder().encode(JSON.stringify({index,audio:btoa('RIFF audio')})+'\n');
test('default fetch is bound to globalThis while injected fetchers are preserved',async()=>{
 const original=globalThis.fetch;let receiver;
 try{
  globalThis.fetch=function(){receiver=this;if(this!==globalThis)throw new TypeError('Illegal invocation');return Promise.resolve({ok:true,body:new ReadableStream({start(c){c.close()}})})};
  const audio={pause(){},removeAttribute(){},load(){}};
  const playback=new SpeechPlayback(audio);
  assert.equal(await playback.start('Hello'),false);
  assert.equal(receiver,globalThis);
  const injected=async()=>({ok:true,body:new ReadableStream({start(c){c.close()}})});
  const custom=new SpeechPlayback(audio,{fetcher:injected});
  assert.equal(custom.fetcher,injected);
  assert.equal(await custom.start('Hello'),false);
 }finally{globalThis.fetch=original}
});
function setup(){
 let controller;const stream=new ReadableStream({start(c){controller=c}});
 const listeners=new Map();
 const audio={plays:0,ended:false,src:'',autoPlaying:true,addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,new Set());listeners.get(name).add(fn)},removeEventListener(name,fn){listeners.get(name)?.delete(fn)},emit(name){for(const fn of [...(listeners.get(name)||[])])fn()},play(){this.plays++;this.ended=false;if(this.autoPlaying)queueMicrotask(()=>this.emit('playing'));return Promise.resolve()},pause(){},removeAttribute(){this.src=''},load(){}};
 let finished=0;const errors=[];
 const playback=new SpeechPlayback(audio,{fetcher:async()=>({ok:true,body:stream}),onFinished:()=>finished++,onError:e=>errors.push(e),urlApi:{createObjectURL:()=>`blob:${audio.plays}`,revokeObjectURL(){}}});
 return {playback,audio,controller,get finished(){return finished},errors};
}
test('first audio plays before later synthesis finishes; no listening gap between chunks',async()=>{
 const s=setup();const first=s.playback.start('Two sentences.');await tick();s.controller.enqueue(packet(0));assert.equal(await first,true);
 assert.equal(s.audio.plays,1);assert.equal(s.finished,0);s.controller.enqueue(packet(1));await tick();s.controller.close();await tick();
 s.audio.ended=true;s.playback.ended();await tick();assert.equal(s.audio.plays,2);assert.equal(s.finished,0);
 s.audio.ended=true;s.playback.ended();assert.equal(s.finished,1);assert.equal(s.errors.length,0);
});

test('play promise is not a media playback measurement; playing event is required',async()=>{
 const s=setup();s.audio.autoPlaying=false;const starts=[],received=[];
 s.playback.onPlaybackStart=e=>starts.push(e);s.playback.onFirstAudio=e=>received.push(e);
 let resolved=false;const first=s.playback.start('Reply.',{session_id:'session',request_id:'request'}).then(x=>{resolved=true;return x});
 await tick();s.controller.enqueue(packet(0));s.controller.close();await tick();
 assert.equal(s.audio.plays,1);assert.equal(starts.length,0);assert.equal(resolved,false);assert.equal(received.length,1);
 s.audio.emit('playing');assert.equal(await first,true);assert.equal(starts.length,1);assert.equal(starts[0].event,'playing');assert.equal(starts[0].correlation.request_id,'request');
 s.audio.emit('playing');assert.equal(starts.length,1);
});

test('speech request remains measurable when HTTP fails before audio arrives',async()=>{
 const s=setup(),requests=[],starts=[];s.playback.fetcher=async()=>({ok:false});
 s.playback.onRequest=e=>requests.push(e);s.playback.onPlaybackStart=e=>starts.push(e);
 assert.equal(await s.playback.start('Reply.',{request_id:'request'}),false);
 assert.equal(requests.length,1);assert.equal(requests[0].correlation.request_id,'request');
 assert.equal(starts.length,0);assert.equal(s.errors.length,1);assert.equal(s.playback.active,false);
});

test('media errors and stale media events release first-audio waiters without reviving playback',async()=>{
 const s=setup();s.audio.autoPlaying=false;const first=s.playback.start('Reply.');await tick();s.controller.enqueue(packet(0));await tick();
 s.audio.emit('error');assert.equal(await first,false);assert.equal(s.errors.length,1);assert.equal(s.playback.active,false);
 s.audio.emit('playing');assert.equal(s.playback.active,false);s.controller.close();await tick();
});
test('interrupting stops pending playback and late packets cannot speak',async()=>{
 const s=setup();const first=s.playback.start('Long reply.');await tick();s.controller.enqueue(packet(0));await first;
 s.playback.stop();s.controller.enqueue(packet(1));await tick();assert.equal(s.audio.plays,1);assert.equal(s.playback.queue.length,0);assert.equal(s.finished,0);
});
test('stop before first audio releases the waiting caller',async()=>{
 const s=setup();const first=s.playback.start('Reply.');await tick();s.playback.stop();assert.equal(await first,false);
 s.controller.close();await tick();assert.equal(s.audio.plays,0);
});

test('prepared welcome starts without a fetch and still requires native playing',async()=>{
 const s=setup();s.audio.autoPlaying=false;let fetches=0,starts=0;
 s.playback.fetcher=()=>{fetches++;throw Error('Cached greeting must not synthesize')};
 s.playback.onPlaybackStart=()=>starts++;
 const result=s.playback.start('Hello.',{},new Blob(['prepared wav'],{type:'audio/wav'}));
 assert.equal(s.audio.plays,1);assert.equal(fetches,0);assert.equal(starts,0);
 s.audio.emit('playing');assert.equal(await result,true);assert.equal(starts,1);
 s.audio.ended=true;s.playback.ended();assert.equal(s.finished,1);
});
test('all answers allow two seconds of silence, including digit and name pauses',()=>{
 assert.equal(responseSilenceMs('What time would you like?'),2000);
 assert.equal(responseSilenceMs('What phone number can I use for your reservation?'),2000);
 assert.equal(responseSilenceMs('Please spell your first and last name.'),2000);
 assert.equal(responseSilenceMs('High chairs are available.',{expected_field:'name'}),2000);
 const e=new VoiceEndpoint(16000,{silenceMs:responseSilenceMs('Please spell your name.')});const f=x=>new Float32Array(1600).fill(x);
 for(let letter=0;letter<5;letter++){e.feed(f(.1));e.feed(f(.1));for(let i=0;i<8;i++)assert.equal(e.feed(f(0)),null)}
 let utterance;for(let i=0;i<12;i++)utterance=e.feed(f(0));assert.ok(utterance?.length);
});
test('speech detected during playback preserves leading interruption frames',()=>{
 const e=new VoiceEndpoint(16000,{silenceMs:450});let stopped=0;const b=new SpeechBargeIn(16000,()=>stopped++);let leading;
 for(let i=0;i<3;i++)leading=b.feed(new Float32Array(1600).fill(.1));
 assert.equal(stopped,1);for(const f of leading)e.feed(f);let result;for(let i=0;i<5;i++)result=e.feed(new Float32Array(1600));
 assert.ok(result?.length>=8);assert.equal(result[0][0],Math.fround(.1));
});

test('a caller interruption during processing takes priority over the old spoken reply',()=>{
 assert.equal(deferSpeechForCaller('listening',true,0),true);
 assert.equal(deferSpeechForCaller('listening',false,1),true);
 assert.equal(deferSpeechForCaller('processing',false,0),false);
 assert.equal(deferSpeechForCaller('listening',false,0),false);
});
