// One preloaded public phrase per slow backend turn. Never queue with the reply
// or take ownership of the microphone: processing already suspends capture.
export class ThinkingCue {
 constructor({fetcher=globalThis.fetch.bind(globalThis),schedule=(...args)=>globalThis.setTimeout(...args),cancel=id=>globalThis.clearTimeout(id),onError=e=>console.warn('voice.thinking_cue_failed',e.message)}={}){
  Object.assign(this,{fetcher,schedule,cancel,onError});this.version=0;
 }
 async prepare(context=null){
  if(!this.bytes){
   if(!this.pending)this.pending=this.fetcher('/api/voice/thinking-cue',{cache:'no-store'}).then(async response=>{
    if(!response.ok)throw Error('Waiting audio could not be prepared.');
    const data=await response.json();
    if(data.text!=='One moment.'||!data.version||!data.audio)throw Error('Invalid waiting recording.');
    this.bytes=Uint8Array.from(atob(data.audio),c=>c.charCodeAt(0)).buffer;
   }).finally(()=>{this.pending=null});
   await this.pending;
  }
  if(context&&!this.buffer){
   if(!this.decoding)this.decoding=context.decodeAudioData(this.bytes.slice(0)).then(buffer=>{this.buffer=buffer}).finally(()=>{this.decoding=null});
   await this.decoding;
  }
 }
 start(context){
  this.stop();if(!context||context.state!=='running')return;
  const version=this.version;
  this.timer=this.schedule(()=>{
   this.timer=null;
   // Late preloads must never cause a delayed filler or delay the real answer.
   if(version!==this.version||context.state!=='running'||!this.buffer)return;
   try{
    const node=context.createBufferSource();this.node=node;node.buffer=this.buffer;
    node.connect(context.destination);
    node.onended=()=>{node.disconnect();if(this.node===node)this.node=null};
    node.start();
   }catch(error){this.stop();this.onError(error)}
  },2000);
 }
 stop(){
  this.version++;this.cancel(this.timer);this.timer=null;
  const node=this.node;this.node=null;
  if(node){node.onended=null;try{node.stop()}catch{}node.disconnect()}
 }
}
