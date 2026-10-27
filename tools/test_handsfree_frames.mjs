import fs from 'node:fs';
import {VoiceEndpoint,SpeechBargeIn} from '../dashboard/src/voiceEndpoint.mjs';
const [input,output,interrupt]=process.argv.slice(2),bytes=fs.readFileSync(input);
const samples=new Float32Array(bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength));
const endpoint=new VoiceEndpoint(16000);let playing=interrupt==='interrupt',paused=0,frames=0,turns=[];
const detector=new SpeechBargeIn(16000,()=>{playing=false;paused++;});
const audio=new Float32Array(samples.length+16000*2);audio.set(samples);
for(let i=0;i<audio.length;i+=512){
 const frame=audio.slice(i,i+512);frames++;
 let toFeed=[frame];
 if(playing){const lead=detector.feed(frame);if(!lead)continue;toFeed=lead;}
 for(const f of toFeed){const turn=endpoint.feed(f);if(turn)turns.push(turn);}
}
if(turns.length!==1)throw Error(`Expected one automatic turn, got ${turns.length}`);
if(interrupt==='interrupt'&&paused!==1)throw Error('Speech failed to interrupt playing reply');
const chunks=turns[0],length=chunks.reduce((n,x)=>n+x.length,0),result=new Float32Array(length);let at=0;
for(const chunk of chunks){result.set(chunk,at);at+=chunk.length;}
fs.writeFileSync(output,Buffer.from(result.buffer));
process.stdout.write(JSON.stringify({automatic_turns:turns.length,playback_pause_callbacks:paused,leading_speech_retained:!playing,captured_samples:length}));
