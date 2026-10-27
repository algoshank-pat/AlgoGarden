"""Compare resident CPU Parakeet and Nemotron servers on hash-checked saved audio."""
import argparse
import hashlib
import json
import random
import statistics
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from benchmark_asr import extract_phone


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='results/parakeet-benchmark')
    parser.add_argument('--repetitions', type=int, default=3)
    args = parser.parse_args()
    root = Path(__file__).parent
    baseline = json.loads((root/'results/asr-benchmark/report.json').read_text())
    output = root/args.output
    output.mkdir(parents=True, exist_ok=True)
    cases = baseline['cases']
    payloads = {}
    for case in cases:
        payload = (root/case['file']).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == case['sha256'], case['id']
        payloads[case['id']] = payload
    clients = {name: httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=120)
               for name, port in [('nemotron', 8021), ('parakeet', 8022)]}
    report = dict(started_at=datetime.now(ZoneInfo('America/Chicago')).isoformat(),
                  hardware=baseline['hardware'], cases=cases, repetitions=args.repetitions,
                  method='Same hash-verified endpointed PCM16 WAVs from prior benchmark. Both Q8_0 CPU engines use four threads and the same NeMo-Speech.cpp runtime and multipart HTTP API. Preloaded audio; sequential requests; seeded shuffled cases and rotated engine order. Loading and warmups excluded. Verbatim true, punctuation true, no phone prompts or ITN.',
                  limitations=baseline['limitations'], engines={}, warmups=[], runs=[])
    for name, client in clients.items():
        ready = client.get('/ready'); ready.raise_for_status()
        assert ready.json()['device'] == 'cpu'
        report['engines'][name] = {'ready': ready.json(), 'version': client.get('/version').json()}
    for name, revision, filename in [
        ('nemotron', baseline['nemotron_revision'], 'nemotron-speech-streaming-en-0.6b.q8_0.gguf'),
        ('parakeet', '541d1f99c6b0c3cd0b11a95167540bb8edefd82b', 'parakeet-tdt-0.6b-v3.q8_0.gguf')]:
        path = next((root/'models/nemotron').rglob(filename))
        report['engines'][name].update(revision=revision, precision='Q8_0', model_file=str(path.relative_to(root)),
                                       sha256=hashlib.file_digest(path.open('rb'), 'sha256').hexdigest())

    def save():
        (output/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    def transcribe(name, case):
        response = clients[name].post('/v1/audio/transcriptions',
            files={'file': ('recording.wav', payloads[case['id']], 'audio/wav')},
            data={'response_format': 'json', 'verbatim': 'true', 'automatic_punctuation': 'true'})
        response.raise_for_status()
        return response.json()['text']

    for name in clients:
        start = time.perf_counter()
        transcribe(name, cases[0])
        report['warmups'].append({'profile': name, 'ms': (time.perf_counter()-start)*1000})
    save()
    for repetition in range(args.repetitions):
        ordered = cases.copy()
        random.Random(4721+repetition).shuffle(ordered)
        for index, case in enumerate(ordered):
            names = list(clients)
            if (index+repetition) % 2: names.reverse()
            for name in names:
                start = time.perf_counter(); transcript = ''; error = None
                try: transcript = transcribe(name, case)
                except Exception as problem: error = f'{type(problem).__name__}: {problem}'
                ms = round((time.perf_counter()-start)*1000, 2)
                phone = extract_phone(transcript)
                row = dict(profile=name, case=case['id'], repetition=repetition+1, ms=ms,
                           transcript=transcript, phone=phone, expected_phone=case['expected_phone'],
                           exact_match=phone == case['expected_phone'] and error is None, error=error)
                report['runs'].append(row); save()
                print(json.dumps(row), flush=True)
    report['summary'] = {}
    for name in clients:
        rows = [r for r in report['runs'] if r['profile'] == name]
        phones = [r for r in rows if r['expected_phone']]
        controls = [r for r in rows if not r['expected_phone']]
        report['summary'][name] = dict(runs=len(rows), median_ms=statistics.median(r['ms'] for r in rows),
            phone_median_ms=statistics.median(r['ms'] for r in phones), phone_exact=sum(r['exact_match'] for r in phones),
            phone_runs=len(phones), controls_passed=sum(r['exact_match'] for r in controls), control_runs=len(controls),
            errors=sum(r['error'] is not None for r in rows),
            phone_clips_correct_every_repeat=sum(all(r['exact_match'] for r in phones if r['case'] == c['id']) for c in cases if c['expected_phone']))
    report['finished_at'] = datetime.now(ZoneInfo('America/Chicago')).isoformat(); save()
    lines = ['# Local Parakeet vs Nemotron benchmark', '', report['method'], '',
             '| Model | Median all clips | Median phone clips | Exact phone readings | Phone clips always correct | Controls | Errors |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for name, s in report['summary'].items():
        lines.append(f"| {name} | {s['median_ms']/1000:.3f} s | {s['phone_median_ms']/1000:.3f} s | {s['phone_exact']}/{s['phone_runs']} | {s['phone_clips_correct_every_repeat']}/6 | {s['controls_passed']}/{s['control_runs']} | {s['errors']} |")
    lines += ['', 'Eight saved clips, six containing phones, three repetitions each. Same literal digit-word/punctuation scorer as the earlier benchmark; no expected-answer repair.', '']
    lines += ['- '+item for item in report['limitations']]
    lines += ['', 'Full transcripts, hashes, settings and timings: [report.json](report.json). Production voice configuration is unchanged.']
    (output/'comparison.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    for client in clients.values(): client.close()
    print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__':
    main()
