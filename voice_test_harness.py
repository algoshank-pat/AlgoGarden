"""One registry and runner for CLI and the existing UI polling API."""
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).parent
OUTPUT=ROOT/'results/voice-suite'
CASES=[
    ('New booking','test_browser_voice_flow.py','browser-voice/full-digit-phone-flow'),
    ('Update and cancellation','test_browser_voice_changes.py','browser-voice/update-cancel-digit-phone-flow'),
    ('Multiple reservations','test_browser_voice_multiple.py','browser-voice/multiple-reservations-voice-flow'),
    ('Hands-free, interruption, restart and 830 correction','test_handsfree_voice_flow.py','browser-voice/handsfree-interruption-flow'),
    ('Booking outside opening hours','test_voice_outside_hours.py','browser-voice/outside-hours-voice-flow'),
    ('Update outside opening hours','test_voice_outside_hours_update.py','browser-voice/outside-hours-update-voice-flow'),
    ('Decline an alternative','test_voice_decline_alternative.py','browser-voice/decline-alternative-voice-flow'),
    ('Decline then confirm another time','test_voice_decline_then_confirm.py','browser-voice/decline-then-confirm-time-voice-flow'),
    ('Fully booked baseline slot','test_voice_baseline_full_slot.py','browser-voice/baseline-full-slot-voice-flow'),
    ('Two customers competing for the last table','test_baseline_last_table_race.py','concurrency/baseline-last-table'),
    ('Natural spoken booking and update dates','test_voice_natural_dates.py','browser-voice/natural-speech-booking-update-flow'),
    ('Unclear responses, counter reset and staff handoff','test_voice_retry_reset.py','browser-voice/retry-reset'),
    ('Optional customer feedback','test_voice_customer_feedback.py','browser-voice/customer-feedback'),
    ('Pause detection and interruption guards',None,None),
    ('Routing, confirmation, restart and speech regressions',None,None),
]


def pending_scenarios():
    return [{'name':name,'status':'pending'} for name,_,_ in CASES]


@contextmanager
def suite_lock():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    with (OUTPUT/'runner.lock').open('a+b') as stream:
        stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:raise RuntimeError('Another CLI or UI voice suite is already running.') from error
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def baseline_digest():
    from receptionist import Tools,load_restaurant
    tools=Tools(load_restaurant(ROOT/'restaurants/demo_restaurant.yaml'))
    rows=[r for r in tools.booking_store().list(tools.business_id) if (r.get('request_key') or '').startswith('baseline:2026-q4:v1:')]
    return {'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()}


@contextmanager
def private_api(run_dir):
    import httpx
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'RECEPTIONIST_TURN_WORKERS':'2','PYTHONUNBUFFERED':'1','PYTHONIOENCODING':'utf-8',
         'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'2'}
    with (run_dir/'api.log').open('w',encoding='utf-8') as log:
        process=subprocess.Popen([sys.executable,'-u',str(ROOT/'voice_test_server.py'),str(port)],cwd=ROOT,
            stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,env=env,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            base=f'http://127.0.0.1:{port}'
            with httpx.Client(trust_env=False,timeout=2) as client:
                for _ in range(120):
                    if process.poll() is not None:raise RuntimeError('Private test API exited; see api.log.')
                    try:
                        if client.get(base+'/api/restaurant').is_success:break
                    except httpx.HTTPError:pass
                    time.sleep(.5)
                else:raise RuntimeError('Private test API startup timed out.')
            yield base
        finally:
            if process.stdin:process.stdin.close()
            try:process.wait(timeout=30)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)


def evaluate_report(report,exit_code):
    cleanup=report.get('cleanup')
    clean=(bool(cleanup) and all(r.get('cancelled') for r in cleanup)) if isinstance(cleanup,list) else bool(isinstance(cleanup,dict) and cleanup.get('cancelled'))
    if report.get('scenario') in {'retry_reset','customer_feedback'} and isinstance(cleanup,dict):
        clean=cleanup.get('no_reservations_created') is True and cleanup.get('session_closed') is True
    checks=report.get('checks',{})
    checks_pass=bool(checks) and all(value is True for value in checks.values())
    return {'status':'passed' if exit_code==0 and report.get('passed') and clean and checks_pass else 'failed',
            'cleanup_verified':clean,'checks':report.get('checks',{}),
            **({'error':report.get('error') or 'Scenario or test-booking cleanup failed; inspect its report.'}
               if exit_code!=0 or not report.get('passed') or not clean or not checks_pass else {})}


def cleanup_in_database(report):
    from receptionist import Tools,load_restaurant
    if report.get('scenario') in {'retry_reset','customer_feedback'}:
        tools=Tools(load_restaurant(ROOT/'restaurants/demo_restaurant.yaml'))
        actual=hashlib.sha256(json.dumps(tools.booking_store().list(tools.business_id),sort_keys=True).encode()).hexdigest()
        return actual==report.get('reservation_snapshot_before') and report.get('cleanup',{}).get('session_closed') is True
    cleanup=report.get('cleanup',[])
    items=cleanup if isinstance(cleanup,list) else [cleanup]
    ids={item.get('id') for item in items if isinstance(item,dict) and item.get('id')}
    if not ids:return False
    tools=Tools(load_restaurant(ROOT/'restaurants/demo_restaurant.yaml'))
    rows={r['id']:r for r in tools.booking_store().list(tools.business_id)}
    return all(key in rows and rows[key]['status']=='cancelled' for key in ids)


def execute_suite(update=lambda value:None):
    run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8]
    run_dir=OUTPUT/run_id;run_dir.mkdir(parents=True,exist_ok=True)
    summary={'run_id':run_id,'status':'running','test_type':'generated_speech_and_API_concurrency',
        'microphone_tested':False,'physical_echo_cancellation_tested':False,'api_workers':2,
        'scenarios':pending_scenarios(),'run_directory':str(run_dir),'passed':False}
    claimed=False
    def publish():
        encoded=json.dumps(summary,indent=2)
        (run_dir/'summary.json').write_text(encoded,encoding='utf-8')
        if claimed:
            temporary=OUTPUT/'latest.tmp'
            temporary.write_text(encoded,encoding='utf-8')
            temporary.replace(OUTPUT/'latest.json')
        update(json.loads(encoded))
    try:
        with suite_lock():
            claimed=True
            summary['baseline_before']=baseline_digest();publish()
            with private_api(run_dir) as base:
                for index,(name,script,folder) in enumerate(CASES):
                    item=summary['scenarios'][index];item['status']='running';publish();started=time.time()
                    try:
                        command=([sys.executable,'-u',str(ROOT/script)] if script else
                            ['node','--test','dashboard/src/voiceEndpoint.test.mjs'] if index==len(CASES)-2 else
                            [sys.executable,'-m','pytest','tests/test_chat_api.py','tests/test_routing.py',
                             'tests/test_returning_customer.py','tests/test_reservation_changes.py',
                             'tests/test_reservation_alternatives.py','tests/test_booking_db.py',
                             'tests/test_booking_dates.py','tests/test_booking_context.py','tests/test_in_person_cutoff.py','tests/test_reservation_schedule.py','tests/test_decision_engine.py','tests/test_decision_protocol.py','tests/test_customer_feedback.py','tests/test_input_decisions.py','tests/test_live_no_correction.py','tests/test_speech_text.py','-q'])
                        log_path=run_dir/f'{index+1:02d}.log'
                        with log_path.open('w',encoding='utf-8') as log:
                            completed=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                                env={**os.environ,'VOICE_TEST_API_URL':base,'PYTHONUNBUFFERED':'1','PYTHONIOENCODING':'utf-8'},
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        if script:
                            path=ROOT/'results'/folder/'report.json'
                            if not path.exists() or path.stat().st_mtime<started:raise RuntimeError('No fresh scenario report; inspect the run log.')
                            report=json.loads(path.read_text(encoding='utf-8'))
                            archived=run_dir/f'{index+1:02d}-report.json';archived.write_text(json.dumps(report,indent=2),encoding='utf-8')
                            item.update(evaluate_report(report,completed.returncode));item['report']=str(archived)
                            item['cleanup_verified']=cleanup_in_database(report)
                            if not item['cleanup_verified']:
                                item.update(status='failed',error='Test fixture cleanup could not be verified in the database.')
                            if script=='test_voice_natural_dates.py' and not report.get('audio_roundtrip_passed'):
                                item.update(status='failed',error='Actual reply-audio round-trip checks did not pass.')
                        else:
                            item.update(status='passed' if completed.returncode==0 else 'failed',cleanup_verified=True,
                                cleanup_required=False,checks={'automated_guards_passed':completed.returncode==0})
                        item['log']=str(log_path)
                    except Exception as error:item.update(status='failed',error=str(error))
                    item['duration_seconds']=round(time.time()-started,1);publish()
            summary['baseline_after']=baseline_digest()
            summary['baseline_unchanged']=summary['baseline_before']==summary['baseline_after']
            summary['passed']=all(r['status']=='passed' for r in summary['scenarios']) and summary['baseline_unchanged']
            summary['status']='passed' if summary['passed'] else 'failed'
    except Exception as error:
        summary.update(status='failed',error=str(error))
        for item in summary['scenarios']:
            if item['status'] in {'pending','running'}:item.update(status='failed',error='Suite could not complete: '+str(error))
    publish()
    return summary
