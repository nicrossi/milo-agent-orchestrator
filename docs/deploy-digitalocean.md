# Deploy Milo to DigitalOcean — step by step

End-to-end runbook for deploying the Milo stack to DigitalOcean.

## Topology

```
Browser ──HTTPS──▶ milo-tutor.ddns.net  (Caddy static file_server on the Droplet)
   │  REST + WSS (same Caddy, api.<ip> host)
   ▼
api.<reserved-ip>.sslip.io ──▶ Caddy :443 ──▶ orchestrator :8000 ──SSL──▶ AWS RDS
```

- **Orchestrator** → DO **Droplet** (Basic 8GB / 4 vCPU), runs `orchestrator` + `caddy` via docker-compose.
- **GUI** → Create React App build, served as static files by the same Caddy container (mounted from `deploy/gui-build`). Built locally and `scp`'d to the Droplet.
- **DB** → stays on **AWS RDS** (Postgres + PGVector), reached over the public internet with SSL.
- **Caddy** → auto-TLS (Let's Encrypt) for both the API host (`api.<ip>.sslip.io`) and the GUI host (`milo-tutor.ddns.net`, `app.<ip>.sslip.io`).

> Alternative GUI hosting on **App Platform Static Site** (managed CDN) is described at the end. The default path below serves the GUI from the Droplet's Caddy.

Relevant files (`deploy/`):

| File | Purpose |
|------|---------|
| `docker-compose.yml` | orchestrator + caddy services (run on the Droplet) |
| `Caddyfile` | auto-TLS reverse proxy + static GUI host |
| `orchestrator.env.example` | template → copy to `orchestrator.env` (not committed) |
| `build-gui.sh` | build the CRA GUI locally with prod URLs baked in |
| `app-platform-gui.yaml` | App Platform spec (alternative GUI hosting) |

---

## Prerequisites

- DigitalOcean account + `doctl` CLI authenticated (`doctl auth init`).
- SSH key uploaded to DO.
- AWS RDS Postgres instance with PGVector, plus its connection string.
- Firebase project (service-account JSON for the orchestrator).
- Google Gemini API key (or Vertex AI service account).
- AWS S3 bucket + IAM access key for activity files.
- Local: Node + the GUI repo clone (CRA, `intilauberer/milo`) for building the frontend.

---

## A. Provision the Droplet

1. **Create Droplet**: Ubuntu 24.04, **Basic 8GB / 4 vCPU**, add your SSH key.
2. **Reserved IP**: assign one to the Droplet → stable `sslip.io` host across reboots. Call it `<ip>` (current prod: `146.190.197.190`).
3. **Install Docker + compose plugin** on the Droplet:
   ```bash
   ssh root@<ip>
   curl -fsSL https://get.docker.com | sh
   docker compose version   # confirm the compose plugin is present
   ```
4. **AWS RDS security group**: add inbound `5432` from `<ip>/32` only.
5. **DO firewall**: inbound `22`, `80`, `443`; outbound all.
6. **(Optional) No-IP hostname**: point `milo-tutor.ddns.net` at `<ip>` so Caddy can issue a Let's Encrypt cert for it. Skip if using only the `sslip.io` host.

---

## B. Configure secrets on the Droplet

7. **Clone the repo** on the Droplet:
   ```bash
   git clone https://github.com/nicrossi/milo-agent-orchestrator.git
   cd milo-agent-orchestrator
   ```
8. **Prepare the env file**:
   ```bash
   cp deploy/orchestrator.env.example deploy/orchestrator.env
   # edit deploy/orchestrator.env — fill in:
   #   DATABASE_URL  (no ?sslmode= — see SSL note below)
   #   DB_SSL=require
   #   GOOGLE_API_KEY
   #   S3_ACTIVITY_FILES_BUCKET, AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
   #   ALLOWED_ORIGINS, FRONTEND_BASE_URL  (the GUI origin, e.g. https://milo-tutor.ddns.net)
   chmod 600 deploy/orchestrator.env
   ```
9. **Copy the service-account secrets** (not committed):
   ```bash
   mkdir -p deploy/secrets
   # from your local machine:
   #   scp firebase.json root@<ip>:/root/milo-agent-orchestrator/deploy/secrets/firebase.json
   #   scp gcp.json      root@<ip>:.../deploy/secrets/gcp.json   # only if using Vertex
   chmod 600 deploy/secrets/*.json
   ```

> **DB SSL note:** put SSL config in `DB_SSL` (e.g. `require`), **not** as `?sslmode=` in `DATABASE_URL` — asyncpg rejects `sslmode`. The app converts `DB_SSL` to an asyncpg SSL context. For full cert verification use `DB_SSL=verify-full` + `DB_SSL_ROOT_CERT=/secrets/rds-ca.pem`.

---

## C. Apply the DB schema (once)

10. With the Droplet → RDS network path open (step 4), apply migrations. `migrations.sql` is idempotent:
    ```bash
    psql "<DATABASE_URL without ?sslmode>" -f migrations.sql
    ```

---

## D. Build the GUI and ship it to the Droplet

11. **On your LOCAL machine** (needs Node + the GUI clone), build with prod URLs baked in:
    ```bash
    DROPLET_IP=<ip> ./deploy/build-gui.sh
    # GUI_SRC defaults to ../milo-gui/milo — override if your clone is elsewhere
    ```
    CRA bakes `REACT_APP_*` at build time. The script sets the API/WS base URLs to `api.<ip>.sslip.io` and the public Firebase web config. Output → `deploy/gui-build/`.

    > If the GUI is served at `milo-tutor.ddns.net` but the API at `api.<ip>.sslip.io`, the baked `REACT_APP_API_BASE_URL` / `REACT_APP_WS_BASE_URL` must point at the API host. That is already the case — `build-gui.sh` points them at `api.<ip>.sslip.io`.

12. **Copy the build to the Droplet**:
    ```bash
    scp -r deploy/gui-build root@<ip>:/root/milo-agent-orchestrator/deploy/
    ```

---

## E. Launch

13. **On the Droplet**, bring up both services. `DROPLET_IP` is injected into the Caddyfile:
    ```bash
    DROPLET_IP=<ip> docker compose -f deploy/docker-compose.yml up -d --build
    ```
    First build is slow — torch (CPU) + the embedding model are baked into the image.

14. Caddy auto-issues Let's Encrypt certs for `api.<ip>.sslip.io`, `milo-tutor.ddns.net`, and `app.<ip>.sslip.io` on first request. DNS/`sslip.io` must already resolve to `<ip>`.

---

## F. Firebase authorized domains

15. Firebase console → Auth → Settings → **Authorized domains**: add the GUI origin (`milo-tutor.ddns.net` and/or `app.<ip>.sslip.io`). Browser sign-in runs on the GUI origin.

---

## Verify

```bash
docker compose -f deploy/docker-compose.yml ps          # orchestrator + caddy healthy
curl https://api.<ip>.sslip.io/healthcheck              # {"status":"healthy"}, valid cert
docker compose -f deploy/docker-compose.yml logs orchestrator | grep -i "RAG Process Pool started"  # "with 3 worker(s)"
curl -I https://milo-tutor.ddns.net                     # 200, valid cert, serves index.html
```

Then in the browser: open the GUI URL → sign in → open an activity → send a text message. Expect streamed `chunk` frames over **wss** and a final `done` frame with `policy` metadata. Message an activity with ingested files to confirm RAG retrieval (RDS PGVector reachable from DO).

---

## Redeploys

- **Orchestrator code change**: `git pull` on the Droplet, then
  `DROPLET_IP=<ip> docker compose -f deploy/docker-compose.yml up -d --build`.
- **GUI change**: rebuild locally (`DROPLET_IP=<ip> ./deploy/build-gui.sh`), `scp -r deploy/gui-build root@<ip>:.../deploy/`, then `docker compose ... up -d` (no rebuild needed — Caddy serves the mounted dir; restart caddy to be safe).
- **Env change** (`orchestrator.env`): edit on the Droplet, `docker compose ... up -d`.

---

## Alternative: GUI on App Platform Static Site

Managed CDN + auto-TLS on a free `*.ondigitalocean.app` domain instead of serving from the Droplet.

1. Edit `deploy/app-platform-gui.yaml`: set the GitHub repo/branch and replace the API IP (`146.190.197.190`) with `<ip>` in the `REACT_APP_*` build envs.
2. Create the app:
   ```bash
   doctl apps create --spec deploy/app-platform-gui.yaml
   doctl apps update <APP_ID> --spec deploy/app-platform-gui.yaml   # after later edits
   ```
   Note the domain, e.g. `milo-gui-xxxxx.ondigitalocean.app`.
3. **Backfill the GUI domain** into `deploy/orchestrator.env` (`ALLOWED_ORIGINS`, `FRONTEND_BASE_URL`), then `docker compose ... up -d`. (Chicken-and-egg: the domain only exists after step 2.)
4. Add the App Platform domain to Firebase **Authorized domains**.

In this mode the Droplet Caddy only proxies the API; the GUI block in the Caddyfile and the `gui-build` mount are unused.

---

## Swapping in a real domain later

Point DNS (`api.yourdomain.com`, `app.yourdomain.com`) at the Droplet IP, then:

- `deploy/Caddyfile`: replace the site labels with your real hostnames.
- `deploy/build-gui.sh` (or `app-platform-gui.yaml`): update `REACT_APP_API_BASE_URL` / `REACT_APP_WS_BASE_URL`, rebuild + redeploy the GUI.
- `orchestrator.env`: update `ALLOWED_ORIGINS` / `FRONTEND_BASE_URL`, `docker compose ... up -d`.
- Firebase: add the new GUI domain to Authorized domains.
</content>
</invoke>
