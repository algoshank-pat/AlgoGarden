// getUserMedia itself is not abortable. Race startup, and dispose every late grant.
export async function openMicrophone({signal,timeoutMs=20000,mediaDevices=globalThis.navigator?.mediaDevices,createContext=()=>new AudioContext(),origin=globalThis.location?.origin||'this page',onStage=()=>{}}={}){
 let stream,context,active=true,timer,abort;
 const close=()=>{stream?.getTracks().forEach(t=>t.stop());if(context&&context.state!=='closed')Promise.resolve(context.close()).catch(()=>{})};
 const aborted=()=>Object.assign(new Error('Microphone startup cancelled.'),{name:'AbortError'});
 try{
  if(signal?.aborted)throw aborted();
  if(!mediaDevices?.getUserMedia)throw Error('Microphone access requires localhost or HTTPS in a browser with microphone support.');
  // Both calls start in the Start-call click's activation, before permission awaits.
  context=createContext();const resumed=Promise.resolve(context.resume());resumed.catch(()=>{});
  onStage('requesting');
  const granted=Promise.resolve(mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}})).then(s=>{
   if(!active){s.getTracks().forEach(t=>t.stop());throw aborted()}
   stream=s;onStage('granted');return s;
  });
  const interrupted=new Promise((_,reject)=>{
   abort=()=>reject(aborted());signal?.addEventListener('abort',abort,{once:true});
   timer=setTimeout(()=>reject(Object.assign(new Error(`Microphone permission or device startup is still pending for ${origin}. Check this tab’s microphone prompt and selected input device, then start again. End call cancels this attempt.`),{name:'MicrophoneTimeoutError'})),timeoutMs);
  });
  await Promise.race([Promise.all([granted,resumed]),interrupted]);
  if(signal?.aborted)throw aborted();
  if(context.state!=='running')throw Error('Audio could not start. Keep this tab visible and press Start call again.');
  onStage('ready');return {stream,context};
 }catch(error){
  close();
  if(error.name==='NotAllowedError'||error.name==='SecurityError')throw Error(`Microphone access was not granted for ${origin}. Allow it in this tab’s microphone prompt or site settings, check OS microphone access, then start again.`);
  if(error.name==='NotFoundError')throw Error('No microphone was found. Connect or select a microphone, then start again.');
  if(error.name==='NotReadableError')throw Error('The microphone could not open. Check the selected device and close other apps using it, then start again.');
  throw error;
 }finally{active=false;clearTimeout(timer);signal?.removeEventListener('abort',abort)}
}
