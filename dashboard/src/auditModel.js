export function timelineFor(turn){
 const events=turn.events||[],first=events.find(e=>Number.isFinite(Date.parse(e.at))),origin=first?Date.parse(first.at):0;
 const offset=e=>Number.isFinite(e.offset_ms)?e.offset_ms:Math.max(0,Date.parse(e.at)-origin)||0;
 const open=new Map(),rows=[];
 for(const [index,event] of events.entries()){
  const match=event.name.match(/^(.*)\.(started|completed|failed)$/);
  if(!match){if(!event.name.startsWith('ollama.'))rows.push({id:`event-${index}`,name:event.name,start:offset(event),duration:null,event,marker:true});continue;}
  const [,name,state]=match,key=event.details?.call_id||name;
  if(state==='started'){
   const row={id:`span-${index}`,callId:event.details?.call_id,name,start:offset(event),duration:null,parent:event.parent_call_id,event,incomplete:true};
   const stack=open.get(key)||[];stack.push(row);open.set(key,stack);rows.push(row);
  }else{
   const row=open.get(key)?.pop();
   if(row){row.duration=Number.isFinite(event.duration_ms)?event.duration_ms:Math.max(0,offset(event)-row.start);row.incomplete=false;row.failed=state==='failed';row.endEvent=event;}
   else rows.push({id:`end-${index}`,name,start:offset(event),duration:null,event,marker:true,failed:state==='failed'});
  }
 }
 return rows.sort((a,b)=>a.start-b.start);
}

export const pretty=value=>String(value||'').replaceAll('_',' ').replaceAll('.',' · ');
export const seconds=value=>{const duration=Number(value||0);return duration===0?'0 sec':duration<10?'<0.01 sec':`${(duration/1000).toLocaleString(undefined,{maximumFractionDigits:2})} sec`;};
