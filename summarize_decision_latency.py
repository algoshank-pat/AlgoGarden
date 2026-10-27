"""Final accepted-rule report. Compact experiments remain explicitly rejected."""
import ast,json,hashlib,runpy
from pathlib import Path
from benchmark_unified_latency import stats,canonical,baseline
from decision_engine import SCHEMA
ROOT=Path(__file__).parent;OUT=ROOT/'results/decision-latency'
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def instruction(path):
 tree=ast.parse(path.read_text(encoding='utf-8'))
 return next(ast.dump(n.value,include_attributes=False) for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='instruction' for t in n.targets))
def main():
 replay=read('safe-replay.json');voice=read('private-acceptance-latest.json');selection=read('safe-selection-rerun.json');audio=read('natural-audio-replay.json');compact=read('paired-final.json');rejected_voice=read('rejected-compact-voice.json')
 old=OUT/'decision_engine.before.py';assert runpy.run_path(str(old))['SCHEMA']==SCHEMA
 assert instruction(old)==instruction(ROOT/'decision_engine.py')
 cases={c['name']:c for c in read('corpus.json')};quality={}
 for variant in ['baseline','safe']:
  rows=[r for r in replay['runs'] if r['variant']==variant]
  quality[variant]={'exact_explicit_delta_correct':sum(r['correct'] for r in rows),'total':len(rows),'scope_correct':sum('relevance' not in r['errors'] for r in rows),'all_requested_fields_correct':sum(all(canonical(k,r['normalized']['fields'].get(k))==canonical(k,v) for k,v in cases[r['case']]['gold']['fields'].items()) for r in rows)}
 keys={(r['case'],r['repeat']) for r in replay['runs'] if r['variant']=='safe' and r['generation_calls']}
 shared=[r for r in compact['runs'] if r['variant']=='baseline' and (r['case'],r['repeat']) in keys]
 unchanged={k:stats([r[k] for r in shared]) for k in ['elapsed_ms','input_tokens','output_tokens','prompt_ms','generation_ms']}
 result={'accepted_change':'Original unified model/prompt/schema plus conservative no-inference rules. Compact prompt/schema changes rejected.',
  'method':replay['method'],'original_prompt_unchanged':True,'original_schema_unchanged':True,'before':replay['baseline'],'after':replay['safe'],
  'accuracy':quality,'new_failures_on_previously_passing_cases':replay['regressions'],'unchanged_generation_cohort':{'n':len(shared),'before_and_after_same_recorded_samples':unchanged},
  'reference_report':{'completed_conversations':56,'api_turns':288,'generations':60,'mean_ms':10727.842,'p50_ms':10702.294,'p95_ms':12311.219,'input_tokens_mean':439.7,'output_tokens_mean':107.2},
  'voice_acceptance':{'original_passed':sum(s['status']=='passed' for s in voice['scenarios']),'total':len(voice['scenarios']),'selection_exact_audio_rerun':selection['status'],'natural_date_business_checks':audio['original_business_checks'],'natural_date_audio_replay_passed':sum(r['passed'] for r in audio['audio']),'natural_date_audio_replay_total':len(audio['audio']),'remaining_failure':'Stored update-readback 12-receptionist.wav returns an empty Parakeet transcript. Not an all-green 15-group run.'},
  'rejected_experiments':{'compact_primary_exact':f"{compact['compact']['correct']}/{compact['compact']['total']}",'compact_voice_groups_passed':sum(s['status']=='passed' for s in rejected_voice['scenarios']),'compact_voice_groups_total':len(rejected_voice['scenarios']),'critical_rejection':'Next Saturday lost after merged No response; schema-valid null date. Smaller JSON did not preserve semantic extraction.','fuller_prompt_preflight':read('v2-preflight-results.json')['compact']},
  'production_baseline':baseline(),'production_baseline_unchanged':voice['production_baseline_unchanged'] and selection['production_baseline_unchanged'] and audio['production_baseline_unchanged'] and replay['baseline_unchanged'],
  'shared_api_restarted':False,'ux_changed':False,'one_call_budget_unchanged':True,
  'provenance':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in ['decision_engine.py','decision_fastpaths.py','receptionist.py','benchmark_safe_decisions.py']}}
 result['accepted_replay_mean_reduction_percent']=round(100*(1-result['after']['elapsed_ms']['average']/result['before']['elapsed_ms']['average']),2)
 (OUT/'comparison.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
 lines=['UNIFIED DECISION ENGINE LATENCY INVESTIGATION','',
 'ACCEPTED: preserve the original unified model, prompt, schema, options and one-call budget. Add conservative guest-count, date-only, explicit name, selection and initial lookup/update/cancel gates. Mixed-field input falls through to the unchanged model. No UX changes. Safe gates are the default for new processes; rollback RECEPTIONIST_DECISION_FASTPATHS=baseline. Shared API not restarted.','',
 'REFERENCE: requested customer100 latency report covers 56/100 conversations, 288 turns and 60 generations. Generation average/p50/p95 10.728 / 10.702 / 12.311s; prompt 4.621s + output 6.077s; 439.7 input / 107.2 output tokens on average. It is not a completed 100-customer run.','',
 'ACCEPTED BENCHMARK: 20 frozen inputs x 2 repetitions; identical caller, context and clock. Existing gates applied on both sides. Gate costs measured; unchanged model samples reused from paired-final.json to avoid duplicate-call prefix-cache bias. After numbers are REPLAY-DERIVED decision-stage estimates, not an independent live API or microphone benchmark.','',
 'Metric | Before | After']
 for metric in ['average','p50','p95']:
  lines.append(f"Decision stage {metric} seconds | {result['before']['elapsed_ms'][metric]/1000:.3f} | {result['after']['elapsed_ms'][metric]/1000:.3f}")
 lines+=[f"Generation calls | {result['before']['generation_calls']} | {result['after']['generation_calls']}",f"Input tokens total | {result['before']['total_input_tokens']} | {result['after']['total_input_tokens']}",f"Output tokens total | {result['before']['total_output_tokens']} | {result['after']['total_output_tokens']}",f"Replay mean reduction: {result['accepted_replay_mean_reduction_percent']}%. p95 is essentially unchanged: remaining freeform generations are still slow.",'',
 'TOKEN COUNTS PER GENERATION (average / p50 / p95; cohort changes when simple calls are removed):']
 for variant in ['before','after']:
  for k in ['input_tokens_per_generation','output_tokens_per_generation']:
   x=result[variant][k];lines.append(f"{variant} {k}: {x['average']} / {x['p50']} / {x['p95']}")
 lines+=['','MATCHED REMAINING GENERATIONS: same 30 recorded samples on both sides; no per-generation speedup is claimed.']
 for k,x in unchanged.items():lines.append(f"{k}: average {x['average']}, p50 {x['p50']}, p95 {x['p95']} (milliseconds for *_ms).")
 lines+=['','ACCURACY (not customer task success): '+json.dumps(quality),
  'Exact delta checks scope, route, all explicit fields, and absence of invented/copied fields. Requested-field check counts cases with every supplied gold field preserved. Zero previously passing replay cases become failures. Baseline failures often echo known slot values; existing remaining model errors are not fixed or hidden. Scope classification is unchanged on non-bypassed cases.',
  '',f"PRIVATE VOICE: {result['voice_acceptance']['original_passed']}/15 groups passed in the original-protocol run. Multiple-selection failure was fixed and the exact same saved caller WAV replayed through Parakeet, lookup, selected-only update/cancel, confirmation and cleanup: {selection['status']}. This is a targeted rerun, not an uninterrupted all-green run.",
  'Natural date booking/update database assertions all passed. The old audio assertion incorrectly required evening even for a legitimate afternoon alternative; it now requires the actual expected periods and still rejects blank output. Exact stored-audio replay: 5/6 passed; one 15.37s update-readback WAV still yields an empty ASR transcript. This remains failed. Physical microphone/speaker and echo cancellation were not tested.',
  '',f"REJECTED COMPACT: first paired microbenchmark was 40/40 exact, yet real private voice acceptance passed only {result['rejected_experiments']['compact_voice_groups_passed']}/15 groups. The merged booking + No lost next Saturday. Schema-required keys cannot prevent semantic loss represented as null. Fuller instructions also failed strict preflight (3/6 at that stage). No prompt/schema shortening is enabled. Raw reports and failed audio retained; additional optimization needs a separate correctness-preserving candidate.",
  '',f"BASELINE: {result['production_baseline']}; unchanged={result['production_baseline_unchanged']}.",
  'VERIFICATION: final-unit-tests.log; private-acceptance-latest.json; safe-selection-rerun.json; natural-audio-replay.json. LangSmith disabled in private acceptance. One-call budget, corrections, grounded answers and session isolation covered by backend contracts. Benchmark excludes ASR, TTS, VAD and playback.',
  'LIMITATIONS: curated small sample, replay-derived accepted timing, no production accuracy guarantee. Original prompt/schema equality is asserted against the pre-investigation snapshot. The rejected later experiments ran with other local model activity; do not compare their absolute wall times across different system loads.']
 (OUT/'comparison.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 print(json.dumps({'mean_reduction_percent':result['accepted_replay_mean_reduction_percent'],'unchanged_baseline':result['production_baseline_unchanged'],'report':str(OUT/'comparison.txt')}))
 assert result['production_baseline_unchanged'] and not replay['regressions']
if __name__=='__main__':main()
