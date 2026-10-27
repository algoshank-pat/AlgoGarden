"""Repeat the same no-write semantic workloads before/after a decision-engine change."""
import argparse,json,time,statistics,hashlib,os
from pathlib import Path
from unittest.mock import Mock
from receptionist import Receptionist,HERE,load_restaurant
from local_audit import capture

CASES=[('freeform_booking','Could you fit two of us in tomorrow at eight in the evening?',None),
       ('irrelevant_biography','Who is Albert Einstein?',None),
       ('irrelevant_during_booking','Write Python code for a stock trading robot','time'),
       ('spoken_time','Midnight','time'),
       ('ambiguous_answer','Um, I am not really sure what I meant','time'),
       ('booking_correction','Make it four guests tomorrow at quarter past eight','time')]

def main():
 p=argparse.ArgumentParser();p.add_argument('label');p.add_argument('--repeats',type=int,default=3);p.add_argument('--mode',choices=['legacy','unified']);args=p.parse_args()
 if args.mode:os.environ['RECEPTIONIST_DECISION_MODE']=args.mode
 report={'decision_mode':os.getenv('RECEPTIONIST_DECISION_MODE','unified'),'engine_sha256':hashlib.sha256(Path('decision_engine.py').read_bytes()).hexdigest(),'label':args.label,'test_type':'same_corpus_warm_model_no_audio_no_writes','runs':[]}
 r=load_restaurant(HERE/'restaurants/demo_restaurant.yaml')
 # Warm-up excluded from the measured samples.
 import ollama
 ollama.chat(model='gemma4:e2b-it-qat',messages=[{'role':'user','content':'Reply OK'}],options={'num_predict':2},keep_alive='30m')
 for repeat in range(args.repeats):
  for name,text,expected in CASES:
   bot=Receptionist(r)
   bot.tools.recognize_customer=Mock(return_value={'has_reservation':False,'returning':False})
   bot.tools.check_table=Mock(return_value={'available':False,'reason':'Outside booking hours.','alternatives':[]})
   if expected:
    bot.reserving=True;bot._awaiting_slot=expected
    bot.slots={'phone':'9900998765','name':'Alex','party_size':2,'date':'tomorrow'}
    from customer_identity import normalize_phone
    bot._recognized_phone=normalize_phone('9900998765','IN')
   events=[];started=time.perf_counter()
   with capture(events):reply,tools=bot.turn(text)
   elapsed=round((time.perf_counter()-started)*1000,2)
   llm=[e for e in events if e['name']=='ollama.request_prepared']
   report['runs'].append({'case':name,'repeat':repeat,'elapsed_ms':elapsed,'llm_calls':len(llm),'reply':reply,'tools':tools})
   print(name,elapsed,len(llm),reply,flush=True)
 report['median_ms']=round(statistics.median(r['elapsed_ms'] for r in report['runs']),2)
 report['mean_ms']=round(statistics.mean(r['elapsed_ms'] for r in report['runs']),2)
 output=HERE/'results/decision-engine';output.mkdir(parents=True,exist_ok=True)
 (output/(args.label+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')

if __name__=='__main__':main()
