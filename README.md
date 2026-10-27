# Restaurant receptionist

React/Vite customer restaurant website and owner dashboard, with a local FastAPI receptionist, chat and voice, SQLite reservations, policy retrieval, optional LangSmith tracing and customer feedback.

## Current customer experience

- `/chat`: restaurant website, USD menu and specials, floating chat widget.
- `/voice`: wide voice workspace with dialing tones, live transcript previews, separate speaker colors, app-only mute/unmute and call controls. The agent finishes speaking before listening to avoid speaker echo interruptions.
- `/`: owner overview with saved date filters and follow-up links.
- `/owner/conversations`: saved filters, summaries, follow-up notes and resolve/reopen controls.
- `/owner/reservations?view=live`: compact calendar/times, guest names on suggested tables, direct reservation management and collapsible navigation/planner.

The demo is configured for US/Central time and USD. Configure the real address and contact phone in `restaurants/demo_restaurant.yaml` before sharing with customers. Voice uses local Parakeet speech recognition and Piper `en_US-lessac-high` synthesis. Completed conversations offer an optional consent-based survey; explicit stops and repeated unintelligible audio can end immediately.

Generated logs, reservations databases, recordings, downloaded models, dependencies and build output are excluded from Git. Run the setup commands below to create a fresh local environment. Automated voice checks use synthetic audio device adapters with real local speech services; they do not verify physical microphone or speaker hardware.


## Reservation sources and in-person seating

The floor plan and timeline use blue for phone/voice reservations, purple for chat, amber for staff-recorded in-person seating, and grey for unknown sources. Text labels accompany colors. Source is saved in SQLite when a booking is created and remains unchanged when it is updated or cancelled. For older rows, the owner view can recover source from recorded session/channel evidence; missing evidence stays unknown.

On today's restaurant date, use **Record in-person seating** to enter the guest name, party size and actual seating time. It requires an explicit staff confirmation, rejects future arrivals, and checks the same 90-minute seat/table capacity as reservations. The write, creation event and retry key are transactional. No phone number is required for a walk-in. These records occupy the configured stay in the plan; early departures, overruns and converting an existing reservation into checked-in occupancy are not yet tracked. The owner API remains loopback-only and unauthenticated.

## Reservation calendar and table planning

The owner Reservations page starts with a month calendar. Choose a restaurant-local date, then a 30-minute window to see overlapping reservations, reserved guest seats, suggested table assignments and that day's register/history. It polls live data every five seconds. Saturday/Sunday include special brunch 07:00–10:00; every day includes lunch 11:00–15:00 and dinner 18:00–23:00. There is no weekly closing day.

The approved layout is ten four-seat tables with 90-minute stays. New bookings and changes check overlapping guest capacity and whole-table requirements inside the SQLite write transaction. A party of five requires two tables; unused seats at a reserved table cannot be promised to a different party. Arrivals must allow the full stay before service closes (last arrivals: brunch 08:30, lunch 13:30, dinner 21:30). All half-hour windows through closing remain visible for planning. Arbitrary valid arrival minutes are included in every window they overlap; end times are exclusive.

Table IDs T01–T10 are suggested planning positions, not saved physical assignments or live occupied status. Groups can use multiple tables; adjacency is not guaranteed. Assignment suggestions can change after bookings change. Guest arrival/check-in, early departure, manual table locking and measured floor layouts are not implemented. Existing bookings are preserved; records that exceed the new layout require host attention. The old fictional recurring capacity holds were removed from the sample YAML; real SQLite reservations and their history remain intact.

## Policy RAG

Policy questions now use local Ollama `nomic-embed-text` embeddings and one Chroma collection per restaurant. Sample Markdown policies live under `businesses/malnad_mane/docs` and `businesses/maison_olive/docs`; only Malnad Mane is currently configured as the active receptionist restaurant. These are fictional course sample policies, not externally verified business policies. The restaurant YAML's `knowledge_id` selects the policy collection without changing its SQLite restaurant identity.

Prepare or refresh indexes while the API is stopped:

```powershell
ollama pull nomic-embed-text
uv run python ingest.py --business malnad_mane
uv run python ingest.py --business maison_olive
uv run python eval_rag.py
```

Both indexes have already been built on this machine. The `ingest.py` command chunks Markdown at approximately 400 lexical tokens with 50 overlap, embeds locally, upserts the restaurant collection, removes obsolete chunks, and marks the index ready only after completion. It never indexes booking/contact logs. Changed or deleted source files invalidate retrieval until re-ingestion. Keep the API stopped during ingestion; the stale/building check fails closed but is not a distributed index-publication protocol.

The menu/policy agent can use `check_dish`, `get_menu`, `get_specials`, and `search_knowledge(query,k=4)` with at most three tool invocations. Policy routes expose only the retrieval tool. After successful retrieval, the LLM receives a structured answer schema and selects up to two complete exact source sentences (60 words total). Code validates every sentence/source pair against this turn's retrieved passages and the original document sentences. A response without retrieval, unsupported text, low relevance, missing model/index, or stale data yields an uncertainty/staff offer. Exact quotes prevent invented policy facts but do not prove the model selected the most relevant sentence; answer relevance still needs evaluation.

Policy source filenames appear in chat and the owner transcript/audit details. Voice speaks only the policy sentences. LangSmith exports still omit raw documents and conversation text. Menu prices, hours, capacity and allergens remain structured-tool concerns, not RAG facts. The initial policy corpus covers cancellations, groups/events, kids/pets, dress/corkage and payments/accessibility. PDFs/TXT and other FAQ domains are not ingested in this step.

The default minimum cosine similarity is conservatively set to 0.62 (`RAG_MIN_SIMILARITY` can override 0–1). It is a retrieval score, not a probability. The initial 60 positive/6 unrelated-query run achieved 53/60 source recall@4 (88.3%) and 6/6 negative abstentions. Several supported paraphrases are rejected at this cutoff; results are in `results/rag-latest.json`. These are development/calibration cases, not a held-out generalization claim. Broader retrieval coverage and LLM-judge faithfulness calibration remain to do. The live Gemma birthday-cake check selected the exact conditional sentence and correct source.

Verification: `uv run pytest tests -q`, `uv run python eval_rag.py`, and `npm run build` in dashboard. Restart the API after changing code/indexes. Try “Can I bring an outside birthday cake?” or “What is your corkage policy?” in chat or voice. Only supplied policy facts can be answered; unconfigured policies should offer staff assistance.

## Local voice agent

The microphone/speaker client is implemented with Silero VAD, local Parakeet TDT 0.6B v3 CPU Q8_0, and Piper sentence-chunk playback. It connects to the same API/SQLite engine and logs voice sessions in the live owner dashboard. See [VOICE.md](VOICE.md) for setup, run commands, audio device selection and verification limits. Start with `uv run --extra voice python voice_agent.py` after launching the API. No real phone line or browser microphone button is included.

## Returning customers and duplicate reservations

During a reservation conversation, the receptionist asks for the phone number first if it was not supplied. Chat has no verified caller ID, so recognition cannot happen before a number is provided. It normalizes numbers using the restaurant's `phone_region` (sample: IN), so a local number and its +91 version match.

Recognition checks same-day visits in the restaurant's timezone plus existing reservations dated today or later. A returning customer with a reservation hears: “Welcome back to Malnad Mane. Would you like to change or cancel your existing reservation, or make a separate booking?” No existing guest name/date/time is disclosed in that prompt. A returning visitor without a reservation gets a welcome-back message and continues slot filling. Same-day visits persist in SQLite; recognition is scoped to the restaurant and does not treat a phone match as identity verification.

Selecting a change or cancellation starts the phone-based MCP lookup described below. Selecting “keep” ends the pending request without a write. Selecting a separate booking continues the normal read-back/confirmation workflow. Verified caller-ID recognition remains future work.

SQLite checks normalized phone + restaurant + reservation date + reservation time under the booking write transaction, independently of the request ID. A match blocks a new reservation even if party size/name changed or capacity remains available. A different date/time can be a separate booking. Identical existing historical rows are preserved during migration, but new duplicates are prevented. Customers needing multiple legitimate bookings under the same phone/time must contact staff; there is no automatic override.

Phone normalization checks whether a number is structurally possible, not whether the customer owns it. The active sample uses `timezone: America/Chicago` and `phone_region: US`; configure these for the actual restaurant location. Local-day recognition tests include midnight across timezones; cross-process tests include six independently keyed attempts at the same customer/slot with spare capacity.

## Update or cancel an existing reservation

Booking date validation accepts English month names (for example, `December 1, 2026`, `Dec 1st, 2026`, or `1 December 2026`), ISO dates, weekdays, and today/tomorrow. A month/day without a year uses its next occurrence from the restaurant's local date. Impossible calendar dates and ambiguous numeric slash dates require clarification. The confirmation read-back includes the resolved year.

Chat and voice share the same change workflow. New booking confirmations include an eight-character reference. Say “change my reservation” or “cancel my reservation”, then provide the reservation phone number. The router calls the local MCP tool `lookup_reservations_by_phone`. A unique active upcoming match is read back; multiple matches are listed by date/time/party size and require a numbered selection. References remain optional for legacy callers. Phone matching is contact matching, not verified identity or an OTP check. No reference is required. Repeated failed matches offer staff assistance.

Updates support date, time and party size. The receptionist reads back the proposed change or cancellation and waits for an explicit “yes”. Corrections require a fresh read-back; “no” keeps the original booking. Opening hours, party limits, duplicate bookings and capacity are checked before an update. Capacity and reservation version are checked again inside the write transaction, so a competing booking or stale confirmation cannot overwrite newer data.

Cancellation retains the record with status `cancelled` and frees its seats. Updates preserve the reference. Before/after events and retry receipts commit with the reservation change; retries cannot apply the same operation twice. The owner dashboard shows status and expandable history. Its reservation table is read-only.

Existing SQLite databases gain `status` and `version` columns automatically on first use; existing rows remain active. Restart the API after updating. Try a synthetic booking, request a new time using its phone number, confirm it, then request cancellation and confirm again. Cancelling a booking does not enforce fees or the sample policy's notice period; staff handle exceptions and commercial terms. Capacity now includes overlapping stays and whole-table limits; see the calendar section above.

## Concurrent chat sessions

The API no longer holds a global lock during model work. Each session allows one in-flight turn; a second request for that session receives HTTP 409 with Retry-After. Once the first completes, retrying its request ID returns the cached result. Session deletion/expiry cannot remove an in-flight conversation. Different sessions are admitted through a bounded turn scheduler; SQLite continues to make booking commits atomic.

CPU-friendly defaults: one active turn, six waiting turns, a maximum 30-second wait for admission. A full queue or admission timeout returns HTTP 503 with Retry-After before the engine runs or mutates the conversation. Retry uses the same request ID. Cached replies bypass the queue. Scheduling covers the whole turn, including its model/tool calls; it is not a strict FIFO queue and does not stream queue-position updates. Multiple conversations can stay connected, but the default executes one turn at a time to avoid overloading local CPU inference.

To allow two turns to execute simultaneously, set this before starting the API:

```powershell
$env:RECEPTIONIST_TURN_WORKERS = '2'
$env:RECEPTIONIST_QUEUE_LIMIT = '6'
$env:RECEPTIONIST_QUEUE_WAIT_SECONDS = '30'
uv run uvicorn chat_api:app --host 127.0.0.1 --port 8010
```

Supported limits: 1–4 active turns, 0–16 waiting, 0–120 seconds admission wait. These bounds leave room in the HTTP thread pool for owner requests. Start at one active turn and measure CPU latency before increasing. The existing per-call Ollama timeout remains separate from queue wait.

Dashboard reads and owner reviews no longer wait for inference. Brief local file/database access can still delay them. In-process JSONL reads/writes are synchronized to avoid partial records during concurrent turns. Responses/logs include `queue_wait_ms`; the owner API includes active/waiting scheduler counts. Keep Uvicorn at one process: sessions and queues remain in memory, and JSONL file locks are process-local. SQLite alone does not make the whole chat service multi-worker ready. Queued/active turns are not cancelled by closing the browser; retry with the same message ID to retrieve the result.

## SQLite reservations

Reservations now use `reservations.sqlite3` through `booking_db.py`. Start the API and React with the existing commands below; SQLite is built into Python and needs no separate service. Before first launch, you can run `uv run migrate_bookings.py` to initialize/import explicitly. The same initialization also runs on first database use.

Legacy `bookings.jsonl` is imported once per source/restaurant and left unchanged. Re-running migration does not import it twice. After cutover it is an archive, not a live booking feed; new bookings go only to SQLite. The database contains reservation IDs, restaurant IDs, contact/details, session IDs, idempotency keys and transactional booking events. The live dashboard reads SQLite. Calls, messages and owner-review files remain JSONL.

Every booking commit uses `BEGIN IMMEDIATE`, reads occupied seats, checks configured capacity plus existing YAML baseline occupancy, and inserts both reservation and booking event in one transaction. Different callers/processes sharing the same local DB cannot pass stale capacity checks concurrently. Readers can preview availability, but that preview does not reserve seats. Writers wait up to five seconds for the database; busy/unavailable writes return failure without a success claim.

Each confirmed reservation request has a stable idempotency key. Reusing the key with identical details returns the existing booking; changed details conflict. A new chat is a new request, but customer duplicate checks now block matching normalized phone/date/time. The current capacity model remains seats per exact start time, not physical-table allocation or dining-duration overlap.

The API has memory-only active sessions and a bounded in-process turn scheduler. Database safety also protects CLI/API booking writers sharing this DB, but the whole service is not multi-worker ready. Use a local disk, one consistent restaurant configuration, and the same DB path for every writer; separate database copies do not coordinate. Prefer a client/server DB for multiple deployment hosts.

Run `uv run pytest tests/test_routing.py tests/test_chat_api.py tests/test_observability.py tests/test_booking_db.py -q`. Tests use temporary databases and include six independent processes competing for last seats, concurrent same-key retries, transaction rollback, legacy import, and restaurant isolation. The old evaluation script uses an isolated temporary DB but its order-taking scenarios remain obsolete.

## Live owner dashboard

The root React page now polls `/api/owner/dashboard` every five seconds after the previous request completes. Run the API and React with the commands below. `/chat` is the customer interface; `/demo` preserves the original fictional dashboard.

New API chat turns append transcripts, routing decisions, latency, trace IDs and actual tool events to `calls.jsonl`. The dashboard groups them by session and supports search, date/outcome filters, JSON export and inspection. Booking read-back and confirmation events are explicit. SQLite reservations and `messages.jsonl` supply actual reservations/messages; evaluation fixtures are excluded. New bookings carry session IDs. Missing historical transcripts cannot be reconstructed.

Owner reviews append to `owner_reviews.jsonl` without rewriting calls. Completed-turn records survive session expiry/restart, although active chats cannot resume after restart. These are ordinary append-only application files, not tamper-proof storage. Failed audit writes are logged and return `audit_warning`; a booking is never replayed to retry logging. Booking and audit writes are not atomic together.

Full transcripts/contact fields stay in local storage. UI phone masking is for display; the owner API and JSON export contain original records. LangSmith exports still omit raw text. Keep this unauthenticated prototype on loopback. Polls do not acquire conversation/inference locks. Voice API sessions are persisted; direct text CLI activity is not. The verification chat contains “dashboard verification” in its caller message.

## Optional LangSmith tracing

Tracing is off unless `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` is set. In the same PowerShell terminal used to start the API (PowerShell 7):

```powershell
$env:LANGSMITH_TRACING = 'true'
$env:LANGSMITH_PROJECT = 'restaurant-receptionist'
$env:LANGSMITH_API_KEY = Read-Host 'LangSmith API key' -MaskInput
uv run uvicorn chat_api:app --host 127.0.0.1 --port 8010
```

Set `LANGSMITH_ENDPOINT` if your workspace needs a regional or self-hosted endpoint. `.env.example` documents settings; `.env` files are not automatically loaded. Do not put keys in the React app or commit them. Disable by setting `$env:LANGSMITH_TRACING = 'false'` and restarting.

Start React as described below, send a chat message, then open the selected tracing project in LangSmith. Expect a `receptionist.turn` root with nested intent classification, Ollama, reservation workflow, and tool spans as applicable. All turns in a chat carry the same application session UUID. API responses include `trace_id` when a root span was queued; this is not proof the hosted service accepted it. The owner dashboard has not been wired to trace links yet.

Export policy: raw inputs/outputs, names, phone numbers, transcripts, tool arguments, and model-written reasoning are omitted entirely. Traces contain static span names, routing labels, confidence, a categorical decision summary, boolean tool outcomes, prompt version, supported model label, generated session UUID, duration and available Ollama token counts. Error text is generic. This deliberately minimizes data instead of trying to detect every name in prose. Normal local engine behavior is unchanged. No external trace delivery has been verified without account credentials.

Exports use the SDK background batching with a short transport timeout. Normal process exit attempts a flush; abrupt termination can lose pending spans. Instrumentation exceptions are isolated from business operations and never cause a booking retry. Router responses and local booking storage do not depend on LangSmith availability.

Run all current checks: `uv run pytest tests/test_routing.py tests/test_chat_api.py tests/test_observability.py -q`.

## Customer web chat (step 2)

Start the API in one PowerShell terminal:

```powershell
cd C:\Maven\Projects\Week4\restaurant-receptionist
uv sync
uv run uvicorn chat_api:app --host 127.0.0.1 --port 8010
```

Start React in another terminal:

```powershell
cd C:\Maven\Projects\Week4\restaurant-receptionist\dashboard
npm install
npm run dev
```

Open [customer chat](http://127.0.0.1:5173/chat) or [owner dashboard](http://127.0.0.1:5173/). Vite proxies `/api` to the local Python server. Ollama must be running. Set `$env:RECEPTIONIST_MODEL = 'qwen3.6'` before launching the API to change the model.

Chat uses the actual restaurant YAML and shared router. Order/human handoffs show the staff contact number and end the session; no phone transfer is claimed. Booking confirmation writes to SQLite. Active sessions expire after one hour of inactivity and disappear on API restart; completed-turn transcripts persist in `calls.jsonl`. A browser reload starts a fresh session. Retries reuse the message ID to avoid executing the same turn twice within that session; separate customer duplicate checks also guard matching normalized phone/date/time across sessions.

This local API runs one process with configurable turn concurrency. CLI/API reservation writes coordinate through SQLite when sharing the same database. It is not a public authenticated service. Policy RAG and the microphone/speaker client are implemented; see their sections for limits. LangSmith instrumentation is implemented; hosted delivery needs account verification.

Verify with `uv run pytest tests/test_routing.py tests/test_chat_api.py -q` and `npm run build` in `dashboard`.

## Run the text router

```powershell
cd C:\Maven\Projects\Week4\restaurant-receptionist
uv sync
uv run receptionist.py --model gemma4
```

Ollama must be running. The default data is `restaurants/demo_restaurant.yaml`; use `--restaurant <path>` for another compatible YAML. Model name is configurable with `--model`. The CLI prints the selected intent and decision source after each turn. Transfers end the text call.

Try opening hours, parking, a table booking, a food order, and two unrecognized inputs. A reservation asks for party size, date, time, name and phone, checks capacity, reads back the details, and waits for explicit confirmation. Changing details requires another read-back. `cancel` stops a pending new booking. Say “cancel my reservation” to start an existing-booking cancellation with phone lookup and confirmation.

## Router verification

Intent JSON now includes `intent`, `reasoning` (a brief decision summary, maximum 240 characters), and `confidence` (a finite number between 0 and 1). Confidence is a model self-estimate, not a calibrated accuracy probability; it does not change routing or bypass confirmation/handoff rules. Invalid metadata triggers keyword fallback. Rule, fallback and active-workflow decisions have code-authored summaries and `confidence: null` rather than fabricated model scores. Chat turn responses expose this metadata under `routing`; the CLI prints it. Customer chat text does not display technical metadata. Reservation slot extraction remains its own field-only schema.

```powershell
uv run pytest tests/test_routing.py -q
```

Tests mock inference and booking writes; they do not require Ollama or alter reservation records. These are router regression checks, not the full component/e2e evaluation suite. The older `eval.py` and scenario data still contain order-taking expectations and need migration before being used as v1 acceptance gates.

## Routes

```text
Caller -> priority handoff rules -> structured intent JSON
                                  -> keyword fallback on failure
                                  -> Python router
                                      hours: YAML + template
                                      location_parking: YAML + template
                                      reservation: extract -> validate -> check
                                                   -> read back -> yes -> book
                                      menu: read-only tool loop, max 3 calls
                                      policy: uncertainty / staff offer until RAG
                                      order/human: transfer and end call
                                      other: clarify; transfer after two failures
```

The menu agent cannot execute booking, order or message tools. Existing legacy order helpers remain in the module for now, but are unreachable through the receptionist router and menu allowlist.

## Scope and remaining work

Routing and chat audit logs use the existing restaurant schema. Full per-business data migration, timezone/holiday handling, stronger extraction validation and broader grounding verification remain subsequent steps. Capacity checks include the configured seating duration and whole-table limits. This is a single-process course prototype, not a concurrent booking service.

The React live dashboard is in `dashboard/`; see its README.

The read-only MCP server (`reservation_mcp.py`) uses the official MCP Python SDK over local stdio. The application supplies restaurant, database path, local date and phone region; the caller/LLM supplies only the phone tool argument. The server filters active upcoming reservations for that restaurant and normalizes contact numbers. Writes remain in the transactional Python workflow with confirmation, capacity checks, version checks and idempotency. MCP startup/lookup errors make no reservation changes. This is contact matching; no OTP verification is implemented.

## Structured handoffs

All production LLM calls use JSON Schema or declared tool-call schemas. Shared validation rejects wrong field types, unknown fields and invalid tool arguments; omitted nullable extraction slots normalize to explicit null without inferring values. Menu responses now use reply/tool_calls JSON, and policy responses retain grounded quotes JSON. MCP lookup has a typed found/reservations output schema; Parakeet HTTP text is validated before use.

Every turn response includes a versioned structured object with session_id, request_id, channel, input, output, routing, tools_called and calls. Local audit events record actual named inputs and outputs with matching call_id values. The owner conversation drawer exposes Inspect structured turn payload and Inspect structured inputs / outputs. Detailed payloads, including caller data and LLM messages, are recorded locally; existing LangSmith exports remain categorical summaries. Errors are structured and do not automatically retry business mutations. Binary microphone audio is represented by speech transcripts rather than embedded in JSON.


## Latest validation (October 27, 2026)

- Dashboard production build passed.
- Focused API, customer-feedback and voice adapter suite: 129 passed.
- Automated active-call regression with real speech services and simulated devices passed: dialing, transcript previews, distinct speaker styles, mute/unmute, no duplicate turns, playback echo guard and microphone cleanup.
- Full `pytest tests -q`: 511 passed, 11 failed, 11 subtests passed. Remaining failures are in decision-engine/protocol and latency-path expectations, reservation changes, restaurant clock and returning-customer tests. Several retain India-specific phone/timezone assumptions; others require reconciliation with availability-only or current menu/extraction behavior. The full suite is not currently green.
- Physical laptop microphone/speaker behavior is not established by the simulated-device regression.
