# Frontdesk live owner dashboard

The default React page shows actual API chat logs, local bookings and staff messages. `/chat` is the customer interface; `/demo` preserves the original fictional preview.

Start the API in one terminal:

```powershell
cd C:\Maven\Projects\Week4\restaurant-receptionist
uv sync
uv run uvicorn chat_api:app --host 127.0.0.1 --port 8010
```

Start React in another:

```powershell
cd C:\Maven\Projects\Week4\restaurant-receptionist\dashboard
npm install
npm run dev
```

Open http://127.0.0.1:5173/. Send a message at `/chat`; after the turn completes it should appear within five seconds. Use Calls & audit to search/filter/export conversations and inspect routing and tool events. Reservations read SQLite and messages read the local JSONL file; evaluation fixtures are excluded. Owner reviews persist separately from call history. Build with `npm run build`.

The owner API is unauthenticated and intended for loopback only. Phone masking is for display; JSON exports contain original local records. Customer chat and the local voice client support confirmed reservation updates/cancellations using a reference and matching phone. The owner reservation table is read-only and shows retained cancellation records and change history. LangSmith trace IDs indicate queued traces, not independently verified delivery. See the parent README for limitations and data retention.
