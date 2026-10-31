import React,{useEffect,useRef,useState} from 'react';
import {Mic,MicOff,Phone,ArrowLeft} from 'lucide-react';
import './voice.css';
import {VoiceEndpoint,SpeechBargeIn} from './voiceEndpoint.mjs';

async function request(path,options={}) {
 const response=await fetch(path,options);
 if(!response.ok){let data;try{data=await response.json()}catch{}throw Error(typeof data?.detail==='string'?data.detail:'Voice service unavailable. Please check the local API.');}
 return response;
}
const json=(path,body)=>request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());

function wavBlob(chunks,rate){
 const size=chunks.reduce((n,c)=>n+c.length,0),samples=new Float32Array(size);let offset=0;
 for(const chunk of chunks){samples.set(chunk,offset);offset+=chunk.length;}
 const count=Math.floor(size*16000/rate),buffer=new ArrayBuffer(44+count*2),view=new DataView(buffer);
 const string=(at,s)=>{for(let i=0;i<s.length;i++)view.setUint8(at+i,s.charCodeAt(i));};
 string(0,'RIFF');view.setUint32(4,36+count*2,true);string(8,'WAVE');string(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);string(36,'data');view.setUint32(40,count*2,true);
 for(let i=0;i<count;i++){const at=i*rate/16000,left=Math.floor(at),fraction=at-left;const value=samples[left]*(1-fraction)+(samples[Math.min(left+1,size-1)]||0)*fraction;view.setInt16(44+i*2,Math.max(-1,Math.min(1,value))*32767,true);}
 return new Blob([buffer],{type:'audio/wav'});
}

export default function VoiceTest(){
 const [liveText,setLiveText]=useState('');
 const surveyRef=useRef(null);
 const previewVersion=useRef(0),previewJob=useRef(null),lastPreview=useRef(0);
 const [muted,setMuted]=useState(false);
 const mutedRef=useRef(false);
 function toggleMute(){const next=!mutedRef.current;mutedRef.current=next;setMuted(next);const current=capture.current;current?.stream.getAudioTracks().forEach(track=>{track.enabled=!next});current?.endpoint.reset();current?.bargeDetector.reset();queued.current=[];bargeMs.current=0;previewVersion.current++;setLiveText('');}
 const [status,setStatus]=useState('Ready'),[messages,setMessages]=useState([]),[error,setError]=useState(''),[session,setSession]=useState(null),[ended,setEnded]=useState(false),[audioUrl,setAudioUrl]=useState(''),[timing,setTiming]=useState(null);
 const audio=useRef(null),capture=useRef(null),generation=useRef(0),sessionRef=useRef(null),urlRef=useRef(''),bottom=useRef(null),busy=useRef(false),mode=useRef('off'),callEnded=useRef(false),bargeMs=useRef(0),queued=useRef([]);
 function stopCapture(){const current=capture.current;if(!current)return null;capture.current=null;clearTimeout(current.timer);current.processor.disconnect();current.source.disconnect();current.gain.disconnect();current.stream.getTracks().forEach(t=>t.stop());current.context.close();return current;}
 function cleanup(){previewVersion.current++;setLiveText('');queued.current=[];mode.current='off';generation.current++;stopCapture();audio.current?.pause();if(urlRef.current)URL.revokeObjectURL(urlRef.current);const id=sessionRef.current;sessionRef.current=null;if(id)fetch(`/api/sessions/${id}/stop`,{method:'POST',keepalive:true});}
 useEffect(()=>()=>cleanup(),[]);
 useEffect(()=>{bottom.current?.scrollIntoView({block:'nearest'})},[messages,status,liveText]);
 async function say(text,token){
  mode.current='processing';setStatus('Preparing speech');const response=await request('/api/voice/speak',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});const blob=await response.blob();
  if(token!==generation.current)return;
  if(urlRef.current)URL.revokeObjectURL(urlRef.current);urlRef.current=URL.createObjectURL(blob);setAudioUrl(urlRef.current);audio.current.src=urlRef.current;mode.current='speaking';setStatus('Speaking');
  try{await audio.current.play()}catch{setStatus('Playback paused');setError('Press Play below to hear the receptionist, then speak normally.');}
 }
 async function dial(){
  const context=capture.current?.context;if(!context)return;
  setStatus('Dialing');
  const sequence=[[697,1209],[770,1336],[852,1477],[941,1336]];
  sequence.forEach((pair,index)=>pair.forEach(frequency=>{
   const oscillator=context.createOscillator(),gain=context.createGain(),at=context.currentTime+index*.18;
   oscillator.frequency.value=frequency;gain.gain.setValueAtTime(.0001,at);gain.gain.exponentialRampToValueAtTime(.055,at+.01);gain.gain.exponentialRampToValueAtTime(.0001,at+.11);
   oscillator.connect(gain);gain.connect(context.destination);oscillator.start(at);oscillator.stop(at+.12);
  }));
  await new Promise(resolve=>setTimeout(resolve,850));
 }
 function previewSpeech(current){
  if(previewJob.current||mutedRef.current||mode.current!=='listening'||current.endpoint.voiced<600||current.endpoint.elapsed-lastPreview.current<1500)return;
  lastPreview.current=current.endpoint.elapsed;
  const version=previewVersion.current,token=generation.current;
  const blob=wavBlob(current.endpoint.chunks,current.context.sampleRate);
  previewJob.current=request('/api/voice/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav'},body:blob}).then(r=>r.json()).then(data=>{
   if(version===previewVersion.current&&token===generation.current&&!mutedRef.current&&mode.current==='listening'&&data.text)setLiveText(data.text);
  }).catch(()=>{}).finally(()=>{previewJob.current=null});
 }
 async function start(){
  if(busy.current)return;busy.current=true;cleanup();const token=generation.current;setMessages([]);setError('');setEnded(false);callEnded.current=false;setTiming(null);setStatus('Starting');setSession(null);setAudioUrl('');
  try{
   await record();if(token!==generation.current)return;
   await Promise.all([json('/api/voice/warmup',{}),dial()]);if(token!==generation.current)return;
   const data=await json('/api/sessions',{channel:'voice'});
   if(token!==generation.current){fetch(`/api/sessions/${data.session_id}`,{method:'DELETE'});return;}
   surveyRef.current=null;sessionRef.current=data.session_id;setSession(data.session_id);setMessages([{role:'assistant',text:data.greeting}]);await say(data.greeting,token);
  }catch(e){if(token===generation.current){setError(e.message);stopCapture();mode.current='off';setStatus('Ready');setEnded(true);}}finally{if(token===generation.current)busy.current=false;}
 }
 function listen(){
  if(!sessionRef.current||callEnded.current)return;
  capture.current?.endpoint.reset();capture.current?.bargeDetector.reset();lastPreview.current=0;mode.current='listening';bargeMs.current=0;setStatus('Listening — speak naturally');
 }
 async function record(){
  setStatus('Opening microphone');const token=generation.current;let stream,context;
  try{
   if(!navigator.mediaDevices?.getUserMedia)throw Error('Microphone access requires localhost or HTTPS.');
   stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
   stream.getAudioTracks().forEach(track=>{track.enabled=!mutedRef.current});
   if(token!==generation.current){stream.getTracks().forEach(t=>t.stop());return;}
   context=new AudioContext();await context.resume();
   if(token!==generation.current){stream.getTracks().forEach(t=>t.stop());context.close();return;}
   const source=context.createMediaStreamSource(stream),processor=context.createScriptProcessor(2048,1,1),gain=context.createGain();gain.gain.value=0;
   const current={stream,context,source,processor,gain,endpoint:new VoiceEndpoint(context.sampleRate),barge:[]};current.bargeDetector=new SpeechBargeIn(context.sampleRate,()=>{audio.current?.pause();listen();});capture.current=current;
   processor.onaudioprocess=e=>{
    if(capture.current!==current||callEnded.current||mutedRef.current||mode.current!=='listening')return;
    const samples=e.inputBuffer.getChannelData(0);
    const chunks=current.endpoint.feed(samples);
    if(!chunks)previewSpeech(current);
    if(chunks&&!busy.current){mode.current='processing';sendRecording(chunks,current.context.sampleRate);}
   };
   source.connect(processor);processor.connect(gain);gain.connect(context.destination);
  }catch(e){stream?.getTracks().forEach(t=>t.stop());context?.close();throw Error(e.name==='NotAllowedError'?'Allow microphone access, then start the call again.':e.message);}
 }
 async function sendRecording(chunks,rate){
  if(!capture.current||busy.current||mutedRef.current)return;previewVersion.current++;setLiveText('');lastPreview.current=0;busy.current=true;const token=generation.current,started=performance.now();setStatus('Transcribing');setError('');
  try{
   await previewJob.current;if(token!==generation.current||mutedRef.current)return;
   const blob=wavBlob(chunks,rate);
   const transcript=await request(`/api/voice/transcribe?session_id=${encodeURIComponent(sessionRef.current)}`,{method:'POST',headers:{'Content-Type':'audio/wav'},body:blob}).then(r=>r.json());if(token!==generation.current)return;
   if(surveyRef.current?.status==='awaiting_comment'&&transcript.input_kind!=='unrecognized_audio'&&!/^(skip|no|no thanks|no comment)[.!?]?$/i.test(transcript.text.trim()))await request(`/api/sessions/${sessionRef.current}/feedback/voice-notes/${crypto.randomUUID()}`,{method:'POST',headers:{'Content-Type':'audio/wav'},body:blob});
   const stt=performance.now()-started;setMessages(m=>[...m,{role:'user',text:transcript.text||'[Speech unclear — please repeat]'}]);setStatus('Receptionist is thinking');
   const data=await json(`/api/sessions/${sessionRef.current}/turns`,{request_id:crypto.randomUUID(),text:transcript.text||'[No intelligible speech detected]',input_kind:transcript.input_kind});if(token!==generation.current)return;
   surveyRef.current=data.survey;
   if(['requested','confirmed'].includes(data.restart_state)){queued.current=[];capture.current?.endpoint.reset();capture.current?.bargeDetector.reset();}
   const responseMs=performance.now()-started;setMessages(m=>[...m,{role:'assistant',text:data.reply,structured:data.structured}]);setEnded(data.ended);callEnded.current=data.ended;await say(data.reply,token);
   if(token===generation.current)setTiming({stt:Math.round(stt),api:Math.round(responseMs-stt),audio:Math.round(performance.now()-started)});
  }catch(e){if(token===generation.current){setError(e.message+' If a booking was being confirmed, check Reservations before starting again.');mode.current='off';stopCapture();setStatus('Error — start a new call');setEnded(true);}}finally{if(token===generation.current){busy.current=false;queued.current=[];}}
 }
 function end(){cleanup();busy.current=false;setSession(null);setEnded(true);setStatus('Call stopped');setAudioUrl('');setError('Conversation stopped. A request already processing may still complete. Check Reservations if you were confirming a change.');}
 return <div className="voice-page voice-customer"><header className="voice-header"><a href="/chat" className="voice-back"><ArrowLeft size={16}/>Back to restaurant</a><span className="voice-wordmark">Masala Mozzarella</span><nav className="voice-tabs" aria-label="Receptionist channels"><a href="/chat">Restaurant & menu</a><a href="/voice" aria-current="page">Voice agent</a></nav></header><div className="voice-layout"><aside className="voice-console"><span className={`voice-orb ${!muted&&(status.startsWith('Listening')||status==='Speaking')?'active':''}`}>{muted?<MicOff size={30}/>:<Mic size={30}/>}</span><h1>Let’s talk.</h1><div className="voice-call-status" role="status"><i/>{status}{muted?' · Mic muted':''}</div></aside><section className="voice-call-card" aria-label="Voice call transcript"><div className="voice-transcript-heading"><span className="voice-phone-icon"><Phone size={20}/></span><div><h2>Your conversation</h2><span className="transcript-legend"><b>Agent</b><b>You</b></span></div></div><div className="voice-transcript" role="log" aria-live="polite">{!messages.length&&<p>Start a call. We’ll take it from there.</p>}{messages.map((m,i)=><div key={i} className={`voice-utterance ${m.role}`}><small>{m.role==='user'?'YOU':'AGENT'}</small><p>{m.text}</p></div>)}{liveText&&!muted&&<div className="voice-utterance user live-transcript"><small>YOU · LIVE</small><p>{liveText}<span className="transcript-cursor">▎</span></p></div>}<div ref={bottom}/></div>{error&&<div className="voice-error" role="alert">{error}</div>}<div className="voice-controls"><audio ref={audio} controls hidden={!audioUrl} onEnded={()=>{if(callEnded.current){mode.current='off';stopCapture();setStatus('Call ended');}else listen()}} onPlay={()=>{mode.current='speaking';capture.current?.bargeDetector.reset();bargeMs.current=0;setStatus('Speaking')}} onPause={()=>{if(audio.current?.ended||mode.current!=='speaking')return;setStatus('Audio paused — press Play to continue')}}/><div className="voice-buttons"><button className="voice-action" disabled={!!session&&!ended||['Starting','Opening microphone','Dialing'].includes(status)} onClick={start}>Start call</button><button className={'voice-action mute-toggle'+(muted?' is-muted':'')} aria-label={muted?'Unmute microphone':'Mute microphone'} aria-pressed={muted} onClick={toggleMute}>{muted?<MicOff size={16}/>:<Mic size={16}/>} {muted?'Unmute':'Mute'}</button><button className="voice-action hangup" disabled={!session&&!['Starting','Opening microphone','Dialing'].includes(status)} onClick={end}>End call</button></div></div><div className="voice-disclaimer">Speak naturally · Headphones recommended</div></section></div></div>;
}
