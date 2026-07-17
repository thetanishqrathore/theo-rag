# Cloudflare Tunnel Deployment (Frontend + API) - End to End

This runbook publishes the app securely to the internet without opening inbound router ports.

## Architecture
- `rag.raeonlabs.com` -> frontend (Vite preview/static server on `127.0.0.1:4173`)
- `api.raeonlabs.com` -> FastAPI backend on `127.0.0.1:8000`
- Cloudflare Zero Trust Access protects both hostnames.

## 1) Prerequisites
- Domain managed in Cloudflare.
- `cloudflared` installed on your host.
- Backend + frontend run locally.

## 2) Harden backend CORS
Set in `.env`:

```bash
API_ALLOWED_ORIGINS=https://rag.raeonlabs.com
```

## 3) Set frontend public API base
Create `frontend/.env.production`:

```bash
VITE_API_BASE=https://api.raeonlabs.com
```

Build frontend:

```bash
cd frontend
npm run build
```

Serve frontend build locally (private bind):

```bash
npm run preview -- --host 127.0.0.1 --port 4173
```

## 4) Run backend locally (private bind)

```bash
cd 
.venv/bin/python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

## 5) Create Cloudflare tunnel

Login:

```bash
cloudflared tunnel login
```

Create tunnel:

```bash
cloudflared tunnel create rag-prod
```

Capture the tunnel UUID from output.

## 6) Create tunnel config
Write `~/.cloudflared/config.yml`:

```yaml
tunnel: <TUNNEL_UUID>
credentials-file: ~/.cloudflared/<TUNNEL_UUID>.json

ingress:
  - hostname: rag.raeonlabs.com
    service: http://127.0.0.1:4173
  - hostname: api.raeonlabs.com
    service: http://127.0.0.1:8000
  - service: http_status:404
```

## 7) Route DNS records through tunnel

```bash
cloudflared tunnel route dns rag-prod rag.raeonlabs.com
cloudflared tunnel route dns rag-prod api.raeonlabs.com
```

## 8) Start tunnel

```bash
cloudflared tunnel run rag-prod
```

Optional service install (auto-start):

```bash
cloudflared service install
```

## 9) Add Cloudflare Access policies (critical)
Cloudflare Dashboard -> Zero Trust -> Access -> Applications:

Create two Self-hosted apps:
1. `rag.raeonlabs.com`
2. `api.raeonlabs.com`

For each app:
- Policy action: Allow
- Include: your email / identity provider group
- Optionally add country/IP restrictions

## 10) Verification
From an external network:

```bash
curl -I https://rag.raeonlabs.com
curl -I https://api.raeonlabs.com/api/health
```

In browser:
- open `https://rag.raeonlabs.com`
- run a query and confirm no localhost calls in DevTools Network tab
- confirm API requests go to `https://api.raeonlabs.com`

## 11) Troubleshooting

### UI loads, API fails
- check `frontend/.env.production` points to public API domain.
- rebuild frontend after env change.
- check API Access policy includes your user.

### CORS errors
- ensure `API_ALLOWED_ORIGINS` includes exact UI origin (scheme + host).
- restart backend after `.env` changes.

### Tunnel up but hostname not reachable
- verify DNS routes exist for both hostnames.
- check `cloudflared tunnel list` and logs.

### Backend reachable locally but not via tunnel
- ensure backend binds `127.0.0.1:8000`.
- confirm tunnel ingress points to same port.

## 12) Security checklist
- [ ] Backend bound to localhost only.
- [ ] Qdrant/Postgres/Ollama not publicly exposed.
- [ ] Access policy enabled on both frontend and API hostnames.
- [ ] CORS limited to UI origin.
- [ ] Strong account security (MFA) on Cloudflare.
