"""Summarize actual voice stage timings; does not run models or record audio."""
import json
import math
import statistics
from pathlib import Path
from local_audit import read


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metrics',type=Path,default=Path(__file__).parent/'voice_metrics.jsonl')
    parser.add_argument('--synthetic-report',type=Path)
    args=parser.parse_args()
    kind='microphone_metrics'
    if args.synthetic_report:
        source=json.loads(args.synthetic_report.read_text(encoding='utf-8'))
        rows=[{**t,'endpoint_ms':source.get('endpoint_ms',480),
               'estimated_end_of_speech_to_audio_ms':t.get('estimated_end_of_speech_to_synthesized_audio_ms')} for t in source['turns']]
        kind='synthetic_audio_not_microphone'
    else:rows=read(args.metrics)
    report = {}
    for stage in ('endpoint_ms','stt_ms','api_ms','llm_ms','queue_wait_ms','tts_first_chunk_ms',
                  'estimated_end_of_speech_to_audio_ms'):
        values = sorted(row[stage] for row in rows if type(row.get(stage)) in (int,float))
        if values:
            report[stage] = {'samples':len(values),'p50':round(statistics.median(values)),
                             'p95':round(values[math.ceil(.95*len(values))-1])}
    print(json.dumps({'kind':kind,'turns':len(rows),'stages':report,
                      'note':('Synthetic first-audio timing measures generation availability, not playback or physical speaker onset; small samples do not establish a reliable p95.' if args.synthetic_report else 'First-audio timing estimates buffer submission, not physical speaker onset; small samples do not establish a reliable p95.')}, indent=2))


if __name__ == '__main__':
    main()
