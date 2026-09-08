#!/bin/sh
# Keep asking a distributor about the parts nobody has asked about yet.
#
# 2026-09-04: 13,583 library parts had never been checked against any
# catalogue, and a sample said ~half are not carried — so the Library was
# offering buy-links that land on an empty page. `offers.derive_offers` now
# links only where something answered, which means the ONLY way a real part
# gets its link back is for this to run. At Mouser's 1000/day that is about
# two weeks for the backlog; unattended, so it is bounded and it stops.
#
# Actives are audited before passives (audit_mpns sorts that way): a sensor
# nobody stocks breaks a build, an unlisted 3.9k resistor has a hundred
# substitutes.
#
#   docker run -d --name auditor -e MOUSER_API_KEY=... \
#     -v /home/ec2-user/curation/library:/app/app/library \
#     aws-api sh /app/scripts/audit_loop.sh
set -u
BATCH="${AUDIT_BATCH:-850}"        # under the published 1000/day cap
REST="${AUDIT_REST:-86400}"        # one pass per day; the cap is per day

RETRY="${AUDIT_RETRY:-900}"        # a stalled batch is not a spent quota

while true; do
  echo "[audit_loop] $(date -u +%FT%TZ) starting a batch of ${BATCH}"
  out=$(python -m scripts.audit_mpns --only-unverified --limit "${BATCH}" --mark 2>&1)
  echo "$out"
  # audit_mpns STOPS the run when the catalogue stops answering, and records
  # nothing for that part — an unanswered call is not an answer (2026-08-23).
  # That is right, but it means one flaky read used to cost a whole day of
  # quota here: the first box run died 8 parts in on a read timeout and then
  # slept 24 h. Only a batch that actually ran to size has spent the day.
  done_n=$(printf '%s\n' "$out" | sed -n 's#^\([0-9][0-9]*\)/\([0-9][0-9]*\) confirmed.*#\2#p' | tail -1)
  if [ -n "${done_n}" ] && [ "${done_n}" -ge $((BATCH / 2)) ]; then
    echo "[audit_loop] $(date -u +%FT%TZ) audited ${done_n}, sleeping ${REST}s for the daily cap"
    sleep "${REST}"
  else
    echo "[audit_loop] $(date -u +%FT%TZ) batch ended early (${done_n:-0} audited) — retrying in ${RETRY}s"
    sleep "${RETRY}"
  fi
done
