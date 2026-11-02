import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ConversationWait} from './conversationWait.mjs';
import {ThinkingCue} from './thinkingCue.mjs';

test('native browser timer defaults keep the global receiver',()=>{
 const originalSet=globalThis.setTimeout,originalClear=globalThis.clearTimeout;
 try{
  globalThis.setTimeout=function(){assert.equal(this,globalThis);return 1};
  globalThis.clearTimeout=function(){assert.equal(this,globalThis)};
  const wait=new ConversationWait();wait.clear();wait.schedule(()=>{},1);
  const cue=new ThinkingCue();cue.stop();cue.schedule(()=>{},1);
 }finally{globalThis.setTimeout=originalSet;globalThis.clearTimeout=originalClear}
});

function setup(){
 let callback,delay,replyCount=0;const calls=[];
 const wait=new ConversationWait({schedule(fn,ms){callback=fn;delay=ms;return 1},cancel(){callback=null},request:async(path,options)=>{calls.push({path,options});return {ok:true,json:async()=>path.endsWith('/arm')?{remaining_ms:20000}:{reply:'Please confirm',ended:false}}},onReply:()=>replyCount++});
 return {wait,calls,get callback(){return callback},get delay(){return delay},get replyCount(){return replyCount}};
}
test('timer starts only when armed after display/playback and fires once',async()=>{
 const s=setup();assert.equal(s.callback,undefined);
 await s.wait.arm('a',{token:'one'});assert.equal(s.delay,20000);
 const fire=s.callback;await fire();await fire();assert.equal(s.replyCount,1);
 assert.equal(JSON.parse(s.calls.at(-1).options.body).input_kind,'timeout');
 assert.equal(s.wait.current,null);
});
test('customer input/end/restart cancel old callbacks, including in-flight replies',async()=>{
 const s=setup();await s.wait.arm('a',{token:'one'});const late=s.callback;
 s.wait.clear();await late();assert.equal(s.replyCount,0);
 assert.ok(s.calls.some(c=>c.path.endsWith('/cancel')));
 await s.wait.arm('b',{token:'two'});const next=s.callback;
 await s.wait.arm('c',{token:'three'});await next();assert.equal(s.replyCount,0);
});
test('late arm response cannot schedule an old-session timeout',async()=>{
 let release;let scheduled=0;
 const w=new ConversationWait({request:async path=>path.endsWith('/arm')?new Promise(r=>release=r):{ok:true},schedule(){scheduled++}});
 const pending=w.arm('old',{token:'old'});w.clear();
 release({ok:true,json:async()=>({remaining_ms:15000})});await pending;
 assert.equal(scheduled,0);
});
