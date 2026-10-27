# Decisions

- 2026-10-26: Persist reservation source at creation: phone/voice blue, chat purple, staff-recorded in-person seating amber, unknown grey. Retain origin on change/cancellation; recover historical source only from session audit evidence. Show text labels alongside colors.
- Owner in-person entry is limited to today and non-future arrival times, uses normal atomic 90-minute seat/table checks, and persists a creation event and idempotency key. It is for walk-ins; it does not check in an existing reservation or track early departures/overruns.

- 2026-10-26: Add restaurant-local calendar and half-hour day plans. Adopt approved ten four-seat tables and 90-minute stays, daily lunch 11–15/dinner 18–23 plus weekend brunch 7–10. No weekly closure; full stays must finish before service closes.
- Extend atomic booking/update capacity checks to overlapping stays and required whole tables. Preserve existing database rows and remove fictional recurring sample holds. Suggestions use deterministic first-fit table positions per day and are explicitly not persisted physical assignments or guest check-in status. This supersedes earlier exact-start-time capacity assumptions.

- 2026-10-26: Support reference-plus-phone reservation lookup and explicit confirmed updates/cancellations through the shared chat/voice workflow. Phone matching is not authentication. Keep write tools outside the LLM tool menu.
- Apply capacity, duplicate and version checks under SQLite BEGIN IMMEDIATE. Persist operation receipts and before/after booking events in the same transaction; cancellation retains the row and releases capacity. Add status/version columns without replacing existing records. The owner table remains read-only with status/history.
- This supersedes the earlier staff-only edit decision. Missing references and repeated failed lookups still offer staff assistance.

- 2026-10-25: Add Chroma/Ollama policy retrieval under restaurant-specific knowledge IDs, independent of existing reservation IDs. One collection per restaurant, manual Markdown ingestion, source digest checks and building/ready metadata prevent serving known stale indexes. Stop the API when re-ingesting.
- Restrict policy answers to structured selections of complete exact retrieved sentences. Code checks sentence/source pairs and brevity; preserve negations/conditions. This sacrifices paraphrase flexibility for auditable grounding and does not replace relevance/faithfulness evaluation.
- Keep similarity threshold 0.62 after an unrelated flight question scored 0.587 at the initial lower cutoff. Initial source recall is 53/60 with 6/6 unrelated-query abstentions; accept conservative misses transparently rather than claim universal retrieval or calibrated confidence. Add broader/held-out cases before relaxing this guard.
- Sample policies are course fiction. Two isolated policy corpora are indexed; the active receptionist remains Malnad Mane. No full per-business restaurant-data migration or unsupported FAQ/PDF ingestion is implied by this step.

- 2026-10-25: Add optional local voice dependencies and a terminal microphone/speaker client using Silero ONNX, faster-whisper small CPU int8, and Piper sentence streaming. Keep audio interfaces separate; use the existing HTTP session API instead of a second engine instance so booking/queue rules and logs stay shared.
- Voice is half duplex with input closed during playback; barge-in remains future work. Reject partial/overlong audio before engine submission. Represent silence as explicit audio-status events, not fabricated customer transcripts.
- PyAV 19 broke faster-whisper's file decoder during the real smoke test; constrain av<19 in the voice extra. Verify actual downloaded models with a synthetic WAV, no microphone capture or audible playback during agent testing.
- Log stage timings without retaining raw audio. One synthetic transcription took roughly 3.9 seconds, so do not claim the 3-second full-turn target. Physical device testing, noisy speech WER/entity metrics and reliable p95 remain unverified.

- 2026-10-25: Recognize returning customers only after a phone is supplied; chat has no verified caller ID. Persist same-day visits using restaurant-local dates and normalize phone formats by configured region. Existing reservations dated today or later trigger a choice before any new write.
- Protect against customer duplicates independently of idempotency keys: same restaurant, normalized phone, date and time is blocked atomically, even when name/party size differ. Keep legitimate different-slot bookings possible after explicit selection and confirmation.
- Existing-reservation edits still require staff. “Modify” invokes handoff and never silently creates a replacement. Phone matching is a contact lookup, not authentication; welcome-back prompts avoid exposing booking details.

- 2026-10-25: Replace the global model-work lock with per-session in-flight locks and a bounded whole-turn scheduler. Default one active/six waiting for CPU; configure two active to overlap independent sessions. Busy sessions receive 409; queue rejection/timeout receives retryable 503 before engine mutation.
- Keep registry locking brief, skip in-flight expiry, and reject deletion during a turn. Serve cached request IDs without queue admission. Synchronize local JSONL I/O separately; owner reads do not acquire inference/session locks.
- Retain single-process Uvicorn because sessions, queue limits and file locks are in memory. SQLite protects local booking commits across processes but not distributed session ownership. Queue scheduling is bounded but not strict FIFO.

- 2026-10-25: Move reservation source of truth to Python's built-in SQLite. Preserve JSONL call/message/review logs. SQLite BEGIN IMMEDIATE transactions serialize capacity recheck plus booking/event insertion across local processes; a five-second busy timeout avoids indefinite writer waits.
- Import legacy bookings once per source/restaurant, transactionally. Preserve source bytes and pre-existing duplicate rows rather than silently modifying historical capacity. New writes do not dual-write the legacy file.
- Booking request keys provide persistent idempotency, distinct from duplicate customer detection. Different requests sharing phone/date are not automatically deduplicated.
- Keep API inference/session locking unchanged for this step. Capacity remains per exact start time as in the previous engine. SQLite is for one local database file, not independent servers with separate copies.

- 2026-10-25: Default owner dashboard uses live local polling; original fixtures remain at `/demo`. Never import evaluation files or invent historical transcripts.
- Persist completed API chat turns and observed events to calls.jsonl. ContextVar event capture is independent of LangSmith. Explicit read-back/confirmation events show booking sequence. New bookings carry originating session IDs.
- Owner review updates append separately. Audit failure never replays business operations. File storage is local, unauthenticated and non-atomic across booking/audit writes; public deployment requires further work.

- 2026-10-25: Add optional LangSmith spans using manual RunTree instrumentation and a ContextVar hierarchy. This preserves plain Python/Ollama architecture without adding LangChain workflows.
- Export no raw conversation or tool payloads. Names/phone numbers can occur anywhere in prose, so omit free text rather than claiming regex redaction is complete. Export categorical decision summaries and confidence; model-written reasoning stays local/API-only.
- Tracing requires explicit enablement and credentials. Batch export failures cannot replay operations. API trace IDs identify queued spans, not confirmed ingestion. Hosted acceptance remains unverified until configured with an account key.

- 2026-10-25: Intent classification adds a short decision summary (`reasoning`) and strictly validated self-estimated confidence. Confidence is diagnostic only, not calibrated and not a routing threshold. Non-LLM decisions use null confidence. Expose structured routing metadata in API responses and CLI output; keep customer spoken/chat replies short.

- 2026-10-25: Add a separate React customer chat at `/chat` using FastAPI sessions and the same Receptionist router. Keep the owner dashboard explicitly demo-only.
- Session UUIDs isolate visitor state; message UUIDs cache turn responses for retry safety. A process-wide lock serializes inference and booking writes for this local single-worker prototype. Memory-only sessions are capped and expire; production persistence/authentication remain separate work.
- Chat handoffs expose the configured restaurant contact, not a pretend phone connection. No voice implementation or architecture substitution is included in this step.
- Bind local servers to loopback. Use Vite's `/api` proxy; no wildcard CORS or frontend credentials. API uses port 8010 because 8000 was already occupied.

- 2026-10-25: Separate constrained intent classification (`routing.py`) from deterministic dispatch (`Receptionist`). Use Ollama JSON-schema output, locally validated, with keyword fallback for malformed responses and transport errors.
- Prioritize explicit order/human/complaint rules over model output and active reservations. These heuristics require the planned broad intent evaluation; router unit tests are not evidence of 100% recall across real speech.
- Limit menu inference to read-only menu/dish tools and a total of three actual tool invocations, including parallel tool requests. Reject tool names outside the allowlist.
- Move reservation writes behind a Python-owned explicit confirmation state. Re-check capacity when committing. Corrections invalidate confirmation and trigger a new check/read-back.
- Reuse existing YAML and JSONL storage for this bounded implementation step. Policy RAG is not present, so policy requests fail closed with an uncertainty response and staff offer.
- Keep the React dashboard unchanged and demo-only until the audit API is reviewed and implemented.
- Existing order-taking evaluation cases are obsolete for v1; preserve them for review rather than claiming the old suite passes.
