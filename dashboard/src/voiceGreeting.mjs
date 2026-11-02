// Only the current restaurant's public welcome audio is prepared ahead of a call.
export class VoiceGreeting {
 constructor(fetcher=globalThis.fetch.bind(globalThis)){this.fetcher=fetcher;this.value=null;this.pending=null}
 prepare(){
  if(this.value)return Promise.resolve(this.value);
  if(!this.pending)this.pending=this.fetcher('/api/voice/greeting',{cache:'no-store'}).then(async response=>{
   if(!response.ok)throw Error('Welcome audio could not be prepared.');
   const data=await response.json();
   if(!data.text||!data.version||!data.audio)throw Error('Invalid welcome recording.');
   const bytes=Uint8Array.from(atob(data.audio),c=>c.charCodeAt(0));
   return this.value={text:data.text,version:data.version,blob:new Blob([bytes],{type:'audio/wav'})};
  }).finally(()=>{this.pending=null});
  return this.pending;
 }
}
