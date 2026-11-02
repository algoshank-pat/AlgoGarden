import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ThinkingCue} from './thinkingCue.mjs';
import {VoiceEndpoint} from './voiceEndpoint.mjs';
import {responseSilenceMs} from './voicePlayback.mjs';
import {VoiceTurnOwnership} from './voiceTurnOwnership.mjs';

test('caller continuation before two seconds remains in the same answer',()=>{
 const endpoint=new VoiceEndpoint(16000,{silenceMs:responseSilenceMs()});
 const frame=x=>new Float32Array(1600).fill(x);
 endpoint.feed(frame(.1));endpoint.feed(frame(.1));
 for(let i=0;i<19;i++)assert.equal(endpoint.feed(frame(0)),null);
 endpoint.feed(frame(.1));endpoint.feed(frame(.1));
 for(let i=0;i<19;i++)assert.equal(endpoint.feed(frame(0)),null);
 assert.ok(endpoint.feed(frame(0))?.length);
 assert.equal(endpoint.lastMeasurement.endpointMs,2000);
});

function setup(){
 const timers=new Map(),errors=[];let id=0,created=0,stops=0,disconnected=0,fetches=0,decodes=0;
 const cue=new ThinkingCue({fetcher:async()=>{fetches++;return Response.json({text:'One moment.',version:'test',audio:'AAAA'})},onError:e=>errors.push(e),schedule(fn,ms){timers.set(++id,{fn,ms});return id},cancel(id){timers.delete(id)}});
 const context={state:'running',destination:{},async decodeAudioData(){decodes++;return {duration:1}},createBufferSource(){created++;return {connect(){},start(){},stop(){stops++},disconnect(){disconnected++}}}};
 const fire=()=>{const [id,{fn}]=timers.entries().next().value;timers.delete(id);fn()};
 return {cue,context,timers,fire,errors,get created(){return created},get stops(){return stops},get disconnected(){return disconnected},get fetches(){return fetches},get decodes(){return decodes}};
}
test('fast replies stay silent; slow replies speak cached audio once at two seconds',async()=>{
 const s=setup();await s.cue.prepare(s.context);s.cue.start(s.context);assert.equal(s.created,0);
 assert.equal([...s.timers.values()][0].ms,2000);
 s.cue.stop();assert.equal(s.timers.size,0);
 s.cue.start(s.context);s.fire();assert.equal(s.created,1);
 assert.equal(s.timers.size,0);assert.equal(s.fetches,1);assert.equal(s.decodes,1);
 s.cue.stop();assert.equal(s.stops,1);assert.equal(s.disconnected,1);assert.equal(s.cue.node,null);
});
test('cancelled callbacks cannot revive audio after reply, error, call end or a new turn',async()=>{
 const s=setup();await s.cue.prepare(s.context);s.cue.start(s.context);const late=[...s.timers.values()][0].fn;
 s.cue.stop();late();assert.equal(s.created,0);
 s.cue.start(null);s.context.state='closed';s.cue.start(s.context);
 assert.equal(s.timers.size,0);
});
test('preload is shared; neither the timer nor a late preload fetches or plays late',async()=>{
 const s=setup();await Promise.all([s.cue.prepare(),s.cue.prepare()]);assert.equal(s.fetches,1);
 s.cue.start(s.context);s.fire();assert.equal(s.created,0);
 await Promise.all([s.cue.prepare(s.context),s.cue.prepare(s.context)]);
 assert.equal(s.created,0);assert.equal(s.timers.size,0);assert.equal(s.decodes,1);
 s.cue.start(s.context);s.fire();s.cue.node.onended();assert.equal(s.cue.node,null);assert.equal(s.timers.size,0);
});
test('processing owns capture throughout the filler; completion never overrides manual mute',async()=>{
 const s=setup(),owner=new VoiceTurnOwnership(),track={enabled:true};owner.attach({getAudioTracks:()=>[track]});
 owner.setOwner('processing');await s.cue.prepare(s.context);s.cue.start(s.context);s.fire();
 assert.equal(track.enabled,false);owner.setManualMuted(true);s.cue.node.onended();
 assert.equal(owner.owner,'processing');assert.equal(track.enabled,false);
 owner.setOwner('agent');s.cue.stop();assert.equal(track.enabled,false);
 owner.setOwner('customer');assert.equal(track.enabled,false);owner.setManualMuted(false);assert.equal(track.enabled,true);
});
test('playback failure stays observable and cannot block the answer',async()=>{
 const s=setup();await s.cue.prepare(s.context);s.context.createBufferSource=()=>{throw Error('device closed')};
 s.cue.start(s.context);s.fire();assert.equal(s.errors.length,1);assert.equal(s.timers.size,0);
 s.cue.stop();assert.equal(s.cue.node,null);
});
test('failed preload can retry without making a slow turn wait',async()=>{
 const s=setup(),fetcher=s.cue.fetcher;s.cue.fetcher=async()=>Response.json({}, {status:503});
 await assert.rejects(s.cue.prepare());s.cue.start(s.context);s.fire();assert.equal(s.created,0);
 s.cue.fetcher=fetcher;await s.cue.prepare(s.context);s.cue.start(s.context);s.fire();assert.equal(s.created,1);s.cue.stop();
});
