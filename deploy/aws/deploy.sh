#!/bin/bash
# Cut synqit.in over to the studio. Runs ON the box, from ~/the-hardware-ai.
#
#   cd ~/the-hardware-ai/deploy/aws && sudo ./deploy.sh
#
# Idempotent: safe to run again after fixing something. It refuses rather than
# guessing if the environment is not ready.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n=== %s ===\n' "$1"; }

say "checking the environment"
for f in backend.env studio.env; do
  [ -f "$f" ] || { echo "missing $f (see studio.env.example)"; exit 1; }
done
for key in STUDIO_SERVICE_SECRET; do
  grep -q "^$key=.\+" backend.env || { echo "$key is empty in backend.env"; exit 1; }
  grep -q "^$key=.\+" studio.env  || { echo "$key is empty in studio.env";  exit 1; }
done
# The two halves of the single-account exchange must agree, or every sign-in
# fails with the engine refusing the session.
a=$(grep "^STUDIO_SERVICE_SECRET=" backend.env | cut -d= -f2-)
b=$(grep "^STUDIO_SERVICE_SECRET=" studio.env  | cut -d= -f2-)
[ "$a" = "$b" ] || { echo "STUDIO_SERVICE_SECRET differs between backend.env and studio.env"; exit 1; }
docker image inspect synqit-studio:latest >/dev/null 2>&1 || {
  echo "synqit-studio:latest is not loaded. Build it elsewhere and ship it:"
  echo "  docker save synqit-studio:latest | gzip | ssh ... 'gunzip | sudo docker load'"
  exit 1
}
docker image inspect aws-api:latest >/dev/null 2>&1 || {
  echo "aws-api:latest is missing; Dockerfile.overlay needs it as its base."
  exit 1
}

say "freeing memory"
# The auditor loops on Mouser 403s and records nothing; staging is not needed
# for the cut-over. Restart later with: docker start auditor aws-api-staging-1
docker stop auditor aws-api-staging-1 >/dev/null 2>&1 || true
free -m | head -2

say "building the engine overlay"
docker compose build api

say "stopping the old stack"
# Frees 80/443 and releases the database volume. Its files stay on the box.
# NOT `~`: this runs under sudo, where ~ is /root and the cd silently fails,
# leaving the old Caddy holding 443 and two engines on one SQLite file.
OLD_STACK="/home/${SUDO_USER:-ec2-user}/synqit/deploy/aws"
if [ -d "$OLD_STACK" ]; then
  ( cd "$OLD_STACK" && docker compose down ) || true
else
  echo "no old stack at $OLD_STACK — nothing to stop"
fi

say "starting engine, studio and caddy"
# Images are pre-built, so the build args are unused; declare them anyway so
# compose does not warn about unset variables.
export VITE_SUPABASE_URL="${VITE_SUPABASE_URL:-}" VITE_SUPABASE_ANON_KEY="${VITE_SUPABASE_ANON_KEY:-}"
docker compose up -d

say "waiting for health"
for i in $(seq 1 30); do
  api_ok=$(docker compose exec -T api sh -c 'command -v curl >/dev/null && curl -sf localhost:8000/api/health >/dev/null && echo yes' 2>/dev/null || true)
  studio_ok=$(docker compose exec -T studio sh -c 'command -v curl >/dev/null && curl -sf localhost:3000/ >/dev/null && echo yes' 2>/dev/null || true)
  [ "$api_ok" = yes ] && [ "$studio_ok" = yes ] && break
  sleep 4
done

say "state"
docker compose ps
free -m | head -2

cat <<'NEXT'

Containers are up. The last step is DNS, and it is the one that flips the
front door:

  synqit.in      A -> 3.111.115.126   (currently an alias to CloudFront)
  www.synqit.in  A -> 3.111.115.126

Caddy issues both certificates on the first request after that resolves.

To roll back:
  cd ~/the-hardware-ai/deploy/aws && sudo docker compose down
  cd ~/synqit/deploy/aws && sudo docker compose up -d
  ...and point synqit.in back at CloudFront E1R85FGGRKPESM.
NEXT
