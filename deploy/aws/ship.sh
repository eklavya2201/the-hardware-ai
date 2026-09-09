#!/bin/sh
# Build the studio where there is memory, then push the repo and the image to
# the box. Runs from a machine with Docker, from the repo root:
#
#   deploy/aws/ship.sh
#
# The box has two cores and no spare memory, so it never builds the studio.
set -e

HOST=${HOST:-ec2-user@3.111.115.126}
KEY=${KEY:-$HOME/.ssh/synqit-ec2.pem}
SSH="ssh -i $KEY -o StrictHostKeyChecking=no $HOST"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "=== building the studio image ==="
docker build -t synqit-studio:latest "$ROOT/app"

echo "=== shipping the image (about 450 MB, gzipped in flight) ==="
docker save synqit-studio:latest | gzip -1 | $SSH 'gunzip | sudo docker load'

echo "=== shipping the repo ==="
# Only what the box builds from and runs: engine source and the deploy files.
# .env files on the box are never overwritten.
tar czf - -C "$ROOT" \
  --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='node_modules' --exclude='.output' \
  backend deploy docs README.md \
  | $SSH 'mkdir -p ~/the-hardware-ai && tar xzf - -C ~/the-hardware-ai'

echo
echo "Shipped. Now, on the box:"
echo "  cd ~/the-hardware-ai/deploy/aws"
echo "  # create backend.env and studio.env (see studio.env.example), then:"
echo "  sudo ./deploy.sh"
