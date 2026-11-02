"""Same-voice before/after speech measurements; no API or reservation writes."""
import hashlib, importlib.util, json, math, random, statistics, sys, time, wave
from collections import OrderedDict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
OUT=ROOT/'results/speech-cache-20261029'
from voice.adapters import PiperSpeaker
from voice.phrases import speech_segments, concise_speech, cacheable
from voice.speech_text import natural_speech
from freeze_customer100 import digest
spec=importlib.util.spec_from_file_location('voice._before', OUT/'adapters_before.py')
old_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(old_module)
CORPUS=[
 ('greeting','Thank you for calling Algo bites. How can I help you today?'),
 ('gate','Do you have a reservation with us?'),
 ('phone','What phone number can I use for your reservation?'),
 ('name','Please say and spell your first and last name. You can say "last name" between them.'),
 ('retry',"I didn't get that. Can you please repeat?"),
 ('more','Is there anything else I can help you with?'),
 ('survey','Thanks for calling. Before you go, would you feel comfortable taking a quick, optional survey?'),
 ('goodbye','Thank you so much for calling. Good night!'),
 ('readback','Table for 4 on 2027-02-17 at 20:30, under Test Guest, phone 2 0 2 5 5 5 0 1 7 3. Is that correct?'),
 ('correction','Change the reservation from 19:00 to 20:30 on 2027-02-17. Is that correct?'),
 ('alternatives','I can offer 2027-02-17 at 18:00 or 2027-02-18 at 19:00. Would either work?'),
 ('policy','All outside food is prohibited, including birthday cakes. Advance notice does not change this.'),
]
def old_segments(text):
 import re
 text=natural_speech(text);parts=[]
 while len(text)>110:
  boundaries=[m.end() for m in re.finditer(r'[,;.!?]\s+',text[:111]) if m.end()>=35]
  at=boundaries[-1] if boundaries else text.rfind(' ',0,110)
  if at<1:break
  parts.append(text[:at].strip());text=text[at:].strip()
 if text:parts.append(text)
 return parts
def stats(values):
 values=sorted(values)
 return {'n':len(values),'average':statistics.mean(values),'p50':statistics.median(values),'p95':values[math.ceil(.95*len(values))-1]}
def main():
 new=PiperSpeaker(ROOT/'models/piper/en_US-lessac-high.onnx')
 old=object.__new__(old_module.PiperSpeaker);old.voice=new.voice;old.synthesis_config=new.synthesis_config;old._cache=OrderedDict()
 list(new.chunks('Warm up the voice.'))
 rows=[]
 def run(engine,version,label,text,round,cached=False):
  if not cached:engine._cache.clear()
  parts=speech_segments(text) if version=='after' else old_segments(text)
  start=time.perf_counter();first=None;chunks=[]
  for part in parts:
   for chunk in engine.chunks(part):
    pcm=chunk.audio_int16_bytes
    if pcm and first is None:first=(time.perf_counter()-start)*1000
    chunks.append(pcm)
  total=(time.perf_counter()-start)*1000;audio=b''.join(chunks)
  assert first is not None and audio
  row={'version':version,'round':round,'label':label,'cache_hit_trial':cached,'input':text,'spoken':' '.join(parts),
       'first_chunk_ms':first,'synthesis_ms':total,'audio_s':len(audio)/2/new.voice.config.sample_rate}
  rows.append(row)
  if round==0 and not cached:
   with wave.open(str(OUT/f'{version}-{label}.wav'),'wb') as wav:
    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(new.voice.config.sample_rate);wav.writeframes(audio)
 for round in range(3):
  order=list(CORPUS);random.Random(829+round).shuffle(order)
  for label,text in order:
   for version,engine in ([('before',old),('after',new)] if round%2==0 else [('after',new),('before',old)]):run(engine,version,label,text,round)
  print('Completed round',round,flush=True)
 # Prime once, then measure three hits per approved phrase. Keep dynamics out.
 for label,text in CORPUS:
  if not cacheable(natural_speech(concise_speech(text))):continue
  list(new.chunks(text))
  for i in range(3):run(new,'after',label,text,i,True)
 report={'method':'Same loaded CPU Piper voice and unchanged synthesis settings, paired inputs, three warm rounds with alternating order. First chunk is PCM ready, not speaker onset. No API, LLM or STT work included. Cache-hit trials are separate; OS/background load uncontrolled.',
         'rows':rows,'summary':{},'baseline_after':digest(ROOT/'reservations.sqlite3')}
 for version in ['before','after']:
  rs=[r for r in rows if r['version']==version and not r['cache_hit_trial']]
  report['summary'][version]={k:stats([r[k] for r in rs]) for k in ['first_chunk_ms','synthesis_ms','audio_s']}
  report['summary'][version]['standard_audio_s']=stats([r['audio_s'] for r in rs if r['label'] in {x[0] for x in CORPUS[:8]}])
 rs=[r for r in rows if r['cache_hit_trial']]
 report['summary']['cache_hits']={k:stats([r[k] for r in rs]) for k in ['first_chunk_ms','synthesis_ms']}
 assert report['baseline_after']==json.loads((OUT/'baseline-before.json').read_text())
 for label,text in CORPUS[8:]:assert concise_speech(text)==text
 (OUT/'benchmark.json').write_text(json.dumps(report,indent=2))
 (OUT/'source-manifest.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'voice/adapters.py',ROOT/'voice/phrases.py',ROOT/'browser_voice.py',Path(__file__)]},indent=2))
 print(json.dumps(report['summary'],indent=2))
if __name__=='__main__':main()
