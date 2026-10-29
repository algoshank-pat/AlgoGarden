import React,{useEffect,useRef,useState} from 'react';

// Optional, explicit microphone capture. Separate from business conversation input.
export default function FeedbackNote({session,enabled,onSaved}){
 const capture=useRef(null),[recording,setRecording]=useState(false),[error,setError]=useState(''),[saving,setSaving]=useState(false);
 function cleanup(){const c=capture.current;capture.current=null;if(!c)return;clearTimeout(c.timer);c.processor.disconnect();c.source.disconnect();c.gain.disconnect();c.stream.getTracks().forEach(t=>t.stop());c.context.close();}
 useEffect(()=>()=>cleanup(),[]);
 useEffect(()=>{if(!enabled){cleanup();setRecording(false)}},[enabled,session]);
 async function finish(){
  const c=capture.current;if(!c)return;cleanup();setRecording(false);setSaving(true);
  try{
   const samples=new Float32Array(c.chunks.reduce((n,x)=>n+x.length,0));let at=0;for(const x of c.chunks){samples.set(x,at);at+=x.length}
   const count=Math.min(480000,Math.floor(samples.length*16000/c.context.sampleRate)),buffer=new ArrayBuffer(44+count*2),v=new DataView(buffer);
   const str=(at,s)=>[...s].forEach((x,i)=>v.setUint8(at+i,x.charCodeAt(0)));
   str(0,'RIFF');v.setUint32(4,36+count*2,true);str(8,'WAVE');str(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,16000,true);v.setUint32(28,32000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);str(36,'data');v.setUint32(40,count*2,true);
   for(let i=0;i<count;i++)v.setInt16(44+i*2,Math.max(-1,Math.min(1,samples[Math.floor(i*c.context.sampleRate/16000)]))*32767,true);
   const r=await fetch(`/api/sessions/${session}/feedback/voice-notes/${crypto.randomUUID()}`,{method:'POST',headers:{'Content-Type':'audio/wav'},body:new Blob([buffer],{type:'audio/wav'})});
   if(!r.ok)throw Error('Voice note could not be saved. Please retry or type your feedback.');onSaved();
  }catch(e){setError(e.message)}finally{setSaving(false)}
 }
 async function start(){
  setError('');let stream,context;
  try{stream=await navigator.mediaDevices.getUserMedia({audio:true});context=new AudioContext();const source=context.createMediaStreamSource(stream),processor=context.createScriptProcessor(4096,1,1),gain=context.createGain();gain.gain.value=0;const c={stream,context,source,processor,gain,chunks:[]};capture.current=c;processor.onaudioprocess=e=>c.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));source.connect(processor);processor.connect(gain);gain.connect(context.destination);c.timer=setTimeout(finish,30000);setRecording(true)}catch(e){stream?.getTracks().forEach(t=>t.stop());context?.close();setError(e.message)}
 }
 if(!enabled)return null;
 return <div aria-label="Optional feedback voice note"><button type="button" disabled={saving} onClick={recording?finish:start}>{saving?'Saving voice note…':recording?'Save voice note':'Record optional voice note (30 seconds max)'}</button>{error&&<p role="alert">{error}</p>}</div>;
}
