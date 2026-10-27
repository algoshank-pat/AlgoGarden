# Dashboard decisions

- 2026-10-25: Build a local React prototype as the first independently reviewable step; preserve the existing Python implementation.
- Use Vite and React with Lucide icons; no frontend service credentials.
- Demo fixtures are explicitly labeled. Existing evaluation files do not contain complete call transcripts or causal audit events; do not infer these from bookings alone.
- Audit timeline remains separate from owner review annotations. Browser localStorage is sufficient for prototype annotations, not production audit persistence.
- Future live integration uses a Python API and server-side LangSmith credentials. A trace link appears only when a real trace exists.
