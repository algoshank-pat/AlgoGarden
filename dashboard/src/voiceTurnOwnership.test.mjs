import {test} from 'node:test';
import assert from 'node:assert/strict';
import {VoiceTurnOwnership,submitTurnTimings} from './voiceTurnOwnership.mjs';

test('agent preparation, stream gaps and processing cannot capture speaker echo',()=>{
 let resets=0;const track={enabled:true};const gate=new VoiceTurnOwnership({onReset:()=>resets++});gate.attach({getAudioTracks:()=>[track]});
 assert.equal(track.enabled,false);gate.setOwner('customer');assert.equal(track.enabled,true);
 for(const owner of ['processing','agent','agent']){gate.setOwner(owner);assert.equal(gate.canCapture,false);assert.equal(track.enabled,false)}
 gate.setOwner('customer');assert.equal(track.enabled,true);assert.equal(resets,5);
});

test('manual mute survives playback completion, errors, cancellation and a new stream',()=>{
 const track={enabled:true};const gate=new VoiceTurnOwnership();gate.attach({getAudioTracks:()=>[track]});gate.setOwner('customer');gate.setManualMuted(true);
 gate.setOwner('agent');gate.setOwner('customer');assert.equal(track.enabled,false);
 gate.setOwner('off');gate.setOwner('customer');assert.equal(track.enabled,false);
 const next={enabled:true};gate.attach({getAudioTracks:()=>[next]});assert.equal(next.enabled,false);
 gate.setManualMuted(false);assert.equal(next.enabled,true);gate.setOwner('agent');gate.setManualMuted(false);assert.equal(next.enabled,false);
});

test('timings contain correlation and numeric metrics, and failures are observable',async()=>{
 const calls=[],errors=[];
 const ok=await submitTurnTimings('session','request',{'browser.reply_start':123,'browser.tts_to_first_playback':45},{fetcher:async(path,options)=>{calls.push({path,body:JSON.parse(options.body),keepalive:options.keepalive});return {ok:false,status:409}},onError:e=>errors.push(e)});
 assert.equal(ok,false);assert.equal(errors.length,2);assert.equal(errors[0].status,409);
 assert.ok(calls.every(c=>c.path==='/api/sessions/session/timings'&&c.body.request_id==='request'&&c.keepalive));
 assert.deepEqual(Object.keys(calls[0].body).sort(),['duration_ms','request_id','stage']);
 const network=[];assert.equal(await submitTurnTimings('session','request',{'browser.playback_started':1},{fetcher:async()=>{throw Error('offline')},onError:e=>network.push(e)}),false);
 assert.equal(network[0].status,'network');
});
