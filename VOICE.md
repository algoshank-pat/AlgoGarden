# Local microphone/speaker agent

This voice client uses the same Python API/router, SQLite reservations, returning-customer recognition, admission queue and audit logs as chat. It is a local terminal microphone/speaker loop, not a browser voice button or a telephone connection.

## Setup

```powershell
cd C:\Maven\Projects\Week4\restaurant-receptionist
uv sync --extra voice
uv run --extra voice python prepare_voice.py
```

The default is Parakeet TDT 0.6B v3 Q8_0 on CPU using NeMo-Speech.cpp 0.2.0. Runtime, Parakeet weights, optional Whisper fallback and Piper are already downloaded. The client verifies the pinned model checksum, starts a private loopback server on an available port and stops it on exit. Silero's ONNX model is bundled in its package. Models are downloaded once from their upstream repositories; inference is local. Voice dependencies are optional so text-only installations can omit them. PyAV is pinned below 19 because the installed faster-whisper decoder uses an argument removed in PyAV 19.

## Start

Start the shared API in one PowerShell terminal (Ollama must be running):

```powershell
cd C:\Maven\Projects\Week4\restaurant-receptionist
uv run --extra voice uvicorn chat_api:app --host 127.0.0.1 --port 8010
```

Start the voice client in a second terminal:

```powershell
cd C:\Maven\Projects\Week4\restaurant-receptionist
uv run --extra voice python voice_agent.py
```

The greeting plays first, then “Listening…” appears. Speak a short question or reservation request and wait for the reply. Say “goodbye” or press Ctrl+C to end. The microphone is opened only during listening and closed before transcription/reply playback. Use headphones for your first test. There is no barge-in: speech while the assistant is speaking is not recorded.

To select devices:

```powershell
uv run --extra voice python voice_agent.py --list-devices
uv run --extra voice python voice_agent.py --input-device 1 --output-device 3
```

The detected defaults during verification were Intel microphone array (1) and Realtek speaker (3), with 16 kHz mono input and 22.05 kHz mono output supported. Device IDs can change. Windows microphone access must be enabled for desktop applications. `--list-devices` only enumerates devices; launching the main client records microphone audio for each turn.

To see voice conversations, start React with `npm run dev` in `dashboard` and open the owner dashboard at http://127.0.0.1:5173/. Voice sessions are labeled separately from chat. Transcripts and tool events persist in the same calls.jsonl. Voice stages are measured locally in voice_metrics.jsonl, linked by session/request IDs. Audio recordings are not saved by default. The logged transcript is what STT heard, so confirm names/phone/date/time carefully. Returning-customer recognition begins after a phone number is supplied, not from voice identity.

## Behavior

- Silero ONNX detects speech in 512-sample/32-ms frames, with pre-roll and about 480 ms ending silence.
- Parakeet v3 transcribes complete utterances locally using CPU Q8_0; `--threads` controls CPU threads. This remains half-duplex batch transcription.
- Incomplete explicitly labelled phone fields request a repeat. Readback and explicit confirmation remain required.
- Use `--stt-backend whisper` to select the previous Whisper pipeline explicitly; there is no automatic fallback.
- Piper synthesizes sentence chunks and plays each before synthesizing the next.
- Four consecutive failed-understanding attempts (including silent/unintelligible audio) invoke staff handoff through the API; logs label these as audio-status events, not invented caller speech.
- Commands longer than 20 seconds or audio with missing frames are not submitted as partial bookings. Idle listening expires after 15 seconds.
- Handoff means a spoken staff contact number; no actual phone transfer occurs.
- API retries keep the same message ID. If the outcome remains uncertain, the client stops rather than collecting another command that could duplicate a booking. A server turn may finish after the client stops; check the dashboard or staff before retrying via a new session.

## Verification and timings

```powershell
uv run --extra voice python voice_self_test.py
uv run --extra voice python voice_report.py
uv run --extra voice pytest tests -q
```

The self-test synthesizes a temporary WAV and checks real VAD/STT; it does not use the microphone, play sound, or book a table. The verified phrase “What are your opening hours?” was transcribed exactly, with 40 speech-positive VAD frames. Synthetic WAV generation took about 523 ms and transcription about 3,912 ms on this machine. These are one-sample smoke timings, not representative p50/p95 or a measured full voice conversation. The under-three-second target is not demonstrated. `voice_report.py` reports p50/p95 of recorded stage timings and sample counts after you run real conversations. Estimated end-of-speech to first audio measures submission to the output stream, not the physical speaker onset.

Unit/API tests cover endpointing, overlong-audio rejection, four failed-understanding handoff, retry IDs, chunked playback order and channel logging. The actual microphone-to-speaker experience still needs your spoken test. Noisy/recorded-caller WER and entity accuracy evaluation remain a later step; the self-test is not a substitute for those evaluations.

Existing reservations can be checked, updated or cancelled through phone-based MCP lookup, shared with chat. Speak the phone number, select a reservation if multiple matches are listed, review the spoken details, and explicitly confirm any mutation. A reference is optional. Phone matching does not verify ownership. See the parent README for concurrency and retained-history behavior.

## Synthetic end-to-end acceptance run (2026-10-26)

`voice_flow_test.py` runs real Piper-generated caller audio through Silero endpointing, Whisper small, the live voice-channel API/Ollama engine, and synthesized Piper responses. It records reports and WAVs under the selected results directory. It does not record a microphone or play speakers. Caller and receptionist use the same Piper voice, so this is a clean synthetic-audio test, not an accent/noise benchmark.

The first attempt (`results/voice-flow/report.json`) successfully booked, but Whisper dropped one phone digit from a long reference-plus-phone utterance. Contact matching rejected it and staff handoff followed. That failed result is retained; its test reservation was cancelled through a typed API cleanup, not counted as a successful spoken cancellation.

The second attempt (`results/voice-flow-separated/report.json`) passed ten spoken turns with reference and phone in separate utterances: book two guests for 2026-12-02 at 20:00, update to three at 21:00, then cancel. Assertions verified no mutation before each explicit yes, the same booking ID and phone source after updating, version increments, created/updated/cancelled events, and removal from active capacity after cancellation. Both synthetic reservations ended cancelled. All ten response WAVs contain audio.

To repeat (creates and then cancels one clearly named synthetic reservation; inspect the report and clean up if a run fails):

```powershell
uv run --extra voice python voice_flow_test.py --output results/voice-flow-new-run
```

The script's December 2, 2026 test date must be moved forward when that date becomes past. Reports are separate from `voice_metrics.jsonl`, so synthetic runs do not become real microphone latency samples. Physical microphone/speaker testing, different human voices, background noise, interruption, reference comprehension and reliable latency percentiles remain unverified. Long spoken phone numbers remain a demonstrated transcription risk; do not relax reference/contact matching or confirmation checks.

## Phone-digit recognition improvement (2026-10-26)

Whisper now uses beam size five with a generic digit-preservation hint, without supplying example customer numbers. Phone-containing turns replay a phone-focused audio crop using word timestamps and a digit-word decoder. Code converts only literal spoken digits (including oh/double/triple); it never fills gaps from stored contacts. The crop uses the phone label because timestamps on long numeric tokens can collapse. A second boundary is tried if the first cannot yield a complete number.

Full ten-digit Indian contact numbers are required in this voice pass; other explicit country codes use structural phone validation. Use `voice_agent.py --phone-region IN` to match this restaurant. If two complete decodings disagree, or no complete number is available, the client requests a slow digit-by-digit repeat. It does not submit an uncertain phone string as customer text. Three consecutive unrecognized attempts request a repeat; the fourth invokes staff handoff. A valid original transcription is retained if the focused decoder cannot interpret the clip.

Run `uv run --extra voice python voice_phone_eval.py` for the fixed eight-clip synthetic comparison and inspect `results/voice-phone-eval/report.json`. The report separates exact matches, requested clarifications and unsafe extractions. This is a development regression set, not a held-out human speech benchmark. Phone-containing turns require extra decoding time; noisy audio and arbitrary accents can still require clarification.

A long combined-contact booking/update/cancellation run with the wider decoding setting passed in `results/voice-flow-phone-fix/report.json`. A later focused-decoder run retained the phone correctly but misheard reference letters (`results/voice-flow-phone-final/report.json`); that failure is preserved, and the synthetic booking was cancelled using explicit typed API cleanup. Reference-letter recognition remains a separate issue. Restart the microphone client to load these adapter changes; restarting the API is unnecessary.

## CPU Nemotron vs Whisper benchmark (2026-10-26)

See [the measured comparison](results/asr-benchmark/comparison.md) and [raw report](results/asr-benchmark/report.json). Eight fixed saved synthetic clips (six phone clips, two controls), three repetitions per engine, four CPU threads, model loading and warmup excluded. All models receive identical endpointed 16 kHz PCM16 inputs. The report includes input/model hashes and the runtime build version. Nemotron includes loopback HTTP overhead; Whisper is in-process. This measures offline transcription, not live streaming or full restaurant-agent response latency.

Median transcription across all clips: original Whisper small beam-one 1.76 seconds with 9/18 exact phone readings; current phone-safe Whisper 4.13 seconds with 18/18; Nemotron Speech Streaming English 0.6B Q8_0 via NeMo-Speech.cpp v0.2.0 0.83 seconds with 18/18. All three passed the six non-phone control runs. These are small clean development recordings from a single Piper voice, not a human/noise/accent generalization claim. The benchmark does not change the production voice backend.

The portable runtime is stored under `tools/nemo-speech-0.2.0/`; its official release archive was checked against the published SHA-256. Weights are stored under `models/nemotron/` and pinned to model revision `ebe59e5a817142986528bbbee5dba8db7b38ed50`. The temporary server was stopped after the benchmark. To repeat, start the CPU server in one PowerShell terminal:

```powershell
$env:NEMO_SPEECH_MODEL_DIR = Join-Path $PWD 'models/nemotron'
& '.\tools\nemo-speech-0.2.0\runtime\nemo-speech-0.2.0-windows-x86_64-cpu\bin\nemo-speech.exe' serve --asr-model nemotron-en --device cpu --asr.backend.threads 4 --threads 1 --host 127.0.0.1 --port 8021 --no-ui
```

Then run in another terminal:

```powershell
uv run --extra voice python benchmark_asr.py
```

Stop the benchmark server with Ctrl+C afterward. No reservation API calls or booking writes are performed by the benchmark.

## CPU Parakeet vs Nemotron benchmark (2026-10-26)

See [comparison](results/parakeet-benchmark/comparison.md) and [raw transcripts and timings](results/parakeet-benchmark/report.json). A fresh comparison used the exact same eight hash-verified endpointed recordings, three repetitions per model, Q8_0 weights, four CPU threads, and NeMo-Speech.cpp 0.2.0 for both engines. Requests run sequentially with shuffled cases and alternating engine order; model loading and warmup are excluded. Both timings include loopback HTTP overhead.

Parakeet TDT 0.6B v3 median transcription was 0.845 seconds versus Nemotron English 0.6B at 0.959 seconds (about 12% lower). Both matched all 18 phone readings, all six distinct phone clips on every repeat, and all six non-phone control runs. No errors occurred. These clean synthetic development clips do not establish accuracy on human voices, noise or accents, and this measures whole-utterance batch inference rather than streaming or full voice response latency. The application still uses its existing Whisper backend.

Parakeet weights are pinned to revision `541d1f99c6b0c3cd0b11a95167540bb8edefd82b`, SHA-256 `e3880d0aaaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e`. To reproduce, run the earlier Nemotron server on port 8021, and a second server with the same options except `--asr-model parakeet-tdt --port 8022`. Run `.venv\Scripts\python.exe benchmark_parakeet.py --output results/parakeet-benchmark-new`, then stop both servers. No microphone capture or reservation writes occur.

Fresh Windows setup also requires the official NeMo-Speech.cpp v0.2.0 CPU release extracted under `tools/nemo-speech-0.2.0/runtime/nemo-speech-0.2.0-windows-x86_64-cpu/`. Set `NEMO_SPEECH_MODEL_DIR` to `models/nemotron` and run its `bin/nemo-speech.exe pull parakeet-tdt` to obtain the pinned weights. `prepare_voice.py` prepares Whisper/Piper assets only. Missing Parakeet files fail before session creation or microphone capture.

## Parakeet full-flow acceptance (2026-10-26)

Default microphone client and synthetic flow now use Parakeet. `results/voice-flow-parakeet-verified/report.json` passed eight Piper-generated caller turns through Silero, the actual Parakeet adapter, live API/Ollama, SQLite, and synthesized Piper replies: book two guests at 20:00 on December 2, 2026; update the same reference to three guests at 21:00; cancel. The test verified explicit confirmation before each mutation, phone source preservation, version increments, created/updated/cancelled audit history and released capacity. Reference and phone were spoken together in long sentences. All caller and reply WAVs contain audio. No physical microphone or speaker playback was tested.

The initial attempt in `results/voice-flow-parakeet/report.json` failed at updating because the backend time parser rejected spoken hour words. The parser now accepts phrases such as nine in the evening and eight in the morning. The failed test booking was cancelled through an explicit typed API cleanup, recorded separately in that report. The successful rerun also ended cancelled.

Reproduce with `uv run --extra voice python voice_flow_test.py --combined-contact --output results/voice-flow-parakeet-new`. Restart any existing microphone client to load the default backend change.

## Phone-only MCP acceptance (2026-10-26)

`results/voice-flow-phone-mcp-final/report.json` passed eight synthetic caller turns through Piper, Silero, Parakeet, FastAPI/Ollama and actual MCP stdio lookup: booking, updating and cancelling without speaking any reference. Audit logs contain `lookup_reservations_by_phone` for update and cancellation. Confirmations, version increments, retained event history and capacity release all passed; the test reservation ended cancelled. The previous two failed attempts are retained with separately recorded typed-API cleanup. Explicit phone digits now bypass LLM field rewriting, and structured extraction requires all fields (missing values null). Full suite: 169 tests and 11 subtests passed. Physical microphone/speaker playback remains untested.

## Exact half-past-eight positive scenario (2026-10-26)

`voice_positive_test.py` seeds a labelled two-person booking for tomorrow at 20:00, then runs the exact four customer utterances using phone-only MCP lookup. `results/voice-positive-experience-final/report.json` passed: four people at 20:30 on the same date and booking ID, explicit Yes, please confirmation, version increment and created/updated history. Fixture cancelled after verification by typed tool cleanup. Median estimated end-of-speech to first synthesized audio was 8.221 seconds, range 2.025–9.624 seconds across four turns. Stage breakdown is in `results/voice-positive-experience-final/comparison.md`. Estimates include 480 ms VAD silence and exclude model loading; no physical microphone or speaker onset was measured. Earlier failed reports are retained; their fixtures were also cancelled. Fixes: half past eight parsing, ASR-split to morrow, comma-tolerant explicit confirmation, and avoiding a no-op update readback before actual changes are supplied.

Voice etiquette: sessions open with Thank you for calling [restaurant]. How can I help you today? Completed reservation operations and restaurant-information answers append Is there anything else I can help you with? A negative reply at that point ends with Thank you so much for calling. Have a good day! A no during reservation confirmation still declines the change rather than ending the call. Explicit goodbye also ends the voice session through the API and is audited.

Day/night closing uses the restaurant timezone and the call arrival time captured when the voice session starts: 06:00 through 17:59 says Have a good day!; 18:00 through 05:59 says Good night! Calls crossing a time boundary keep the arrival-time farewell. Tests cover both boundaries and calls crossing into the next day. The opening remains Thank you for calling [restaurant]. How can I help you today?

## Complete greeting/update/goodbye acceptance

`results/voice-greeting-update-goodbye/report.json` passed the opening greeting plus five synthesized caller turns through Silero, Parakeet, actual phone-only MCP lookup, live API/Ollama, SQLite and Piper response audio. Two guests at 20:00 changed to four at 20:30 only after confirmation. The reply asked Is there anything else I can help you with? Caller said No, thank you. That is all; the agent thanked the caller, said Good night and ended the session. All eleven WAV files contain audio; the labelled fixture was cancelled by separate typed-tool cleanup. Day/night boundaries and arrival-time preservation were separately tested with fixed clocks. Physical microphone and speaker playback remain untested.

Unavailable reservations now offer up to three availability-checked alternatives: one hour later on the same date (or one hour earlier if later is unavailable), then the requested time on the next day and two days later. Suggestions obey operating hours, stay duration, seat/table capacity, duplicate checks for updates and restaurant-local past-time filtering. New bookings and updates ask Would any of these work for you? No option is held or committed; selection needs readback and explicit confirmation, and availability is rechecked at commit.

## Unavailable-slot voice acceptance

`voice_alternatives_test.py` and `results/voice-alternatives-final/report.json` verify a real full 40-seat fixture, checked next-day and two-days-later offers, spoken first-option selection, explicit confirmation before saving, further-help question and goodbye. All eleven synthetic reservations were cancelled in cleanup. Core reservation assertions passed, with one date clarification and a misheard surname recorded as speech limitations in the report. `The first option works for me` now maps to the actual offered date/time in code; new-booking Yes, please tolerates comma punctuation. Booking extraction requires all structured fields, with null for missing fields. Full suite: 186 tests plus 11 subtests passed. Physical microphone/speaker playback remains untested.

Authoritative restaurant clock: date/time comes from the operating-system clock converted to the configured restaurant timezone (currently Asia/Kolkata). The API captures call_started_at at session creation, and the LLM receives fresh trusted clock context each turn for booking extraction, changes and menu/policy responses. Relative dates remain caller words and are resolved by Python with restaurant-local today. Caller text is not the source of the current clock. The farewell still follows arrival time. Timezone independence and midnight rollover tests pass; full suite 188 tests and 11 subtests.

## Tomorrow at half past eight voice acceptance

`voice_tomorrow_test.py` and `results/voice-tomorrow-clock/report.json` passed four synthetic caller turns. The only booking date/time spoken was tomorrow at half past eight; the caller never supplied the current date/time or an absolute booking date. The restaurant-local clock independently determined the expected next date. Readback and saved SQLite row both matched 2026-10-28 at 20:30 Asia/Kolkata; no write occurred before Yes, please. The agent asked about further help and ended with Good night. All nine audio files contain frames. The test booking was cancelled separately after verification. Physical microphone/speaker playback remains untested.

## Next Saturday voice acceptance

`voice_tomorrow_test.py --when "next Saturday" --output results/voice-next-saturday-clock` passed four synthetic spoken turns. The caller requested next Saturday at half past eight, gave only name/phone, confirmed, and declined further help. The clock-derived expected date, spoken readback and saved SQLite row all matched Saturday October 31, 2026 at 20:30 Asia/Kolkata. No current date/time or absolute reservation date was supplied by the caller. Confirmation-before-write, further-help question, goodbye/session end and fixture cleanup passed. All nine WAVs contain audio; physical microphone/speaker playback remains untested.

Voice reservation entry now asks Do you have a reservation with us? before collecting new-booking contact details. Yes starts existing-reservation phone/MCP lookup; no processes the original booking request, preserving its date, time and party size. The question and yes/no branch are code-owned, not LLM-generated. Explicit update/cancellation requests still enter their existing workflow directly. Neither branch commits without the normal readback and confirmation.

## Question-by-question new voice booking

`results/voice-saturday-asked-phone-name-verified/report.json` passed six actual-model synthetic caller turns: request two guests next Saturday at half past eight; answer no to existing-reservation question; provide only spoken phone digits when asked; say Alex only after the name question; confirm; decline further help. The original request date/time/party size survived each turn. Readback and SQLite row matched October 31, 2026 at 20:30 Asia/Kolkata, name Alex and phone source. No reservation existed before confirmation. All thirteen audio files contain frames. Fixture cancelled after verification; physical microphone/speaker playback remains untested. The preceding attempt only failed an overly strict test assertion that did not allow Welcome back before the name question; no booking was created.

## Existing-reservation yes-branch voice acceptance

`voice_existing_yes_test.py` and `results/voice-existing-yes-update-complete/report.json` passed six synthetic caller turns: generic reservation request, Yes to existing booking, phone digits only after being asked, Make it four people and half past eight, explicit Yes please, then no further help. Actual MCP lookup is present in session audit events. Same booking ID/source/date retained; party size four, 20:30, version two after confirmation, created/updated history without duplicates. All thirteen WAV files contain audio. Fixture cancelled by separate typed-tool cleanup. Test exposed and fixed lookup-to-update transition for natural change wording and LLM omission of half-past minutes; literal half-past text is now preserved. Phone digits cannot be reused as the extracted booking reference. Earlier failed attempts remain recorded and fixtures were cleaned up. Physical microphone/speaker playback remains untested.

## Measured latency optimization

See `results/voice-latency-optimization/comparison.md`. No real microphone turns exist in voice_metrics.jsonl; these are labelled synthetic measurements. Same six caller utterances: median estimated end-of-speech to generated first audio fell from 5.014 s to 1.139 s, max from 12.257 s to 3.472 s. Median backend fell from 3719.5 ms to 24 ms. LLM calls dropped from [0,2,1,1,0,0] to [0,1,0,0,0,0]. Warmup and common-phrase priming excluded. New booking, existing update, and full-slot alternative selection all passed and fixtures were cancelled.

Default LLM is now gemma4:e2b-it-qat, selected after an eight-case structured-extraction development evaluation (8/8 versus old model 7/8). Ollama requests default to 2048 context, 192 output tokens and keep_alive 30m; override with OLLAMA_NUM_CTX, OLLAMA_NUM_PREDICT and OLLAMA_KEEP_ALIVE. The microphone launcher warms the model through /api/voice/warmup and primes common public Piper phrases before listening. VoiceRunner metrics now include llm_calls and llm_ms. Use `python voice_report.py` for real metrics, or `python voice_report.py --synthetic-report results/voice-latency-optimization/after-new-booking/report.json` for synthetic summaries.

VAD ends after 14 silent 32-ms frames (448 ms). Piper synthesizes later sentence chunks in a bounded producer queue while the first plays; common greetings/questions use a bounded in-memory cache. LLM output remains schema-validated before speech; raw partial JSON is not sent to Piper. LangSmith post/patch exports use a bounded daemon queue and do not block the response. Local audit writes and booking transactions remain synchronous.


## Browser voice testing

Open http://127.0.0.1:5173/voice (also linked from customer chat and the owner dashboard). Keep the API on port 8010 and Vite on 5173 running. Press Start call to hear the Piper greeting, then Record answer and allow microphone access. Press Send recording when finished; recordings stop automatically after 29 seconds. The browser sends mono 16 kHz PCM WAV to local Parakeet, submits its transcript through the shared voice session API, and plays the Piper response. This page uses explicit recording controls rather than the command-line Silero automatic endpoint detection.

The page shows transcripts, structured turn data, and timings measured from Send recording until response audio is ready (not actual speaker onset). It does not write browser timings to voice_metrics.jsonl. Audio is not saved to disk; transcripts and reservation audit records use the existing persistence. Confirming a reservation writes a real prototype booking. End call stops capture/playback and closes the session; it does not cancel reservations. If a turn fails during confirmation, inspect Reservations before retrying in a new call. The local audio service handles one STT/TTS request at a time and rejects concurrent work with 503.


### Combined voice acceptance suite

Run `.\.venv\Scripts\python.exe -u voice_acceptance_suite.py` from the project directory while the local API is running. This single command covers new booking, phone-only update and cancellation, and selection among multiple reservations on the same phone number. It generates caller speech, uses the same Parakeet/Piper HTTP endpoints as the browser, checks confirmation guards and database/audit results, and cancels its dedicated test fixtures. It does not test a human microphone or physical speaker playback. Reports and WAV recordings are under `results/browser-voice/`; `acceptance-suite.json` summarizes all scenarios.

The Voice Test page also has **Run all tests**, with pending/running/passed/failed results and expandable checks for each scenario group. Only one suite runs at a time; its synthetic speech shares the local audio service, so finish live calls first. Results remain available while the API stays running. **End call** and **Stop chat** close their respective conversations and reject future turns. Queued turns are stopped before processing; work already processing may still complete, so inspect Reservations when stopping during confirmation. Stopping does not cancel saved reservations.


### Hands-free browser calls

The browser voice page now needs only Start call and End call. Start requests microphone access once and keeps its stream connected. Browser energy detection retains a short lead-in, waits about one second of silence (to tolerate spoken phone digits), submits the turn automatically, and resumes listening after reply playback. Brief clicks and idle silence do not submit turns; utterances are capped at 28 seconds. Sustained louder speech during playback interrupts the reply; browser echo cancellation and headphones help. Up to two completed extra utterances can wait behind an in-progress API turn, so reservation operations remain sequential. The page still uses completed-turn Parakeet/LLM/Piper HTTP requests; it does not stream partial ASR or LLM tokens. Model processing latency remains. Real microphone/noise/barge-in acceptance still requires a live human test. The generated-audio suite verifies backend workflows; `node --test dashboard/src/voiceEndpoint.test.mjs` verifies browser endpoint segmentation.


### Confirmed conversation restart

In both chat and voice, “I change my mind”, “I changed my mind”, “forget what I told you”, “forget what I said”, and “start over” prompt for restart confirmation. Yes replaces the receptionist state, clears draft slots and pending changes, and returns to the Algo bites greeting. No retains the pending workflow; unclear replies repeat the restart confirmation. Restart confirmation takes priority over booking/update/cancellation confirmation. Existing saved reservations, audit records, call arrival time, and request-id retry receipts remain intact. To change a saved reservation, use the normal update or cancellation flow. The browser drops queued pre-restart utterances when the restart question or confirmed restart arrives. The hands-free acceptance scenario now includes a rejected readback, correction from 9 p.m. to “830”, confirmed and declined restarts, and restart after saving a booking.


## Complete unified acceptance harness

Run `.\.venv\Scripts\python.exe -u voice_acceptance_suite.py` from the project directory, or use the existing Run all tests button. Both use the single registry in `voice_test_harness.py` (13 scenario groups). The runner starts its own loopback API with two active turn workers, so the concurrency case is a real simultaneous-confirmation test and does not depend on the shared UI server's one-worker default. No frontend changes are required.

Coverage includes booking, phone-only update/cancellation, multiple reservation selection, hands-free endpointing/interruption logic, restart yes/no and 830 correction, out-of-hours booking and updates, declining an alternative, declining then confirming a different time, fully booked baseline alternatives, last-table races and idempotent retries, natural spoken readbacks/alternatives/confirmations with actual audio round-trip transcription, and routing/database/confirmation regression tests. Physical microphone, room noise, echo cancellation and speaker playback still need live testing.

Each scenario reports Pending/Running/Passed/Failed, elapsed seconds, individual checks, errors, its process log and cleanup status. A failed scenario does not stop later scenarios. The process exit code, fresh report, assertions and fixture cleanup must all pass. CLI exits nonzero if anything fails. CLI and UI runs share a file lock to avoid overlapping fixtures. Existing baseline reservations are fingerprinted before and after the full run and must remain unchanged. Native numerical libraries load before the private API creates worker threads to avoid Windows import deadlocks.

Latest summary: `results/voice-suite/latest.json`. Each run also has an immutable folder under `results/voice-suite/<run-id>/` containing `summary.json`, individual reports/logs and the private API log. Existing scenario folders still contain WAV recordings. The private API shuts down when the runner closes its control pipe. The shared UI/API is not restarted by the harness.


### Default repeat policy

Incomplete sentences, unrecognized text/audio, and reservation-field extraction failures share a consecutive-understanding counter. The first three failures say “I didn't get that. Can you please repeat?” The fourth consecutive failure ends the AI conversation with a staff-assistance handoff. A successfully understood turn or confirmed restart resets the counter. Replayed request IDs do not consume extra attempts. Explicit customer requests for staff still hand off immediately. The local prototype supplies staff contact details; it does not place a telephone transfer.


### Consolidated decision engine
The default `RECEPTIONIST_DECISION_MODE=unified` uses one validated JSON generation for relevance, intent, confidence, a short decision summary, and explicitly supplied reservation/menu fields. The schema requires every field key, with null for missing fields. The same decision supplies creation and update/cancellation workflows; unresolved inputs reuse that decision rather than calling a second judge.

Clear controls, explicit reservation choices, phone digits, standalone expected times, broad-availability clarification, and direct hours/location requests can use zero generation calls. A ContextVar isolates decisions and the one-call budget per turn and customer. Failed or invalid model output requests repetition without a model retry or booking write. Menu facts and policy passages are rendered from tool results, without an extra response-generation call. Embedding retrieval and ASR are separate from the LLM generation budget.

`legacy` remains available as an explicit rollback/benchmark mode. Existing individual component-schema mock tests run in legacy mode; `tests/test_decision_engine.py` tests the unified public turn contract and API retries. The real generated-speech acceptance suite uses the default unified engine. No frontend changes are required.

Repeatable latency measurements: `python benchmark_decision_engine.py before --mode legacy` and `python benchmark_decision_engine.py after --mode unified`. Results in `results/decision-engine/` exclude microphone, ASR and audio playback. Voice-suite reports separately retain generated-audio scenario evidence and cleanup checks.

Measured decision latency on this laptop did not improve with consolidation: median freeform booking 3.61s → 11.05s, and unrelated input during booking 7.01s (two calls) → 9.09s (one call), three repetitions per input. The required contextual JSON contract is larger than the old extraction-only prompt. Sparse fields and a shorter prompt were evaluated but lost explicit fields or misassigned them, so the accurate required-field context was retained. These measurements exclude ASR/TTS and are not a microphone-to-speaker latency benchmark. See `results/decision-engine/comparison.json` for the final evidence and limitations.


### Optional end-of-call feedback
After the customer says they need nothing else (or gives a polite goodbye), the API offers an optional survey. An explicit yes opens the 1–5 whole-number rating question; no/skip ends without a score. Hard stop/End call and staff handoffs do not force a survey. Ratings and consent/decline status, with timestamps, are saved in each session's durable call audit records, returned in `survey` and `structured.output.survey`, and exposed as `calls[].feedback` in the owner API. Owner intent/outcome/latency use the last business turn, so survey turns do not relabel a successful booking as Needs review. No LLM calls are used for the survey.

`test_voice_customer_feedback.py` tests generated speech through consent, invalid score, a recorded 4/5, session deletion and owner retrieval. It is scenario 15 in Run all tests. Existing voice scenarios explicitly decline the new survey at close and retain that extra caller/agent exchange in their reports. Physical microphone/speaker testing remains separate.


### In-person occupancy cutoff
Owner API in-person entries may be backfilled for historical dates or entered for today up to the current restaurant-local minute; future arrivals are rejected by both the API and the seating tool. Live and preview day plans never label future windows as observed in-person occupancy. Older future walk-in labels are returned as unknown/unverified with their original recorded source retained; their reserved capacity is preserved. Database baseline rows are not deleted or rewritten. Phone/chat sources remain unchanged.


### Contextual booking recommendations
The unified engine receives a bounded previous user/assistant exchange, the most recent topic, and the pending unavailable date/time/party size. A short suggestion/recommendation follow-up immediately after an unavailable booking stays in the booking workflow and refreshes database-checked alternatives without another generation call. Explicit food/menu requests may change topic; numbered option selection remains a separate confirmation-safe path. Rejected times are retained as suggestion context even though they are removed from the pending booking slots. If the original ±1-hour/next-day choices are all unavailable, search is bounded to valid service slots on the requested day and next two days. No month-wide search or write is performed. `tests/test_booking_context.py` and `test_voice_booking_recommendations.py` cover this regression.

### Decision latency investigation (October 27, 2026)

The accepted profile keeps the original unified prompt, nested required-field schema and generation settings. `RECEPTIONIST_DECISION_FASTPATHS=safe` is the default for new processes. Anchored guest-count, valid date-only, explicit name, reservation-selection and initial lookup/change controls avoid unnecessary inference. Mixed selections/corrections fall through to inference; ordinary polite selection endings stay deterministic. The one-call ceiling, session isolation, grounded answers and booking confirmation controls remain. Set `RECEPTIONIST_DECISION_FASTPATHS=baseline` to roll back these added gates.

Prompt/schema shortening was **rejected**. A smaller required JSON and abstracted context reduced generation time, but real voice booking lost “next Saturday” after the customer said no. Restoring fuller instructions also failed field/routing checks. `RECEPTIONIST_DECISION_PROTOCOL=compact` is an unaccepted experimental path retained only for reproducing the investigation; do not enable it for customer use. The default stays `baseline`. Prototype code-derived summaries and temporal evidence checks are not part of the accepted production protocol.

Run `.venv\Scripts\python.exe benchmark_safe_decisions.py` to reproduce the paired replay on 20 frozen inputs × 2 repetitions. It measures gate costs and reuses the identical recorded model sample when the model request is unchanged, avoiding cache-biased duplicate generation. **After latency is replay-derived, not an independent live API benchmark.** The original model payload and per-generation cost are unchanged. See `results/decision-latency/comparison.txt` and `comparison.json` for average/p50/p95, token counts, explicit-field accuracy and rejected experiments. Strict field-delta accuracy is not customer task success.

Run `.venv\Scripts\python.exe run_private_decision_acceptance.py --protocol baseline` for the existing 15 groups against a private SQLite/RAG/audit copy. Set `RECEPTIONIST_FIXTURE_DIR` before importing application modules; it redirects reservations, business JSONL, call audits and Chroma. The original 1,911 baseline rows are checked under a canonical row hash. The first original-protocol run passed 13/15 groups; an exact saved selection-audio rerun verifies the selection fix. Natural-date business assertions passed, but one stored update-readback WAV still produces an empty ASR transcript; the audio replay remains failed. The afternoon/evening assertion now checks the periods actually offered, and still fails blank audio.

These tests use generated speech and API calls, not physical microphone/speaker or echo-cancellation tests. The shared live API was not restarted; the safe default takes effect on its next restart. No UX files were changed.

### MCP connection reuse and information requests (October 27, 2026)

MCP lookups now default to `pooled`; set `RECEPTIONIST_MCP_MODE=oneshot` to use a separate process per lookup. This changes connection reuse only, not model selection or inference settings. The pool binds each process to resolved database path, restaurant, trusted restaurant-local date and phone region. Four scopes and 32 queued requests per scope are bounded; idle connections expire after 60 seconds. Each owner thread keeps the entire stdio/ClientSession lifetime in one async task. Every lookup is a fresh read with a separate Future/request ID and response-scope validation. Tool errors stay per caller; transport/timeout failures retire the connection and fail closed until a subsequent lookup can open a new one. No automatic replay or result caching is used.

The total lookup deadline is 20 seconds including pool waiting/startup, with a 15-second tool read limit. Cleanup runs on idle expiry, API shutdown and normal process exit. `RECEPTIONIST_MCP_METRICS` optionally names a local JSONL diagnostic file for process readiness, client stages and server database timings; diagnostic entries contain request IDs/timings, not phone numbers. Regular audit events expose client timings and failure phases. Raw initialize time includes Python/import startup; the report separates readiness from post-ready protocol completion.

`results/mcp-lookup/comparison.txt` contains real before/after calls on identical private copies of the 1,911-row baseline. The benchmark includes cold scopes; warm-only numbers must not be presented as first-call or end-to-end voice latency. `benchmark_mcp_lookup.py --baseline-volume` runs with the mode explicitly selected in the benchmark process. `run_private_mcp_regressions.py` fixes regional component fixtures without altering live configuration.

Availability questions such as “anything free in November?” and “come sometime next week?” now narrow date/time/party size and answer availability before requesting identity. Their clarification state is separate from pending booking details. Late-arrival questions route to exact policy quotations, including during active booking/update/cancel flows. Information acknowledgments cannot confirm a pending write: the original readback is repeated first. New corrections invalidate the saved readback. `evaluate_information_requests.py` scores the customer's original request through the API using private reservations/RAG and frozen fresh paraphrases; it does not count an accidental later booking as success. No UX files or production baseline rows are changed by these tests.
