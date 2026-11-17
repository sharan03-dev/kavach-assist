#!/usr/bin/env bash
# =============================================================================
# Lab 9.2 - Simulate a Network Partition (automated, timestamped)
#
#   Scenario A  isolate ONE secondary for 30 s
#               -> majority (primary + other secondary) keeps working;
#                  w:1 and w:majority succeed, w:all times out;
#                  after reconnecting, the member catches up.
#   Scenario B  isolate BOTH secondaries for 30 s
#               -> primary loses its majority: w:majority times out, the
#                  primary steps down (~electionTimeoutMillis = 10 s),
#                  the set becomes read-only; on reconnect an election runs,
#                  and the priority-2 member (mongo1) takes its role back.
#
#   lab9 9.2            run everything (about 3 minutes)
#   lab9 9.2 --step     pause before every step (for a live demonstration)
#
# Runs inside the lab9 container (lab9 9.2) or on the host (needs docker +
# python3 + pymongo + the hosts-file entry from Exercise 8).
# Everything is logged to results/ for the report.
#
# Improvements over the reference lab_02_simulate_partition.sh:
#   * the containers to cut off are chosen from rs.status() (real SECONDARYs);
#     the reference used `docker ps ... | head -1`, which may pick the PRIMARY;
#   * the partition lasts 30 s as the handout asks (reference: 15 s);
#   * writes use both w:1 and w:majority with wtimeout, outcomes are recorded;
#   * exact role-change times are read from the mongod logs;
#   * the network is ALWAYS reconnected, even after Ctrl+C (trap).
# =============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
export RESULTS_DIR="${RESULTS_DIR:-$ROOT/results}"
PY="${PYTHON:-python3}"
PARTITION_SECS="${PARTITION_SECS:-30}"
STEP="${STEP:-0}"
for a in "$@"; do
  case "$a" in
    --step) STEP=1 ;;
    --secs=*) PARTITION_SECS="${a#--secs=}" ;;
    *) echo "unknown option: $a (use --step or --secs=N)"; exit 2 ;;
  esac
done
mkdir -p "$RESULTS_DIR"

rsctl()    { "$PY" "$ROOT/setup/rsctl.py" "$@"; }
writer()   { "$PY" "$HERE/partition_writer.py" "$@"; }
event()    { rsctl event --source script "$@"; }
step()     { printf '\n%s\n%s\n%s\n' "$(printf '=%.0s' {1..76})" "$*" "$(printf '=%.0s' {1..76})"; }
pause()    { if [ "$STEP" = 1 ]; then read -r -p ">> press Enter to continue: $* " _ </dev/tty || true; fi; }
container_of() { echo "${1%%:*}"; }
port_of()      { echo "${1##*:}"; }

networks_of() {
  docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{println $k}}{{end}}' "$1" 2>/dev/null
}
is_connected() { networks_of "$1" | grep -qx "$NET"; }

disconnect() {
  if docker network disconnect "$NET" "$1"; then
    event PARTITION_START "docker network disconnect $NET $1"
  else
    echo "!! could not disconnect $1"; exit 1
  fi
}
reconnect() {
  if is_connected "$1"; then return 0; fi
  docker network connect --alias "$1" "$NET" "$1" && \
    event PARTITION_END "docker network connect $NET $1"
}
isolated_view() {   # $1 = host:port, $2 = output file
  # docker exec does not use the Docker network, so this works while the
  # member is cut off. --eval (not stdin) keeps mongosh from echoing prompts.
  docker exec "$(container_of "$1")" mongosh --port "$(port_of "$1")" --quiet \
    --eval "$(cat "$HERE/isolated_view.js")" 2>&1 | grep -v -E 'DeprecationWarning|trace-deprecation' | tee "$2"
}

NET=""
ALL_CONTAINERS=""
OBS_PID=""
cleanup() {
  [ -n "$NET" ] || return 0
  for c in $ALL_CONTAINERS; do reconnect "$c" >/dev/null 2>&1 || true; done
  if [ -n "$OBS_PID" ]; then kill "$OBS_PID" 2>/dev/null; wait "$OBS_PID" 2>/dev/null; fi
}
trap cleanup EXIT
trap 'echo; echo "Interrupted - reconnecting every member ..."; exit 130' INT TERM

# -----------------------------------------------------------------------------
step "Step 0 - Preconditions and container IDs"
command -v docker >/dev/null || { echo "docker CLI not found"; exit 1; }
rsctl wait-healthy --timeout 120 --preferred-primary || rsctl wait-healthy --timeout 60 || {
  echo "Replica set is not healthy - aborting."; exit 1; }

PRIMARY="$(rsctl primary)"
SECONDARIES="$(rsctl secondaries | tr '\n' ' ')"
read -r S1 S2 _ <<<"$SECONDARIES"
[ -n "${S1:-}" ] && [ -n "${S2:-}" ] || { echo "Need two SECONDARY members, got: $SECONDARIES"; exit 1; }
# isolate the highest-numbered secondary first (deterministic: normally mongo3)
if [[ "$S1" < "$S2" ]]; then TMP="$S1"; S1="$S2"; S2="$TMP"; fi
C0="$(container_of "$PRIMARY")"; C1="$(container_of "$S1")"; C2="$(container_of "$S2")"
ALL_CONTAINERS="$C0 $C1 $C2"
NET="${RS_NETWORK:-$(networks_of "$C0" | grep -v '^$' | head -1)}"

writer --reset
docker ps --filter "name=mongo" --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}' \
  | tee "$RESULTS_DIR/lab9_2_docker_ps.txt"
echo
echo "Network: $NET   PRIMARY: $PRIMARY   SECONDARIES: $S1, $S2"
rsctl status | tee "$RESULTS_DIR/lab9_2_status_before.txt"
echo
echo "Predictions (chapter-09 README, written before running):"
echo "  A: the isolated member will NOT disappear from rs.status(); the majority side keeps its"
echo "     PRIMARY, so w:1 and w:majority keep working; w:all needs every member -> times out."
echo "  B: the primary alone has no majority -> w:majority times out, the primary steps down"
echo "     after ~10 s, the set is read-only; after reconnecting an election restores a primary."

"$PY" "$HERE/partition_observer.py" --interval 0.5 > "$RESULTS_DIR/lab9_2_observer.log" 2>&1 &
OBS_PID=$!
echo
echo "Observer running in the background (pid $OBS_PID) -> results/lab9_2_observer.log"
echo "Tip: watch it live in another terminal:  docker exec -it lab9-runner lab9 observer"
sleep 3

step "Step 1 - Baseline: all members connected"
pause "baseline writes"
writer --phase baseline --rounds 3 --duration 0 --concerns 1,majority,all --interval 0.3
RUN_T0="$(rsctl now)"

# -----------------------------------------------------------------------------
step "Scenario A - isolate ONE secondary ($C1) for ${PARTITION_SECS}s"
pause "disconnect $C1"
event SCENARIO_START "A: primary=$PRIMARY; isolating $S1"
T0="$(rsctl now)"
disconnect "$C1"
echo "--- writes during the partition (first 14 s) ---"
writer --phase A_partition --duration 14 --concerns 1,majority,all --wtimeout 3000 --interval 0.5
echo
echo "--- $C1's OWN view (docker exec works without the network) ---"
isolated_view "$S1" "$RESULTS_DIR/lab9_2_isolated_view_A.txt"
echo
echo "--- majority side's view ---"
rsctl status | tee "$RESULTS_DIR/lab9_2_majority_view_A.txt"
echo
echo "--- more writes now that the primary has marked $C1 DOWN ---"
writer --phase A_partition_late --duration 8 --concerns 1,majority --wtimeout 3000 --interval 0.5
rsctl sleep-until "$("$PY" -c "print($T0 + $PARTITION_SECS)")"
pause "reconnect $C1"
reconnect "$C1"
echo "--- waiting for $C1 to catch up ---"
rsctl wait-caught-up "$S1" --timeout 120
rsctl wait-healthy --timeout 120
writer --verify

# -----------------------------------------------------------------------------
step "Scenario B - isolate BOTH secondaries ($C1, $C2): the primary loses its majority"
pause "disconnect $C1 and $C2"
PRIMARY="$(rsctl primary)"; C0="$(container_of "$PRIMARY")"
event SCENARIO_START "B: primary=$PRIMARY; isolating $S1 and $S2"
T0="$(rsctl now)"
disconnect "$C1"
disconnect "$C2"
echo "--- writes + reads while the primary is alone (watch it step down after ~10 s) ---"
writer --phase B_partition --duration 24 --concerns 1,majority --wtimeout 3000 --interval 0.5 --reads
echo
echo "--- $C0's view (the former primary, still reachable) ---"
rsctl status | tee "$RESULTS_DIR/lab9_2_view_B_primary_side.txt"
echo
echo "--- $C1's OWN view ---"
isolated_view "$S1" "$RESULTS_DIR/lab9_2_isolated_view_B.txt"
rsctl sleep-until "$("$PY" -c "print($T0 + $PARTITION_SECS)")"
pause "reconnect $C1 and $C2"
reconnect "$C1"
reconnect "$C2"
T_HEAL="$(rsctl now)"
echo "--- election after the partition heals ---"
rsctl wait-primary --timeout 90 --log
NEWP="$(rsctl primary)"
PREF="$(rsctl preferred)"
if [ "$NEWP" = "$PREF" ]; then
  event FORMER_PRIMARY_KEPT_ROLE "$PREF won the election directly (priority + newest oplog)"
else
  echo "--- $NEWP won the election; waiting for $PREF (highest priority) to take its role back ---"
  rsctl wait-primary-is "$PREF" --timeout 60 --since "$T_HEAL" --event PRIORITY_TAKEOVER || true
fi
rsctl wait-healthy --timeout 180
event SCENARIO_END "B: final primary=$(rsctl primary)"

echo
echo "--- exact role changes, read from the mongod logs (server timestamps) ---"
rsctl mongod-log --since "$RUN_T0" --echo
echo
echo "--- rollback evidence on $C0 (files appear only if writes were rolled back) ---"
docker exec "$C0" sh -c 'find /data/db/rollback -type f 2>/dev/null | head -20 || true' \
  | tee "$RESULTS_DIR/lab9_2_rollback_files.txt"
[ -s "$RESULTS_DIR/lab9_2_rollback_files.txt" ] || echo "(no rollback files)" | tee "$RESULTS_DIR/lab9_2_rollback_files.txt"

# -----------------------------------------------------------------------------
step "Step 4 - Which acknowledged / failed writes survived?"
writer --verify

kill "$OBS_PID" 2>/dev/null; wait "$OBS_PID" 2>/dev/null; OBS_PID=""
step "Timeline (script + observer + writer + mongod-log events)"
rsctl timeline
echo
echo "Lab 9.2 complete. Files in results/: lab9_2_*.txt, lab9_2_writes.jsonl, lab9_2_events_*.csv,"
echo "lab9_2_durability.json, lab9_2_timeline.md, lab9_2_observer.log/.csv"
