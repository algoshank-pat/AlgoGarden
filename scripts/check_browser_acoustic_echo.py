"""Drive physical speaker input; observe actual browser playback via numeric logs."""
import json,pathlib,time,wave
import numpy as np
import sounddevice as sd
ROOT=pathlib.Path(__file__).resolve().parents[1];OUT=ROOT/'results/voice-turn-ownership-20261029';LOG=OUT/'private/turn_timings.jsonl'
def play(label):
 with wave.open(str(OUT/f'acoustic-{label}.wav')) as w:rate=w.getframerate();pcm=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2')
 sd.play(pcm,rate,device=3,blocking=True)
def calls():return [json.loads(x) for x in (OUT/'private/calls.jsonl').read_text().splitlines() if x.strip()]
initial=calls();sid=initial[-1]['session_id'];before=[r for r in initial if r['session_id']==sid]
offset=LOG.stat().st_size
print('Ready for the microphone to be unmuted; playing the question in six seconds.',flush=True)
time.sleep(6);play('hours');deadline=time.monotonic()+60;played=None
while time.monotonic()<deadline:
 with LOG.open('rb') as f:f.seek(offset);rows=[json.loads(x) for x in f.read().decode().splitlines() if x.strip()]
 played=next((r for r in rows if r['session_id']==sid and r['stage']=='browser.playback_started'),None)
 if played:break
 time.sleep(.025)
if not played:raise RuntimeError('No new native browser playing event recorded')
injected=time.time();play('pets')
evidence={'session_id':sid,'request_id':played['request_id'],'initial_turns':len(before),'native_playing_event':played,'background_injected_epoch':injected,'background_question':'Can I bring my dog?','physical_output_device':3,'test':'Laptop speaker -> actual browser microphone; background injection triggered by browser media playing telemetry'}
(OUT/'controlled-echo.json').write_text(json.dumps(evidence,indent=2));print(json.dumps(evidence),flush=True)
