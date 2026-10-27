// Browser energy endpointing. Retain a short lead-in and tolerate pauses in phone digits.
export class VoiceEndpoint {
 constructor(rate,{silenceMs=1000,maxMs=28000,minSpeechMs=160}={}){
  this.rate=rate;this.silenceMs=silenceMs;this.maxMs=maxMs;this.minSpeechMs=minSpeechMs;this.reset();
 }
 reset(){this.chunks=[];this.lead=[];this.leadMs=0;this.elapsed=0;this.quiet=0;this.voiced=0;this.started=false;}
 feed(samples){
  const frame=new Float32Array(samples),ms=frame.length/this.rate*1000;
  const rms=Math.sqrt(frame.reduce((sum,x)=>sum+x*x,0)/frame.length);
  const speech=rms>=0.015;
  if(!this.started){
   this.lead.push(frame);this.leadMs+=ms;
   while(this.leadMs>350&&this.lead.length>1)this.leadMs-=this.lead.shift().length/this.rate*1000;
   if(!speech)return null;
   this.started=true;this.chunks=this.lead;this.lead=[];
  }else this.chunks.push(frame);
  this.elapsed+=ms;this.quiet=speech?0:this.quiet+ms;if(speech)this.voiced+=ms;
  if(this.quiet>=this.silenceMs||this.elapsed>=this.maxMs){
   const chunks=this.voiced>=this.minSpeechMs?this.chunks:null;this.reset();return chunks;
  }
  return null;
 }
}

export class SpeechBargeIn {
 constructor(rate,onInterrupt=()=>{}){this.rate=rate;this.onInterrupt=onInterrupt;this.reset();}
 reset(){this.frames=[];this.voiced=0;this.quiet=0;}
 feed(samples){
  const frame=new Float32Array(samples),ms=frame.length/this.rate*1000;
  const rms=Math.sqrt(frame.reduce((sum,x)=>sum+x*x,0)/frame.length);
  if(rms>.035){this.voiced+=ms;this.quiet=0;}else this.quiet+=ms;
  if(this.quiet>250){this.reset();return null;}
  this.frames.push(frame);
  if(this.voiced<250)return null;
  const leading=this.frames;this.reset();this.onInterrupt();return leading;
 }
}
