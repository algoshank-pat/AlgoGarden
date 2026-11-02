import {conversationTimestamp} from './conversationTimestamp';
import React,{useEffect,useRef,useState} from 'react';
import {Mic,MicOff,Phone,ArrowLeft} from 'lucide-react';
import './voice.css';
import {VoiceEndpoint} from './voiceEndpoint.mjs';
import {SpeechPlayback,responseSilenceMs} from './voicePlayback.mjs';
import {VoiceTurnOwnership,submitTurnTimings} from './voiceTurnOwnership.mjs';
import {ConversationWait} from './conversationWait.mjs';
import {ThinkingCue} from './thinkingCue.mjs';
import {openMicrophone} from './microphoneCapture.mjs';
import {VoiceGreeting} from './voiceGreeting.mjs';

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
 const [starting,setStarting]=useState(false);
 const greeting=useRef(null),startupReady=useRef(false);
 if(!greeting.current)greeting.current=new VoiceGreeting();
 useEffect(()=>{greeting.current.prepare().catch(e=>console.warn('voice.greeting_prepare_failed',e.message))},[]);
 function toggleMute(){const next=!mutedRef.current;mutedRef.current=next;setMuted(next);ownership.current.setManualMuted(next);captureObservation('manual_mute');}
 const [status,setStatus]=useState('Ready'),[messages,setMessages]=useState([]),[error,setError]=useState(''),[session,setSession]=useState(null),[ended,setEnded]=useState(false),[audioUrl,setAudioUrl]=useState(''),[timing,setTiming]=useState(null);
 const speech=useRef(null),audio=useRef(null),capture=useRef(null),generation=useRef(0),sessionRef=useRef(null),urlRef=useRef(''),bottom=useRef(null),busy=useRef(false),mode=useRef('off'),callEnded=useRef(false),bargeMs=useRef(0),queued=useRef([]);
 const previewAbort=useRef(null),turnAbort=useRef(null),ownership=useRef(null);
 const startupAbort=useRef(null),thinkingCue=useRef(null),wait=useRef(null),pendingWait=useRef(null);
 if(!wait.current)wait.current=new ConversationWait({onReply:async(data,id)=>{if(id!==sessionRef.current||callEnded.current)return;const token=generation.current;pendingWait.current=null;await say(data.spoken_reply||data.reply,token,data.voice_input,{session_id:id,request_id:data.request_id},null,data.structured);},onError:e=>console.warn('conversation.wait_failed',e)});
 if(!thinkingCue.current)thinkingCue.current=new ThinkingCue();
 useEffect(()=>{thinkingCue.current.prepare().catch(e=>console.warn('voice.thinking_cue_prepare_failed',e.message))},[]);
 if(!ownership.current)ownership.current=new VoiceTurnOwnership({onReset:owner=>{previewVersion.current++;if(owner!=='processing'){previewAbort.current?.abort();previewAbort.current=null;}capture.current?.endpoint.reset();queued.current=[];lastPreview.current=0;setLiveText('')}});
 function takeTurn(owner){ownership.current.setOwner(owner)}
 function captureObservation(phase,requestId=null){
  const id=sessionRef.current;if(!id)return;
  const current=capture.current,track=current?.stream.getAudioTracks()[0];
  request(`/api/sessions/${id}/capture-observations`,{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:requestId,phase,track_enabled:track?.enabled===true,track_live:track?.readyState==='live',manually_muted:mutedRef.current,audio_context:current?.context.state||'none',audio_frames_received:(current?.frames||0)>0})}).catch(e=>console.warn('voice.capture_observation_failed',e.message));
 }
 function timingError(details){console.warn('voice.timing_submission_failed',JSON.stringify(details));if(details.session_id===sessionRef.current)setError('Performance timing could not be saved. The voice call can continue.');}
 function timings(id,requestId,metrics){return submitTurnTimings(id,requestId,metrics,{onError:timingError})}
 function stopCapture(){const current=capture.current;if(!current)return null;capture.current=null;clearTimeout(current.timer);current.processor.disconnect();current.source.disconnect();current.gain.disconnect();current.stream.getTracks().forEach(t=>t.stop());current.context.close();return current;}
 function cleanup(){startupReady.current=false;wait.current.clear();pendingWait.current=null;thinkingCue.current.stop();startupAbort.current?.abort();startupAbort.current=null;takeTurn('off');turnAbort.current?.controller.abort();turnAbort.current=null;mode.current='off';generation.current++;speech.current?.stop();speech.current=null;stopCapture();audio.current?.pause();if(urlRef.current)URL.revokeObjectURL(urlRef.current);const id=sessionRef.current;sessionRef.current=null;if(id)fetch(`/api/sessions/${id}/stop`,{method:'POST',keepalive:true}).catch(()=>{});}
 function finishCall(){
  // The server has ended the conversation, and its complete goodbye has played.
  // Release the whole call, not just capture; invalidate any late callbacks.
  callEnded.current=true;takeTurn('off');mode.current='off';stopCapture();captureObservation('ended');
  cleanup();busy.current=false;setSession(null);setEnded(true);setAudioUrl('');setStatus('Call ended');
 }
 useEffect(()=>()=>cleanup(),[]);
 useEffect(()=>{bottom.current?.scrollIntoView({block:'nearest'})},[messages,status,liveText]);
 async function say(text,token,inputHint=null,correlation={},origin=null,structured=null,preparedAudio=null){
  if(token!==generation.current)return;
  // Publish only at native playback start, never on API/fetch completion.
  // Keep a text fallback on audio failure so confirmed actions remain visible.
  let published=false;
  const publish=()=>{if(published||token!==generation.current)return;published=true;setMessages(m=>[...m,{at:new Date().toISOString(),role:'assistant',text,structured}])};
  thinkingCue.current.stop();
  takeTurn('agent');mode.current='preparing';setStatus('Preparing speech');
  captureObservation('agent',correlation.request_id||null);
  if(capture.current)capture.current.endpoint.silenceMs=responseSilenceMs(text,inputHint);
  if(!speech.current)speech.current=new SpeechPlayback(audio.current,{
   onSpeaking:url=>{if(token!==generation.current)return;setAudioUrl(url);mode.current='speaking';setStatus('Speaking')},
   onFinished:()=>{if(token!==generation.current)return;if(callEnded.current)finishCall();else resumeListening()},
  });
  speech.current.onError=e=>{if(token!==generation.current)return;publish();setError(e.message+' The reply is shown as text.');if(!callEnded.current)resumeListening();else finishCall()};
  speech.current.onRequest=event=>{if(origin&&token===generation.current)timings(correlation.session_id,correlation.request_id,{'browser.speech_request':event.at-origin.endOfSpeech})};
  speech.current.onFirstAudio=event=>{if(origin&&token===generation.current)timings(correlation.session_id,correlation.request_id,{'browser.first_audio_received':event.at-origin.endOfSpeech})};
  speech.current.onPlaybackStart=event=>{
   if(correlation.greeting&&token===generation.current)console.info('voice.greeting_playback',JSON.stringify({click_to_playing_ms:event.at-correlation.clickAt,cached:!!preparedAudio,event:'playing'}));
   publish();
   if(token===generation.current)captureObservation('playing',correlation.request_id||null);
   if(!origin||token!==generation.current)return;
   timings(correlation.session_id,correlation.request_id,{'browser.playback_started':event.at-origin.endOfSpeech,'browser.tts_to_first_playback':event.at-event.requestAt,'browser.reply_start':event.at-origin.endOfSpeech});
  };
  return await speech.current.start(text,correlation,preparedAudio);
 }
 function resumeListening(){
  if(!startupReady.current)return;
  listen();const next=pendingWait.current;pendingWait.current=null;if(next)wait.current.arm(sessionRef.current,next);
 }
 function previewSpeech(current){
  if(previewJob.current||mutedRef.current||mode.current!=='listening'||current.endpoint.voiced<600||current.endpoint.elapsed-lastPreview.current<1500)return;
  lastPreview.current=current.endpoint.elapsed;
  const version=previewVersion.current,token=generation.current;
  const blob=wavBlob(current.endpoint.chunks,current.context.sampleRate);
  const controller=new AbortController();previewAbort.current=controller;
  previewJob.current=request('/api/voice/transcribe',{method:'POST',signal:controller.signal,headers:{'Content-Type':'audio/wav'},body:blob}).then(r=>r.json()).then(data=>{
   if(version===previewVersion.current&&token===generation.current&&!mutedRef.current&&mode.current==='listening'&&data.text)setLiveText(data.text);
  }).catch(()=>{}).finally(()=>{previewJob.current=null;if(previewAbort.current===controller)previewAbort.current=null});
 }
 async function start(){
  if(busy.current)return;busy.current=true;cleanup();const token=generation.current,clickAt=performance.now();setMessages([]);setError('');setEnded(false);callEnded.current=false;setTiming(null);setStatus('Starting');setSession(null);setAudioUrl('');
  setStarting(true);startupReady.current=false;
  try{
   // Cached audio starts in the click gesture, independently of microphone
   // permission and API session setup. No dialing delay or LLM on this path.
   const cached=greeting.current.value;
   const welcome=cached?say(cached.text,token,null,{greeting:true,clickAt},null,null,cached.blob):null;
   // Give the media element its first playing event before AudioContext/device
   // setup can occupy the browser thread. Permission still runs during speech.
   if(welcome)await welcome;if(token!==generation.current)return;
   const sessionJob=json('/api/sessions',{channel:'voice'}).then(data=>{
    if(token!==generation.current){fetch(`/api/sessions/${data.session_id}/stop`,{method:'POST'}).catch(()=>{});return null;}
    surveyRef.current=null;sessionRef.current=data.session_id;setSession(data.session_id);return data;
   });
   const [,data]=await Promise.all([record(),sessionJob]);if(token!==generation.current||!data)return;
   startupReady.current=true;captureObservation('ready');
   if(welcome)await welcome;else {
    const prepared=await greeting.current.prepare().catch(()=>null);if(token!==generation.current)return;
    await say(data.spoken_greeting||data.greeting,token,null,{greeting:true,clickAt},null,null,prepared?.text===(data.spoken_greeting||data.greeting)?prepared.blob:null);
   }
   if(token!==generation.current)return;
   if(!speech.current?.active)resumeListening();
   // say() resolves on the real audio `playing` event. Prepare subsequent
   // turns in the background only after the customer can hear the greeting.
   if(token===generation.current&&!callEnded.current)json('/api/voice/warmup',{}).catch(e=>console.warn('voice.background_warmup_failed',e.message));
  }catch(e){if(token===generation.current){cleanup();busy.current=false;setStarting(false);setSession(null);setAudioUrl('');setError(e.message);setStatus('Ready');setEnded(true);}}finally{if(token===generation.current){busy.current=false;setStarting(false);}}
 }
 function listen(){
  if(!sessionRef.current||!capture.current||!startupReady.current||callEnded.current)return;
  takeTurn('customer');mode.current='listening';setStatus('Listening — speak naturally');captureObservation('listening');
 }
 async function record(){
  if(!speech.current?.active)setStatus('Opening microphone');const token=generation.current;let stream,context;
  try{
   if(!navigator.mediaDevices?.getUserMedia)throw Error('Microphone access requires localhost or HTTPS.');
   const controller=new AbortController();startupAbort.current=controller;
   console.info('voice.microphone_context',JSON.stringify({origin:location.origin,secureContext:globalThis.isSecureContext,visibility:document.visibilityState}));
   navigator.permissions?.query({name:'microphone'}).then(p=>console.info('voice.microphone_permission',p.state)).catch(()=>{});
   ({stream,context}=await openMicrophone({signal:controller.signal,onStage:stage=>console.info('voice.microphone_startup',stage)}));
   if(token!==generation.current){stream.getTracks().forEach(t=>t.stop());context.close();return;}
   ownership.current.attach(stream);
   if(token!==generation.current){stream.getTracks().forEach(t=>t.stop());context.close();return;}
   const source=context.createMediaStreamSource(stream),processor=context.createScriptProcessor(2048,1,1),gain=context.createGain();gain.gain.value=0;
   const current={stream,context,source,processor,gain,endpoint:new VoiceEndpoint(context.sampleRate,{silenceMs:responseSilenceMs()})};capture.current=current;
   thinkingCue.current.prepare(context).catch(e=>console.warn('voice.thinking_cue_prepare_failed',e.message));
   processor.onaudioprocess=e=>{
    current.frames=(current.frames||0)+1;
    if(capture.current!==current||callEnded.current||!ownership.current.canCapture)return;
    const samples=e.inputBuffer.getChannelData(0);
    if(mode.current!=='listening')return;
    const wasStarted=current.endpoint.started;
    const chunks=current.endpoint.feed(samples);
    if(!wasStarted&&current.endpoint.started)wait.current.clear();
    if(!chunks)previewSpeech(current);
    if(chunks&&!busy.current){const endpoint=current.endpoint.lastMeasurement,detectedAt=performance.now();mode.current='processing';sendRecording(chunks,current.context.sampleRate,endpoint,detectedAt);}
   };
   source.connect(processor);processor.connect(gain);gain.connect(context.destination);
  }catch(e){stream?.getTracks().forEach(t=>t.stop());context?.close();throw e;}
 }
 async function sendRecording(chunks,rate,endpoint,detectedAt=performance.now()){
  if(!capture.current||busy.current||mutedRef.current)return;wait.current.clear();takeTurn('processing');busy.current=true;const token=generation.current,started=performance.now(),requestId=crypto.randomUUID(),sessionId=sessionRef.current,endOfSpeech=detectedAt-(endpoint?.endpointMs||0),controller=new AbortController();turnAbort.current={controller,phase:'recognition'};setStatus('Transcribing');setError('');
  try{
   // Mute gates new microphone capture, not an utterance already submitted.
   // End call still aborts this request and invalidates its generation.
   await previewJob.current;if(token!==generation.current)return;
   const previewWait=performance.now()-started,sttStart=performance.now();
   const blob=wavBlob(chunks,rate);
   const transcript=await request(`/api/voice/transcribe?session_id=${encodeURIComponent(sessionId)}&request_id=${requestId}`,{method:'POST',signal:controller.signal,headers:{'Content-Type':'audio/wav'},body:blob}).then(r=>r.json());const sttComplete=performance.now();if(token!==generation.current||controller.signal.aborted)return;
   if(surveyRef.current?.status==='awaiting_comment'&&transcript.input_kind!=='unrecognized_audio'&&!/^(skip|no|no thanks|no comment)[.!?]?$/i.test(transcript.text.trim()))await request(`/api/sessions/${sessionRef.current}/feedback/voice-notes/${crypto.randomUUID()}`,{method:'POST',headers:{'Content-Type':'audio/wav'},body:blob});
   const stt=performance.now()-sttStart;setMessages(m=>[...m,{at:new Date().toISOString(),role:'user',text:transcript.text||'[Speech unclear — please repeat]'}]);setStatus('Receptionist is thinking');
   thinkingCue.current.start(capture.current?.context);
   turnAbort.current.phase='backend';const backendStart=performance.now();
   const data=await request(`/api/sessions/${sessionId}/turns`,{method:'POST',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:requestId,text:transcript.text||'[No intelligible speech detected]',input_kind:transcript.input_kind})}).then(r=>r.json());const backendComplete=performance.now();if(token!==generation.current)return;
   surveyRef.current=data.survey;
   if(['requested','confirmed'].includes(data.restart_state)){queued.current=[];capture.current?.endpoint.reset();}
   // Keep this call attached until all farewell audio finishes. In particular,
   // don't enable Start call while the final response is still speaking.
   const responseMs=performance.now()-started;callEnded.current=data.ended;
   const metrics={'browser.preview_wait':previewWait,'browser.stt_roundtrip':sttComplete-sttStart,'browser.backend_roundtrip':backendComplete-backendStart,'browser.endpoint_detected':detectedAt-endOfSpeech,'browser.recognition_request':sttStart-endOfSpeech,'browser.recognition_complete':sttComplete-endOfSpeech,'browser.backend_request':backendStart-endOfSpeech,'browser.backend_reply':backendComplete-endOfSpeech};
   if(endpoint){metrics['customer.audio_capture']=endpoint.captureMs;metrics['speech.endpoint_delay']=endpoint.endpointMs;}
   timings(sessionId,requestId,metrics);
   pendingWait.current=data.conversation_wait;
   await say(data.spoken_reply||data.reply,token,data.voice_input,{session_id:sessionId,request_id:requestId},{endOfSpeech},data.structured);
   if(token===generation.current)setTiming({stt:Math.round(stt),api:Math.round(responseMs-stt),audio:Math.round(performance.now()-started)});
  }catch(e){if(token===generation.current&&e.name!=='AbortError'){setError(e.message+' If a booking was being confirmed, check Reservations before starting again.');takeTurn('off');mode.current='off';stopCapture();setStatus('Error — start a new call');setEnded(true);}}finally{if(token===generation.current){thinkingCue.current.stop();busy.current=false;turnAbort.current=null;if(mode.current==='processing')listen();}}
 }
 function end(){cleanup();setStarting(false);busy.current=false;setSession(null);setEnded(true);setStatus('Call stopped');setAudioUrl('');setError('Conversation stopped. A request already processing may still complete. Check Reservations if you were confirming a change.');}
 return <div className="voice-page voice-customer"><header className="voice-header"><a href="/chat" className="voice-back"><ArrowLeft size={16}/>Back to restaurant</a><span className="voice-wordmark">Saffron & Basil</span><nav className="voice-tabs" aria-label="Receptionist channels"><a href="/chat">Restaurant & menu</a><a href="/voice" aria-current="page">Voice agent</a></nav></header><div className="voice-layout"><aside className="voice-console"><span className={`voice-orb ${!muted&&(status.startsWith('Listening')||status==='Speaking')?'active':''}`}>{muted?<MicOff size={30}/>:<Mic size={30}/>}</span><h1>Let’s talk.</h1><div className="voice-call-status" role="status" data-turn-owner={ownership.current.owner} data-manual-muted={muted} data-input-enabled={capture.current?.stream.getAudioTracks()[0]?.enabled??false}><i/>{status}{muted?' · Mic muted':''}</div></aside><section className="voice-call-card" aria-label="Voice call transcript"><div className="voice-transcript-heading"><span className="voice-phone-icon"><Phone size={20}/></span><div><h2>Your conversation</h2><span className="transcript-legend"><b>Algo Garden Agent</b><b>You</b></span></div></div><div className="voice-transcript" role="log" aria-live="polite">{!messages.length&&<p>Start a call. We’ll take it from there.</p>}{messages.map((m,i)=><div key={i} className={`voice-utterance ${m.role}`}><div className="conversation-message-meta"><small>{m.role==='user'?'YOU':'Algo Garden Agent'}</small><time dateTime={m.at} title="America/Chicago">{conversationTimestamp(m.at)}</time></div><p>{m.text}</p></div>)}{liveText&&!muted&&<div className="voice-utterance user live-transcript"><small>YOU · LIVE</small><p>{liveText}<span className="transcript-cursor">▎</span></p></div>}<div ref={bottom}/></div>{error&&<div className="voice-error" role="alert">{error}</div>}<div className="voice-controls"><audio ref={audio} controls hidden={!audioUrl} onEnded={()=>{if(speech.current?.active)speech.current.ended();else if(sessionRef.current&&!callEnded.current)listen()}} onPlay={()=>{if(!sessionRef.current)return;takeTurn('agent');mode.current='speaking';setStatus('Speaking')}} onPause={()=>{if(audio.current?.ended||mode.current!=='speaking')return;setStatus('Audio paused — press Play to continue')}}/><div className="voice-buttons"><button className="voice-action" disabled={starting||!!session&&!ended} onClick={start}>Start call</button><button className={'voice-action mute-toggle'+(muted?' is-muted':'')} aria-label={muted?'Unmute microphone':'Mute microphone'} aria-pressed={muted} onClick={toggleMute}>{muted?<MicOff size={16}/>:<Mic size={16}/>} {muted?'Unmute':'Mute'}</button><button className="voice-action hangup" disabled={!session&&!starting} onClick={end}>End call</button></div></div><div className="voice-disclaimer">Speak naturally · Headphones recommended</div></section></div></div>;
}
