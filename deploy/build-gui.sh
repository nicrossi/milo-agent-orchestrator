#!/usr/bin/env bash
# Build the Milo GUI (CRA) with prod URLs baked in, output to deploy/gui-build/.
# Run from the orchestrator repo root on your LOCAL machine (needs Node + the GUI clone):
#   DROPLET_IP=146.190.197.190 ./deploy/build-gui.sh
# Then scp the result to the Droplet:
#   scp -r deploy/gui-build root@$DROPLET_IP:/root/milo-agent-orchestrator/deploy/
set -euo pipefail

IP="${DROPLET_IP:?set DROPLET_IP, e.g. DROPLET_IP=146.190.197.190}"
GUI_SRC="${GUI_SRC:-../milo-gui/milo}"          # path to the CRA repo (intilauberer/milo)
OUT="$(pwd)/deploy/gui-build"

# CRA bakes these at build time (they are NOT read at runtime).
export REACT_APP_API_BASE_URL="https://api.${IP}.sslip.io"
export REACT_APP_WS_BASE_URL="wss://api.${IP}.sslip.io"
export REACT_APP_UPLOAD_API_URL="https://api.${IP}.sslip.io"
export REACT_APP_AUTH_PROVIDER="firebase"
export GENERATE_SOURCEMAP="false"               # don't ship JS source maps to prod
# Public Firebase web config (safe to ship in a client bundle).
export REACT_APP_FIREBASE_API_KEY="AIzaSyDC6IcO1nArB0TQK4vZvUAKeswiLfc6JCs"
export REACT_APP_FIREBASE_AUTH_DOMAIN="milo-auth-e1505.firebaseapp.com"
export REACT_APP_FIREBASE_PROJECT_ID="milo-auth-e1505"
export REACT_APP_FIREBASE_APP_ID="1:355820233071:web:f2866f404c27dafaa1889a"
export REACT_APP_FIREBASE_MESSAGING_SENDER_ID="355820233071"
export REACT_APP_FIREBASE_STORAGE_BUCKET="milo-auth-e1505.firebasestorage.app"

echo "Building GUI from ${GUI_SRC} for api.${IP}.sslip.io ..."
( cd "$GUI_SRC" && npm ci && npm run build )

rm -rf "$OUT"
cp -r "$GUI_SRC/build" "$OUT"
echo "Done -> $OUT  (now scp it to the Droplet)"
