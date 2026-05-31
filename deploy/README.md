# Milo deployment (DigitalOcean)

Hybrid topology:

- **GUI** → DO **App Platform Static Site** (managed CDN + auto-TLS, free `*.ondigitalocean.app`).
- **Orchestrator** → DO **Droplet** (Basic 8GB / 4 vCPU) running `orchestrator` + `caddy` via docker-compose.
- **DB** stays on **AWS RDS** (Postgres + PGVector), reached over the public internet with SSL.

```
Browser ──HTTPS──▶ milo-gui-xxxxx.ondigitalocean.app   (App Platform Static Site)
   │  REST + WSS (cross-origin)
   ▼
api.<reserved-ip>.sslip.io ──▶ Caddy :443 ──▶ orchestrator :8000 ──SSL──▶ AWS RDS
```

Files in this dir:

| File | Purpose |
|------|---------|
| `docker-compose.yml` | orchestrator + caddy (run on the Droplet) |
| `Caddyfile` | auto-TLS reverse proxy for `api.<ip>.sslip.io` |
| `orchestrator.env.example` | template → copy to `orchestrator.env` (not committed) |
| `app-platform-gui.yaml` | App Platform spec for the GUI static site |

---

## A. Orchestrator on the Droplet

1. **Create Droplet**: Ubuntu 24.04, **Basic 8GB / 4 vCPU**, add your SSH key.
2. **Reserved IP**: assign one to the Droplet (stable `sslip.io` host across reboots). Call it `<ip>`.
3. **Install Docker + compose plugin** on the Droplet.
4. **AWS RDS security group**: add inbound `5432` from `<ip>/32` only.
5. **DO firewall**: inbound `22`, `80`, `443`; outbound all.
6. **Clone repo** on the Droplet, then prepare secrets:
   ```bash
   cp deploy/orchestrator.env.example deploy/orchestrator.env
   #   edit deploy/orchestrator.env  (DATABASE_URL, GOOGLE_API_KEY, AWS creds, S3 bucket)
   chmod 600 deploy/orchestrator.env
   mkdir -p deploy/secrets
   #   copy the Firebase service-account JSON to:
   #   deploy/secrets/firebase.json
   chmod 600 deploy/secrets/firebase.json
   ```
7. **Apply DB schema once** (network path is now open from the Droplet):
   ```bash
   psql "<your DATABASE_URL without ?sslmode>" -f migrations.sql   # idempotent
   ```
8. **Launch**:
   ```bash
   DROPLET_IP=<ip> docker compose -f deploy/docker-compose.yml up -d --build
   ```
   First build is slow (torch + model download baked into the image).

> **SSL note:** put SSL config in `DB_SSL` (e.g. `require`), **not** as `?sslmode=` in `DATABASE_URL` — asyncpg rejects `sslmode`. The app converts `DB_SSL` to an asyncpg SSL context. Use `verify-full` + `DB_SSL_ROOT_CERT=/secrets/rds-ca.pem` for full cert verification.

## B. GUI on App Platform

9. Edit `deploy/app-platform-gui.yaml`: set the GitHub repo, and replace `<ORCHESTRATOR_IP>` with `<ip>`. Then:
   ```bash
   doctl apps create --spec deploy/app-platform-gui.yaml
   ```
   Note the resulting domain, e.g. `milo-gui-xxxxx.ondigitalocean.app`.
10. **Backfill the GUI domain** into `deploy/orchestrator.env` (`ALLOWED_ORIGINS` and `FRONTEND_BASE_URL`), then reload the orchestrator:
    ```bash
    DROPLET_IP=<ip> docker compose -f deploy/docker-compose.yml up -d
    ```
    (Chicken-and-egg: the GUI domain only exists after step 9.)

## C. Firebase

11. Firebase console → Auth → Settings → **Authorized domains**: add `milo-gui-xxxxx.ondigitalocean.app` (browser sign-in runs on the GUI origin).

---

## Verify

```bash
docker compose -f deploy/docker-compose.yml ps          # orchestrator + caddy healthy
curl https://api.<ip>.sslip.io/healthcheck              # {"status":"healthy"}, valid cert
docker compose -f deploy/docker-compose.yml logs orchestrator | grep -i "RAG Process Pool started"  # "with 3 worker(s)"
```

Then in the browser: open the GUI URL → sign in → open an activity → send a text message. Expect streamed `chunk` frames over **wss** and a final `done` frame with `policy` metadata. Message an activity that has ingested files to confirm RAG retrieval (RDS PGVector reachable from DO).

## Swapping in a real domain later

Point DNS (`api.yourdomain.com`, `app.yourdomain.com`) at the Droplet IP / App Platform, then:
- `deploy/Caddyfile`: replace the site label with `api.yourdomain.com`.
- `app-platform-gui.yaml`: update `REACT_APP_API_BASE_URL` / `REACT_APP_WS_BASE_URL`, redeploy.
- `orchestrator.env`: update `ALLOWED_ORIGINS` / `FRONTEND_BASE_URL`, `docker compose up -d`.
- Firebase: add the new GUI domain to Authorized domains.
