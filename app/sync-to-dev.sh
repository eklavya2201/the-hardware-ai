#!/bin/sh
# One-way sync: this repo is the source of truth, ../../cadam is the installed
# sandbox with node_modules where the dev server and checks run.
#
# One file flows the other way. src/routeTree.gen.ts is generated from the
# files in src/routes by the TanStack plugin, which only runs in the sandbox.
# The Docker build runs `tsc -b` against whatever is committed, so a stale tree
# here fails the build with "not assignable to keyof FileRoutesByPath" for
# every new route -- pull the regenerated one back.
set -e
DEV="$(dirname "$0")/../../cadam"
cp -r src shared scripts package.json "$DEV/"
if [ -f "$DEV/src/routeTree.gen.ts" ]; then
  sleep 2   # let the dev server regenerate after the copy above
  cp "$DEV/src/routeTree.gen.ts" src/routeTree.gen.ts
fi
echo "synced -> $DEV"
