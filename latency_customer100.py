"""Analyse existing per-request timings; never run or replay customer operations."""
import json,sys,math,statistics,hashlib
from pathlib import Path
from collections import defaultdict
NAMES={'ollama.chat.completed':'Model decision','lookup_reservations_by_phone.completed':'MCP phone lookup (startup + lookup)','check_table.completed':'Availability check (validation + SQLite)','book_table.completed':'Booking save (validation + SQLite transaction)','change_reservation.completed':'Update/cancel (validation + SQLite transaction)','lookup_reservation.completed':'Reservation reference lookup','recognize_customer.completed':'Customer recognition (SQLite)','search_knowledge.completed':'Policy retrieval (embedding + Chroma)','knowledge.embed.completed':'Policy query embedding','check_dish.completed':'Menu dish lookup','get_menu.completed':'Menu lookup','get_specials.completed':'Specials lookup','get_hours.completed':'Hours lookup','get_location.completed':'Location lookup'}
def stats(values):
 v=sorted(values)
 return {'n':len(v),'mean_ms':round(statistics.mean(v),3) if v else None,'p50_ms':round(statistics.median(v),3) if v else None,'p95_ms':round(v[math.ceil(.95*len(v))-1],3) if v else None,'max_ms':round(max(v),3) if v else None}
def main():
 out=Path(sys.argv[1]);report=json.loads((out/'report.json').read_text(encoding='utf-8'))
 audit={}
 for line in (out/'calls.jsonl').read_text(encoding='utf-8').splitlines():
  a=json.loads(line);audit[(a['session_id'],a['request_id'])]=a
 conversations=[];stages=defaultdict(list);durations=[];by_flow=defaultdict(list)
 for c in report['results']:
  turns=[]
  for i,t in enumerate(c['turns'],1):
   response=t['response'];sid=response.get('structured',{}).get('session_id');request=response.get('request_id')
   a=audit.get((sid,request));spans=[];model=[]
   if a:
    for e in a['events']:
     if e['name'] in NAMES and isinstance(e.get('duration_ms'),(int,float)):
      spans.append({'step':NAMES[e['name']],'event':e['name'],'duration_ms':e['duration_ms'],'end_offset_ms':e.get('offset_ms'),'parent_call_id':e.get('parent_call_id')})
      stages[NAMES[e['name']]].append(e['duration_ms'])
     if e['name']=='ollama.response_received':
      obj=e['details']['output'];item={}
      for source,label in [('load_duration','model_load_ms'),('prompt_eval_duration','prompt_processing_ms'),('eval_duration','output_generation_ms'),('total_duration','model_reported_total_ms')]:
       if obj.get(source) is not None:item[label]=obj[source]/1e6;stages[label].append(item[label])
      item['model']=obj.get('model');item['input_tokens']=obj.get('prompt_eval_count');item['output_tokens']=obj.get('eval_count');model.append(item)
   longest=max(spans,key=lambda x:x['duration_ms']) if spans else None
   turn={'turn':i,'caller':t['caller'],'reply':response.get('reply'),'client_roundtrip_ms':t['elapsed_ms'],'api_service_ms':response.get('latency_ms'),'queue_wait_ms':response.get('queue_wait_ms'),'stage_spans':spans,'model_internal':model,'slowest_measured_step':longest,'audit_found':a is not None}
   turns.append(turn);durations.append(t['elapsed_ms']);by_flow[c['case']['kind']].append(t['elapsed_ms'])
  slowest=max(turns,key=lambda t:t['client_roundtrip_ms']);spans=[s for t in turns for s in t['stage_spans']]
  conversations.append({'case':c['case']['id'],'flow':c['case']['kind'],'channel':c['case']['channel'],'outcome':c['outcome'],'turn_count':len(turns),'api_response_latency':stats([t['client_roundtrip_ms'] for t in turns]),'total_active_response_ms':round(sum(t['client_roundtrip_ms'] for t in turns),3),'slowest_turn':slowest['turn'],'slowest_turn_caller':slowest['caller'],'slowest_turn_ms':slowest['client_roundtrip_ms'],'slowest_measured_step':max(spans,key=lambda s:s['duration_ms']) if spans else None,'stage_summary':{name:stats([s['duration_ms'] for s in spans if s['step']==name]) for name in dict.fromkeys(s['step'] for s in spans)},'turns':turns})
 api_times=[t['api_service_ms'] for c in conversations for t in c['turns'] if t['api_service_ms'] is not None]
 queue=[t['queue_wait_ms'] for c in conversations for t in c['turns'] if t['queue_wait_ms'] is not None]
 summary={'completed_conversations':len(conversations),'api_turns':len(durations),'response_latency':stats(durations),'api_service_latency':stats(api_times),'queue_wait':stats(queue),'mean_active_conversation_response_ms':round(statistics.mean(c['total_active_response_ms'] for c in conversations),3),'by_flow_response_latency':{k:stats(v) for k,v in by_flow.items()},'stage_latency':{k:stats(v) for k,v in stages.items()},'conversations':conversations,'timing_cautions':['API roundtrip is measured from sending text to receiving complete text reply; no network-to-shared-server or audio device stage is involved.','Conversation active latency is the sum of reply waits. It excludes human thinking/speaking and fixture setup.','Model internal timings are model-reported and nested inside model request duration; do not add them to the total.','Tool timings can nest: policy retrieval includes embedding; update can include reference lookup. Do not sum nested spans.','SQLite tool spans include validation, connection and transaction overhead; pure SQL execution is not separately instrumented.','MCP lookup includes subprocess startup, protocol initialization and DB lookup; these are not separately timed.','Slowest measured step excludes uninstrumented routing/serialization. It is not a claim that all CPU time is attributed.','ASR, VAD/endpointing, TTS, first-audio and end-of-speech-to-audio: NOT TESTED.']}
 (out/'latency.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
 lines=['CUSTOMER CONVERSATION LATENCY REPORT',f"Completed conversations: {len(conversations)}/100; API turns: {len(durations)}",'','All timings below are seconds unless otherwise stated.','Average response: %.3f; p50: %.3f; p95: %.3f' % tuple(summary['response_latency'][k]/1000 for k in ['mean_ms','p50_ms','p95_ms']),'Average total active reply waiting per conversation: %.3f' % (summary['mean_active_conversation_response_ms']/1000),'','MEASURED STEPS — AVERAGE / P50 / P95 / SAMPLE COUNT']
 for name,s in summary['stage_latency'].items():lines.append(f"{name}: {s['mean_ms']/1000:.3f} / {s['p50_ms']/1000:.3f} / {s['p95_ms']/1000:.3f}; n={s['n']}")
 lines+=['','PER CONVERSATION — AVERAGE REPLY / TOTAL REPLY WAIT / SLOWEST TURN / BOTTLENECK']
 for c in conversations:
  longest=c['slowest_measured_step'];label=f"{longest['step']} {longest['duration_ms']/1000:.3f}s" if longest else 'No timed model/tool span'
  lines.append(f"{c['case']:03d} {c['flow']} ({c['channel']} text): avg {c['api_response_latency']['mean_ms']/1000:.3f}s; total {c['total_active_response_ms']/1000:.3f}s; slowest turn #{c['slowest_turn']} {c['slowest_turn_ms']/1000:.3f}s; {label}")
  for t in c['turns']:
   label='; '.join(f"{s['step']} {s['duration_ms']/1000:.3f}s" for s in t['stage_spans']) or 'No timed model/tool span'
   lines.append(f"  Turn {t['turn']}: {t['caller']} | reply wait {t['client_roundtrip_ms']/1000:.3f}s | {label}")
 lines+=['','MEASUREMENT LIMITS']+['- '+s for s in summary['timing_cautions']]
 (out/'latency-report.txt').write_text('\n'.join(lines),encoding='utf-8')
 print(json.dumps({k:summary[k] for k in ['completed_conversations','api_turns','response_latency','mean_active_conversation_response_ms','stage_latency']},indent=2))
if __name__=='__main__':main()
