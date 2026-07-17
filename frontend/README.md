# Frontend Runbook

React + Vite UI for the theo-rag backend.

## Stack
- React 19
- TypeScript
- Zustand state store
- Vite
- Vitest (configured in project files)

## Key Paths
- App shell: `frontend/src/App.tsx`
- Store/state orchestration: `frontend/src/store/chatStore.ts`
- API/SSE client: `frontend/src/lib/api.ts`
- Message rendering: `frontend/src/components/MessageBubble.tsx`
- Evidence inspector: `frontend/src/components/EvidenceInspector.tsx`

## Run
```bash
cd frontend
npm ci
npm run dev
```

Default dev host: `0.0.0.0`.

## Build
```bash
npm run build
```

## API Base Resolution
The client resolves API base in this order:
1. `VITE_API_BASE`
2. same-origin
3. fallback to host on `:8000`

For remote deployments, set:
```bash
VITE_API_BASE=https://api.your-domain.com
```

## Streaming Contract
`streamQuery(...)` consumes SSE from `POST /api/query/stream` and handles payload types:
- `status`
- `citations`
- `token`
- `final`

Errors are mapped to typed categories (for example `busy`, `timeout`, `ollama_unavailable`, `stream_incomplete`).

## Session and Message Model
- Sessions are persisted locally.
- Assistant messages carry retrieval/generation metadata (`answer_mode`, `grounded`, `refusal`, `evidence`, `follow_ups`).
- Store manages abort/cancel and stream watchdog behavior.

## Frontend Verification
- Dev smoke:
  1. `npm run dev`
  2. open app
  3. submit a query
  4. confirm streaming updates and citations render
- Build smoke:
```bash
npm run build
```

## Troubleshooting
- `Failed to load collections/models`: backend unavailable or API base mismatch.
- CORS errors: ensure backend `API_ALLOWED_ORIGINS` includes frontend origin.
- Stream ends without final: inspect network for SSE interruptions and backend logs.
