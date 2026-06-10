# Milo deployment (DigitalOcean)

> Full step-by-step runbook: [`docs/deploy-digitalocean.md`](../docs/deploy-digitalocean.md). This file is the quick reference.

Topology:

- **Orchestrator** → DO **Droplet** (Basic 8GB / 4 vCPU) running `orchestrator` + `caddy` via docker-compose.
- **GUI** → CRA build served as static files by the same **Caddy** container (mounted from `deploy/gui-build`). Built locally, `scp`'d to the Droplet.
- **DB** → stays on **AWS RDS** (Postgres + PGVector), reached over the public internet with SSL.

```
Browser ──HTTPS──▶ milo-tutor.ddns.net  (Caddy static file_server on the Droplet)
   │  REST + WSS (same Caddy, api.<ip> host)
   ▼
api.<reserved-ip>.sslip.io ──▶ Caddy :443 ──▶ orchestrator :8000 ──SSL──▶ AWS RDS
```

> Alternative: host the GUI on a DO **App Platform Static Site** (managed CDN + auto-TLS, free `*.ondigitalocean.app`). See section D.

Files in this dir:

| File | Purpose |
|------|---------|
| `docker-compose.yml` | orchestrator + caddy (run on the Droplet) |
| `Caddyfile` | auto-TLS reverse proxy + static GUI host |
| `orchestrator.env.example` | template → copy to `orchestrator.env` (not committed) |
| `build-gui.sh` | build the CRA GUI locally with prod URLs baked in |
| `app-platform-gui.yaml` | App Platform spec (alternative GUI hosting) |

---

## A. Droplet + orchestrator

1. **Create Droplet**: Ubuntu 24.04, **Basic 8GB / 4 vCPU**, add your SSH key.
2. **Reserved IP**: assign one to the Droplet (stable `sslip.io` host across reboots). Call it `<ip>`.
3. **Install Docker + compose plugin** on the Droplet.
4. **AWS RDS security group**: add inbound `5432` from `<ip>/32` only.
5. **DO firewall**: inbound `22`, `80`, `443`; outbound all.
6. **(Optional) No-IP**: point `milo-tutor.ddns.net` at `<ip>` so Caddy can issue a cert for it.
7. **Clone repo** on the Droplet, then prepare secrets:
   ```bash
   cp deploy/orchestrator.env.example deploy/orchestrator.env
   #   edit deploy/orchestrator.env  (DATABASE_URL, GOOGLE_API_KEY, AWS creds, S3 bucket,
   #                                  ALLOWED_ORIGINS, FRONTEND_BASE_URL = the GUI origin)
   chmod 600 deploy/orchestrator.env
   mkdir -p deploy/secrets
   #   copy the Firebase service-account JSON to deploy/secrets/firebase.json
   #   (and deploy/secrets/gcp.json if using Vertex)
   chmod 600 deploy/secrets/*.json
   ```
8. **Apply DB schema once** (network path now open from the Droplet):
   ```bash
   psql "<your DATABASE_URL without ?sslmode>" -f migrations.sql   # idempotent
   ```

> **SSL note:** put SSL config in `DB_SSL` (e.g. `require`), **not** as `?sslmode=` in `DATABASE_URL` — asyncpg rejects `sslmode`. The app converts `DB_SSL` to an asyncpg SSL context. Use `verify-full` + `DB_SSL_ROOT_CERT=/secrets/rds-ca.pem` for full cert verification.

## B. GUI (served by Caddy on the Droplet)

9. **On your LOCAL machine** (needs Node + the GUI clone), build with prod URLs baked in, then ship it:
   ```bash
   DROPLET_IP=<ip> ./deploy/build-gui.sh
   scp -r deploy/gui-build root@<ip>:/root/milo-agent-orchestrator/deploy/
   ```
   CRA bakes `REACT_APP_*` at build time; the script points the API/WS base URLs at `api.<ip>.sslip.io`.

## C. Launch

10. **On the Droplet**, bring up both services (`DROPLET_IP` is injected into the Caddyfile):
    ```bash
    DROPLET_IP=<ip> docker compose -f deploy/docker-compose.yml up -d --build
    ```
    First build is slow (torch CPU + embedding model baked into the image). Caddy auto-issues Let's Encrypt certs for `api.<ip>.sslip.io`, `milo-tutor.ddns.net`, and `app.<ip>.sslip.io`.

## D. Alternative — GUI on App Platform

Skip B and the GUI block of the Caddyfile/compose mount; instead:

11. Edit `deploy/app-platform-gui.yaml`: set the GitHub repo, replace the API IP with `<ip>`. Then:
    ```bash
    doctl apps create --spec deploy/app-platform-gui.yaml
    ```
    Note the resulting domain, e.g. `milo-gui-xxxxx.ondigitalocean.app`.
12. **Backfill the GUI domain** into `deploy/orchestrator.env` (`ALLOWED_ORIGINS`, `FRONTEND_BASE_URL`), then reload:
    ```bash
    DROPLET_IP=<ip> docker compose -f deploy/docker-compose.yml up -d
    ```

## E. Firebase

13. Firebase console → Auth → Settings → **Authorized domains**: add the GUI origin
    (`milo-tutor.ddns.net` / `app.<ip>.sslip.io`, or the App Platform domain). Browser sign-in runs on the GUI origin.

---

## Verify

```bash
docker compose -f deploy/docker-compose.yml ps          # orchestrator + caddy healthy
curl https://api.<ip>.sslip.io/healthcheck              # {"status":"healthy"}, valid cert
docker compose -f deploy/docker-compose.yml logs orchestrator | grep -i "RAG Process Pool started"  # "with 3 worker(s)"
curl -I https://milo-tutor.ddns.net                     # 200, valid cert (Droplet-served GUI)
```

Then in the browser: open the GUI URL → sign in → open an activity → send a text message. Expect streamed `chunk` frames over **wss** and a final `done` frame with `policy` metadata. Message an activity that has ingested files to confirm RAG retrieval (RDS PGVector reachable from DO).

## Swapping in a real domain later

Point DNS (`api.yourdomain.com`, `app.yourdomain.com`) at the Droplet IP, then:
- `deploy/Caddyfile`: replace the site labels with your real hostnames.
- `deploy/build-gui.sh` (or `app-platform-gui.yaml`): update `REACT_APP_API_BASE_URL` / `REACT_APP_WS_BASE_URL`, rebuild + redeploy the GUI.
- `orchestrator.env`: update `ALLOWED_ORIGINS` / `FRONTEND_BASE_URL`, `docker compose up -d`.
- Firebase: add the new GUI domain to Authorized domains.
</content>
</invoke>
