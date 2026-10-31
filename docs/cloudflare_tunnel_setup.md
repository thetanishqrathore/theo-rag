# Cloudflare Tunnel: public access

The site is published at `https://bibleai.raeonlabs.com` through a Cloudflare
Tunnel: an outbound connection from this workstation to Cloudflare, so no
router ports are opened and the machine's IP is never exposed. The API serves
the web UI on the same origin, so one hostname covers both.

```
browser ──HTTPS──▶ Cloudflare edge ──tunnel (outbound from workstation)──▶ cloudflared ──▶ 127.0.0.1:8000 (FastAPI + UI)
```

## What is published

`ops/cloudflared/config.bibleai.yml` publishes only what the web UI uses:

| Path | Published |
| --- | --- |
| `/`, `/assets/*`, client routes | yes (web UI) |
| `/static/*` | yes (canonical evidence pages) |
| `/api/query/stream`, `/api/health`, `/api/collections`, `/api/models`, `/api/evidence/<id>` | yes |
| `/api/query` (blocking), `/api/models/pull`, `/api/models/keepalive`, `/api/enrich_citations`, `/api/stats`, `/docs`, `/redoc`, `/openapi.json` | **no** (404 at the tunnel) |

The API also refuses any model not in `API_ALLOWED_MODELS` (default: the
configured `OLLAMA_MODEL` / `SECONDARY_OLLAMA_MODEL`), so a visitor cannot load
another installed model onto the shared GPU.

## Setup (one time)

```bash
# 1. Install cloudflared (user space)
curl -fsSL -o ~/.local/bin/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x ~/.local/bin/cloudflared

# 2. Authorize the zone (opens a browser; pick raeonlabs.com)
cloudflared tunnel login

# 3. Create the tunnel and its DNS record
cloudflared tunnel create theo-rag
cloudflared tunnel route dns theo-rag bibleai.raeonlabs.com

# 4. Write ~/.cloudflared/config.yml and install the boot-time service
scripts/ops/install_tunnel.sh theo-rag bibleai.raeonlabs.com
```

Add the public origin to `API_ALLOWED_ORIGINS` in `.env` and restart the API
(`systemctl --user restart theo-rag`).

## Operate

| Task | Command |
| --- | --- |
| Status | `systemctl --user status theo-rag-tunnel` · `cloudflared tunnel info theo-rag` |
| Logs | `journalctl --user -u theo-rag-tunnel -f` |
| Take the site offline (local access unaffected) | `systemctl --user stop theo-rag-tunnel` |
| Disable at boot | `systemctl --user disable theo-rag-tunnel` |
| Remove completely | stop the service, delete the DNS record in Cloudflare, `cloudflared tunnel delete theo-rag` |

## Public access: know the limits

- **No login.** Anyone with the link can ask questions. To restrict access later,
  add a Cloudflare Zero Trust Access application for `bibleai.raeonlabs.com`
  (for example, email one-time PIN for an allowlist); no code change is needed.
- **One answer at a time.** `RAG_GENERATION_MAX_CONCURRENCY=1`: a second
  simultaneous visitor sees "Server is busy" and can retry. Answers take about
  10–20 s each.
- **Abuse protection** belongs at the edge: a Cloudflare WAF rate-limiting rule
  on `/api/query/stream` (for example 10 requests per minute per IP) is
  recommended.
- Chats are stored only in each visitor's browser.
- Streaming works through Cloudflare: reasoning heartbeats keep the connection
  active well under Cloudflare's 100-second idle limit.
