#!/bin/bash
# Sweep driver: one repetition.
# Usage: ./run_sweep_rep.sh <SPOT_RUN_ID>
# Runs num_tasks in {20, 40, ..., 140}, all seven strategies per run, with a
# Floodlight pre-flight check before each count (its device/link tables can
# go empty after ~2 h idle; pings repopulate them).
set -e
cd "$(dirname "$0")"
RID="$1"
[ -n "$RID" ] || { echo "usage: $0 <SPOT_RUN_ID>"; exit 2; }

preflight() {
  for attempt in 1 2 3; do
    dev=$(curl -s http://localhost:8080/wm/device/ | python3 -c "import json,sys; d=json.load(sys.stdin); print(sum(1 for x in (d['devices'] if isinstance(d,dict) else d) if x.get('ipv4')))" 2>/dev/null || echo 0)
    links=$(curl -s http://localhost:8080/wm/topology/links/json | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
    if [ "$dev" -ge 13 ] && [ "$links" -ge 5 ]; then return 0; fi
    echo "preflight attempt $attempt: devices=$dev links=$links -- healing with pings"
    for c in mn.mec2 mn.mec3 mn.mec4 mn.cloud1 mn.host1 mn.host2 mn.host3 mn.host4 mn.host5 mn.host6 mn.host7 mn.host8; do
      docker exec "$c" ping -c1 -W2 10.0.0.1 >/dev/null 2>&1 || true
    done
    docker exec mn.mec1 ping -c1 -W2 10.0.1.1 >/dev/null 2>&1 || true
    sleep 5
  done
  echo "preflight FAILED (devices=$dev links=$links) -- aborting sweep"
  return 1
}

for n in 20 40 60 80 100 120 140; do
  sed -i "s/^    num_tasks = [0-9]* #/    num_tasks = $n #/" network_graph.py
  grep -q "num_tasks = $n #" network_graph.py || { echo "knob edit failed for n=$n"; exit 3; }
  preflight
  echo "=== $RID num_tasks=$n start $(date +%T) ==="
  SPOT_RUN_ID="$RID" python3 network_graph.py > "/tmp/sweep_${RID}_${n}.log" 2>&1 \
    || { echo "RUN FAILED at n=$n -- see /tmp/sweep_${RID}_${n}.log"; exit 4; }
  echo "=== $RID num_tasks=$n done  $(date +%T) ==="
done
echo "SWEEP $RID COMPLETE"
