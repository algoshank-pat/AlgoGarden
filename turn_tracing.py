"""Correlated, text-free turn timings. All remote exports are best effort."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from threading import RLock
from time import perf_counter, monotonic
from uuid import UUID, uuid5, NAMESPACE_URL
import logging
import os

_INPUT={'customer.audio_capture','speech.endpoint_delay','browser.preview_wait','browser.stt_roundtrip','speech.to_text','browser.endpoint_detected','browser.recognition_request','browser.recognition_complete'}

def group_name(stage):
    if stage in _INPUT:return 'Input'
    if stage.startswith('speech.') or stage in {'browser.tts_to_first_playback','browser.reply_start','browser.speech_request','browser.first_audio_received','browser.playback_started'}:return 'Response'
    return 'Processing'

def group_for(run,name):
    if run is None:return None
    with _lock:
        entry=next((v for v in _runs.values() if v['run'] is run),None)
        if entry is None:return run
        if name not in entry['groups']:
            try:
                group=run.create_child(name,run_type='chain',inputs={},start_time=run.start_time,extra={'metadata':{'stage_group':name}})
                group.end(end_time=run.start_time,outputs={'measured':False})
                entry['groups'][name]=group
                obs.export_later(group.post)
            except Exception:
                logging.getLogger(__name__).warning('Trace group unavailable; continuing without grouping.')
                return run
        return entry['groups'][name]

def update_group(run,name,begin,end):
    group=group_for(run,name)
    if group is None:return
    with _lock:
        if not group.extra.get('metadata',{}).get('timing_measured',False):
            group.start_time=begin
            group.extra.setdefault('metadata',{})['timing_measured']=True
        else:group.start_time=min(group.start_time,begin)
        group.end_time=max(group.end_time or end,end)
        group.outputs={**(group.outputs or {}),'measured':True,'duration_kind':'envelope_not_sum'}
        obs.export_later(group.patch)

def workflow(bot):
    action=getattr(getattr(bot,'reservation_changes',None),'action',None)
    return {'cancel':'cancellation','update':'update','lookup':'lookup'}.get(action) or ('booking' if getattr(bot,'reserving',False) else None)

def label_turn(run,bot,text,result=None,prior=None):
    if run is None:return
    from reservation_changes import change_intent
    explicit={'cancel':'cancellation','update':'update','lookup':'lookup'}.get(change_intent(text))
    intent=(result or {}).get('routing') or {}
    intent=intent.get('intent')
    current=workflow(bot) or prior
    case=explicit or ({'reservation':current or 'booking','menu':'menu','policy':'policy','hours':'hours','location_parking':'location','human':'staff_assistance'}.get(intent)) or current or 'general'
    try:
        run.extra.setdefault('metadata',{}).update(use_case=case,workflow=current or explicit or case)
        run.tags=[tag for tag in (run.tags or []) if not tag.startswith('use_case:')]+['use_case:'+case]
        obs.export_later(run.patch)
        for group_name_value in ('Input','Processing','Response'):
            group=group_for(run,group_name_value)
            group.extra.setdefault('metadata',{}).update(use_case=case,workflow=current or explicit or case)
            obs.export_later(group.patch)
    except Exception:logging.getLogger(__name__).warning('Trace label unavailable.')
import local_audit as audit
import observability as obs

_lock=RLock()
_runs={}
MAX_RUNS=512

def identity(session_id,request_id):
    return str(UUID(str(session_id))),str(UUID(str(request_id)))

def root_for(session_id,request_id,channel):
    session_id,request_id=identity(session_id,request_id)
    key=(session_id,request_id)
    with _lock:
        for old in [k for k,v in _runs.items() if monotonic()-v['touched']>3600]:_runs.pop(old,None)
        if key in _runs:
            _runs[key]['touched']=monotonic()
            return _runs[key]['run']
        if not obs.enabled():return None
        try:
            run=obs.RunTree(id=uuid5(NAMESPACE_URL,':'.join(key)),name='customer.turn',run_type='chain',inputs={},
                **obs.destination_kwargs(),client=obs.client(),
                extra={'metadata':{'session_id':session_id,'thread_id':session_id,'request_id':request_id,'channel':channel,'payload_policy':'no_raw_text'}})
            obs.export_later(run.post)
            if len(_runs)>=MAX_RUNS:_runs.pop(next(iter(_runs)))
            _runs[key]={'run':run,'touched':monotonic(),'groups':{}}
            for group in ('Input','Processing','Response'):group_for(run,group)
            return run
        except Exception:
            logging.getLogger(__name__).warning('Turn tracing unavailable; continuing locally.')
            return None

def record(session_id,request_id,stage,duration_ms,run=None,started=None,status='completed'):
    row={'session_id':str(session_id),'request_id':str(request_id),'at':audit.now(),'stage':stage,
         'duration_ms':round(duration_ms,3),'status':status,'measurement':'server' if started else 'browser_reported'}
    saved=True
    try:audit.append(audit.DATA_ROOT/'turn_timings.jsonl',row)
    except (OSError,ValueError):
        saved=False
        logging.getLogger(__name__).warning('Timing record could not be saved.')
    if run is not None:
        try:
            end=started+timedelta(milliseconds=duration_ms) if started else datetime.now(timezone.utc)
            begin=started or end-timedelta(milliseconds=duration_ms)
            parent=group_for(run,group_name(stage))
            child=parent.create_child(stage,run_type='chain',inputs={},start_time=begin)
            child.end(end_time=end,outputs={'duration_ms':round(duration_ms,3),'measurement':row['measurement']},error='Stage failed' if status=='failed' else None)
            obs.export_later(child.post)
            update_group(run,group_name(stage),begin,end)
        except Exception:logging.getLogger(__name__).warning('Timing span unavailable.')
    return saved

@contextmanager
def stage(session_id,request_id,name,channel='voice'):
    run=root_for(session_id,request_id,channel);token=obs._parent.set(group_for(run,group_name(name)))
    start=perf_counter();at=datetime.now(timezone.utc);status='completed'
    try:yield run
    except BaseException:
        status='failed';raise
    finally:
        obs._parent.reset(token)
        record(session_id,request_id,name,(perf_counter()-start)*1000,run,at,status)

def set_payload(run,inputs=None,outputs=None):
    from trace_payloads import enabled,payload
    if run is not None and enabled():
        try:
            if inputs is not None:run.inputs=payload(inputs)
            if outputs is not None:run.outputs={**(run.outputs or {}),**payload(outputs)}
            run.extra.setdefault('metadata',{})['payload_policy']='developer_redacted'
            obs.export_later(lambda:run.patch(exclude_inputs=False))
        except Exception:logging.getLogger(__name__).warning('Trace payload unavailable.')

def finish(run,**outputs):
    if run is not None:
        try:run.end(outputs={**(run.outputs or {}),**outputs});obs.export_later(run.patch)
        except Exception:logging.getLogger(__name__).warning('Turn trace completion unavailable.')
