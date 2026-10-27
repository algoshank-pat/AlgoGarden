import React,{useState} from 'react';

export default function OwnerFollowUp({call,onSaved}){
 const [text,setText]=useState(''),[requestId,setRequestId]=useState(crypto.randomUUID()),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 async function submit(resolve){
  if(busy||!text.trim())return;setBusy(true);setError('');setNotice('');
  try{
   const response=await fetch(`/api/owner/conversations/${call.id}/notes`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:requestId,text,resolve})});
   const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Could not save your note.');
   onSaved(data.note,resolve?'Resolved':null);setText('');setRequestId(crypto.randomUUID());setNotice(resolve?'Note saved. Conversation marked as resolved.':'Follow-up note saved.');
  }catch(e){setError(e.message)}finally{setBusy(false)}
 }
 async function status(value){
  if(busy)return;setBusy(true);setError('');setNotice('');
  try{
   const response=await fetch('/api/owner/reviews',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:'call:'+call.id,status:value})});
   if(!response.ok)throw Error('Could not update this conversation. Please try again.');
   onSaved(null,value);setNotice(value==='Resolved'?'Conversation marked as resolved.':'Conversation reopened for follow-up.');
  }catch(e){setError(e.message)}finally{setBusy(false)}
 }
 return <section className="owner-follow-up" aria-label="Owner follow-up"><div className="follow-up-heading"><h3>Follow-up notes</h3><span className={`follow-up-state ${call.review==='Resolved'?'resolved':''}`}>{call.review==='Resolved'?'Resolved':call.review==='Reviewed'?'Reviewed':'Not reviewed'}</span></div><p>Record what you did, what you agreed with the customer, or what still needs attention. These notes are for the owner; they are not sent to the customer.</p><div className="saved-owner-notes">{(call.follow_up_notes||[]).map(note=><article key={note.id}><div><strong>{note.author||'Owner'}</strong><time dateTime={note.at}>{new Date(note.at).toLocaleString()}</time></div><p>{note.text}</p></article>)}{!call.follow_up_notes?.length&&<p>No follow-up notes yet.</p>}</div><form onSubmit={e=>{e.preventDefault();submit(false)}}><label htmlFor="owner-follow-up-note">Add a note</label><textarea id="owner-follow-up-note" rows={3} maxLength={2000} value={text} disabled={busy} placeholder="For example: Called the customer and confirmed the new arrival time." onChange={e=>{setText(e.target.value);setRequestId(crypto.randomUUID());setNotice('')}}/><div className="follow-up-actions"><button className="secondary" type="submit" disabled={busy||!text.trim()}>Save note</button>{text.trim()?<button className="primary" type="button" disabled={busy} onClick={()=>submit(true)}>Save note & resolve</button>:<button className="primary" type="button" disabled={busy} onClick={()=>status(call.review==='Resolved'?'New':'Resolved')}>{call.review==='Resolved'?'Reopen conversation':'Mark as resolved'}</button>}</div></form>{busy&&<p role="status">Saving…</p>}{notice&&<p role="status" className="follow-up-success">{notice}</p>}{error&&<p role="alert" className="live-error">{error}</p>}</section>;
}
