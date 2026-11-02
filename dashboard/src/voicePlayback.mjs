// Versioned streamed speech: late packets and ended events cannot revive interrupted audio.
export class SpeechPlayback {
 constructor(audio,{fetcher=globalThis.fetch.bind(globalThis),onSpeaking=()=>{},onFinished=()=>{},onError=()=>{},onRequest=()=>{},onFirstAudio=()=>{},onPlaybackStart=()=>{},urlApi=URL,clock=()=>performance.now()}={}){
  Object.assign(this,{audio,fetcher,onSpeaking,onFinished,onError,onRequest,onFirstAudio,onPlaybackStart,urlApi,clock});this.version=0;this.queue=[];this.active=false;
 }
 stop(){
  this.version++;this.detachMedia?.();this.detachMedia=null;this.controller?.abort();this.controller=null;this.queue=[];this.complete=true;this.active=false;
  this.resolveFirst?.(false);this.resolveFirst=null;this.audio.pause();this.audio.removeAttribute('src');this.audio.load();
  if(this.url)this.urlApi.revokeObjectURL(this.url);this.url=null;
 }
 async start(text,correlation={},preparedAudio=null){
  this.stop();const version=this.version;this.controller=new AbortController();this.active=true;this.complete=false;this.playing=false;this.requestAt=this.clock();this.firstAudioAt=null;this.playbackAt=null;this.correlation=correlation;
  const first=new Promise(resolve=>this.resolveFirst=resolve);
  this.onRequest({at:this.requestAt,correlation});
  if(preparedAudio){
   this.firstAudioAt=this.clock();this.onFirstAudio({at:this.firstAudioAt,requestAt:this.requestAt,correlation});
   this.queue.push(preparedAudio);this.complete=true;this.next(version);return first;
  }
  this.read(text,version,correlation).catch(error=>{if(version!==this.version)return;this.stop();if(error.name!=='AbortError')this.onError(error)});
  return first;
 }
 async read(text,version,correlation){
  const response=await this.fetcher('/api/voice/speak/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,...correlation}),signal:this.controller.signal});
  if(!response.ok)throw Error('Speech service unavailable.');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
  try{
   while(version===this.version){
    const {done,value}=await reader.read();if(version!==this.version)return;
    buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});const lines=buffer.split('\n');buffer=lines.pop();
    if(done&&buffer.trim()){lines.push(buffer);buffer='';}
    for(const line of lines){
     if(!line.trim())continue;const packet=JSON.parse(line);if(packet.error)throw Error(packet.error);
     const bytes=Uint8Array.from(atob(packet.audio),c=>c.charCodeAt(0));
     if(bytes.length&&this.firstAudioAt===null){this.firstAudioAt=this.clock();this.onFirstAudio({at:this.firstAudioAt,requestAt:this.requestAt,correlation:this.correlation})}
     this.queue.push(new Blob([bytes],{type:'audio/wav'}));this.next(version);
    }
    if(done)break;
   }
   if(version!==this.version)return;
   if(this.firstAudioAt===null)throw Error('No speech audio was returned.');
   this.complete=true;this.next(version);
  }finally{if(version!==this.version)await reader.cancel().catch(()=>{});reader.releaseLock();}
 }
 next(version=this.version){
  if(version!==this.version||!this.active||this.playing)return;
  if(!this.queue.length){
   if(this.complete){this.active=false;this.resolveFirst?.(false);this.resolveFirst=null;this.onFinished()}
   return;
  }
  if(this.url)this.urlApi.revokeObjectURL(this.url);this.url=this.urlApi.createObjectURL(this.queue.shift());
  this.audio.src=this.url;this.playing=true;
  this.detachMedia?.();const url=this.url;
  const fail=error=>{if(version!==this.version)return;this.stop();this.onError(error instanceof Error?error:Error('Audio playback failed.'))};
  const playing=()=>{
   if(version!==this.version||!this.active||this.audio.src!==url)return;
   this.onSpeaking(url);
   if(this.playbackAt===null){this.playbackAt=this.clock();this.onPlaybackStart({at:this.playbackAt,requestAt:this.requestAt,firstAudioAt:this.firstAudioAt,correlation:this.correlation,event:'playing'});this.resolveFirst?.(true);this.resolveFirst=null}
  };
  this.audio.addEventListener('playing',playing);this.audio.addEventListener('error',fail);
  this.detachMedia=()=>{this.audio.removeEventListener('playing',playing);this.audio.removeEventListener('error',fail)};
  // play() acceptance is not a playback observation. Wait for the browser's
  // media 'playing' event; physical speaker onset requires acoustic loopback.
  try{Promise.resolve(this.audio.play()).catch(fail)}catch(error){fail(error)}
 }
 ended(){if(!this.active||!this.playing||!this.audio.ended)return;this.playing=false;this.next()}
}

// Wait for a full two seconds of caller silence, including between spoken digits.
export function responseSilenceMs(){return 2000;}

export const deferSpeechForCaller=(mode,started,queued)=>mode==='listening'&&(started||queued>0);
