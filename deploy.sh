#!/usr/bin/env bash
# Build the app and (re)start it as the "yfm" container, at http://127.0.0.1:8080.
#
#   ./deploy.sh
#
# Put your own reverse proxy or tunnel in front of it to reach it from elsewhere.
#
# Settings come from .env (GOOGLE_API_KEY or LIBRARY_DIR at least; see .env.example). The Drive
# library's local copy lives in the yfm-data volume, so a redeploy doesn't
# download it again.
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "ERROR: no .env here; it needs GOOGLE_API_KEY (see .env.example)" >&2; exit 1; }
grep -qE '^(GOOGLE_API_KEY|LIBRARY_DIR)=.' .env || { echo "ERROR: .env needs GOOGLE_API_KEY (Drive library) or LIBRARY_DIR" >&2; exit 1; }

docker build -t yfm .
docker rm -f yfm >/dev/null 2>&1 || true
docker run -d --name yfm --restart unless-stopped -p 127.0.0.1:8080:8080 \
  --env-file .env -v yfm-data:/app/data yfm >/dev/null

echo "Started. Waiting for the library to load…"
for _ in $(seq 60); do
  if out=$(curl -sf -u "x:$(sed -n 's/^APP_PASSWORD=//p' .env)" http://127.0.0.1:8080/api/library/status); then
    echo "$out"
    exit 0
  fi
  sleep 2
done
echo "ERROR: the site didn't answer within 2 minutes; see: docker logs yfm" >&2
exit 1
