#!/bin/sh
# One-way sync: this repo is the source of truth, ../../cadam is the installed
# sandbox with node_modules where the dev server and checks run.
set -e
DEV="$(dirname "$0")/../../cadam"
cp -r src shared scripts package.json "$DEV/"
echo "synced -> $DEV"
