#!/usr/bin/env bash
# Start the public HTTPS tunnel that Telegram loads the Mini App through.
#
# Why Cloudflare and not ngrok: the ngrok free tier sits a browser interstitial
# in front of every tunnel ("You are about to visit ...", ERR_NGROK_6024).
# curl passes straight through it, which makes the tunnel look perfectly
# healthy — but Telegram's WebView is a browser, gets the warning page instead
# of the app, and reports "WebView crashed". A Cloudflare quick tunnel has no
# interstitial, so what curl sees is what Telegram gets.
#
# Usage:  ./tunnel.sh
# Then:   copy the printed URL into MINI_APP_URL in ../.env and restart the bot.

set -euo pipefail

PORT=5180
CLOUDFLARED="${CLOUDFLARED:-$HOME/.local/bin/cloudflared}"
LOG=/tmp/opencode/cloudflared.log

# --- check the dev server is actually up on that port ------------------------
if ! curl -sf -o /dev/null "http://localhost:${PORT}/"; then
  echo "ERROR: nothing is serving on port ${PORT}." >&2
  echo "       Start it first, in another terminal:" >&2
  echo "         cd $(dirname "$0") && npm run dev" >&2
  exit 1
fi

if [ ! -x "$CLOUDFLARED" ]; then
  echo "ERROR: cloudflared not found at $CLOUDFLARED" >&2
  echo "       Install it with:" >&2
  echo "         curl -sL -o ~/.local/bin/cloudflared \\" >&2
  echo "           https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" >&2
  echo "         chmod +x ~/.local/bin/cloudflared" >&2
  exit 1
fi

# --- stop any previous tunnel ----------------------------------------------
pkill -f "cloudflared tunnel" 2>/dev/null || true
pkill -x ngrok 2>/dev/null || true
sleep 2

echo "Starting a Cloudflare quick tunnel to port ${PORT}..."
"$CLOUDFLARED" tunnel --url "http://localhost:${PORT}" --no-autoupdate >"$LOG" 2>&1 &

# --- wait for the URL, then report it --------------------------------------
URL=""
for _ in $(seq 1 30); do
  sleep 1
  URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$LOG" | head -1 || true)
  [ -n "$URL" ] && break
done

if [ -z "$URL" ]; then
  echo "ERROR: the tunnel did not come up. See $LOG" >&2
  exit 1
fi

# --- prove the app is what a browser actually receives ----------------------
# Without this check the tunnel looks healthy to curl while serving
# Telegram an error page — which is exactly how a broken tunnel hides.
BODY=$(curl -s -m 25 -A "Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 Chrome/107.0 Mobile Safari/537.36 TelegramAndroid" "$URL/")
if echo "$BODY" | grep -qi "ngrok\|ERR_NGROK\|not authorized"; then
  echo "ERROR: the tunnel is serving a browser interstitial, not the app." >&2
  exit 1
fi
if ! echo "$BODY" | grep -q "telegram-web-app.js"; then
  echo "WARNING: the page does not look like the Mini App." >&2
fi

echo
echo "  Public URL: $URL"
echo "  Forwarding: http://localhost:${PORT}"
echo
echo "Set this in ../.env (line 46), then restart the bot:"
echo
echo "  MINI_APP_URL=$URL"
echo
echo "Verify (expect 200):"
echo "  curl -s -o /dev/null -w '%{http_code}\\n' $URL/"