// Separate from speech endpointing. Server capabilities fence stale callbacks.
export class ConversationWait {
 constructor({request=globalThis.fetch.bind(globalThis),schedule=(...args)=>globalThis.setTimeout(...args),cancel=id=>globalThis.clearTimeout(id),onReply=()=>{},onError=console.warn}={}){
  Object.assign(this,{request,schedule,cancel,onReply,onError});this.version=0;
 }
 clear(){
  this.version++;this.cancel(this.timer);this.timer=null;
  const current=this.current;this.current=null;
  if(current)this.request(`/api/sessions/${current.session}/waits/${current.token}/cancel`,{method:'POST'}).catch(e=>this.onError(e));
 }
 async arm(session,wait){
  this.clear();if(!wait)return;
  const version=this.version;this.current={session,token:wait.token};
  try{
   const response=await this.request(`/api/sessions/${session}/waits/${wait.token}/arm`,{method:'POST'});
   if(version!==this.version)return;
   if(!response.ok){if(response.status===409)return;throw Error('Could not start conversation wait.')}
   const {remaining_ms}=await response.json();if(version!==this.version)return;
   let fired=false;
   this.timer=this.schedule(async()=>{
    if(version!==this.version||fired)return;
    fired=true;
    try{
     const r=await this.request(`/api/sessions/${session}/turns`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:crypto.randomUUID(),text:'[Conversation wait elapsed]',input_kind:'timeout',wait_token:wait.token})});
     if(version!==this.version)return;
     this.current=null;this.timer=null;
     if(!r.ok){if(r.status===409)return;throw Error('Conversation wait follow-up failed.')}
     const data=await r.json();if(version===this.version)await this.onReply(data,session);
    }catch(e){if(version===this.version)this.onError(e)}
   },Math.max(0,remaining_ms));
  }catch(e){if(version===this.version)this.onError(e)}
 }
}
