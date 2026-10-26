#!/usr/bin/env bash
# Phase 2 manual smoke test for the sandbox images.
#
# Runs ONE gateway + RefundBot pair on a fresh internal-only Docker network with
# the hardening flags from ARCHITECTURE.md §7, prints the gateway's evidence
# summary and the agent's final response, checks that egress is blocked, and
# always cleans up. Synthetic data only; targets only the bundled local images.
# Superseded by DockerSandboxRunner (Phase 5).
#
# Usage: scripts/sandbox_smoke.sh [vulnerable|hardened] ["user message"]
# Env:   NOTE="..."   optional override of order A-101's note (untrusted content)
#        IMAGE_TAG    image tag (default 0.1.0)
set -euo pipefail

MODE="${1:-vulnerable}"
# Default kept in its own variable: macOS bash 3.2 mis-parses quotes inside ${x:-...}.
DEFAULT_MESSAGE="What's the status of order A-101?"
MESSAGE="${2:-$DEFAULT_MESSAGE}"
TAG="${IMAGE_TAG:-0.1.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SEED_FILE="$ROOT/seeds/synthetic_store.json"
AGENT_TIMEOUT_S=30

case "$MODE" in vulnerable|hardened) ;; *) echo "mode must be vulnerable|hardened" >&2; exit 2;; esac

ID="$(python3 -I -c 'import secrets; print(secrets.token_hex(4))')"
NONCE="$(python3 -I -c 'import secrets; print(secrets.token_hex(16))')"
NET="bbj-smoke-$ID"; GW="bbj-smoke-gw-$ID"; AG="bbj-smoke-agent-$ID"

HARDEN=(--read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m
        --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges
        --memory 256m --cpus 0.5 --pids-limit 64)

cleanup() {
  docker rm -f "$GW" "$AG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

SEED_B64="$(python3 -I - "$SEED_FILE" "${NOTE:-}" <<'PY'
import base64, json, sys
seed = json.load(open(sys.argv[1]))
if sys.argv[2]:
    next(o for o in seed["orders"] if o["order_id"] == "A-101")["note"] = sys.argv[2]
print(base64.b64encode(json.dumps(seed).encode()).decode())
PY
)"
TASK_B64="$(python3 -I - "$SEED_FILE" "$MODE" "$MESSAGE" <<'PY'
import base64, json, sys
seed = json.load(open(sys.argv[1]))
task = {"principal": "C-1001", "user_message": sys.argv[3], "mode": sys.argv[2],
        "system_context": seed.get("agent_context", {})}
print(base64.b64encode(json.dumps(task).encode()).decode())
PY
)"

echo "== network $NET (internal, no egress)"
docker network create --internal "$NET" >/dev/null

echo "== gateway"
docker run -d --name "$GW" --network "$NET" "${HARDEN[@]}" \
  -e BBJ_EXECUTION_NONCE="$NONCE" -e BBJ_SEED_STATE="$SEED_B64" \
  "bbj-gateway:$TAG" >/dev/null
for _ in $(seq 1 50); do
  docker logs "$GW" 2>/dev/null | grep -q '"gateway_ready"' && break
  sleep 0.1
done
docker logs "$GW" 2>/dev/null | grep -q '"gateway_ready"' || { echo "gateway not ready" >&2; docker logs "$GW" >&2; exit 1; }

echo "== agent (mode=$MODE)"
docker run -d --name "$AG" --network "$NET" "${HARDEN[@]}" \
  -e BBJ_GATEWAY_URL="http://$GW:8080" -e BBJ_TASK_INPUT="$TASK_B64" \
  "bbj-refundbot:$TAG" >/dev/null
for _ in $(seq 1 $((AGENT_TIMEOUT_S * 10))); do
  [ "$(docker inspect -f '{{.State.Running}}' "$AG")" = "false" ] && break
  sleep 0.1
done
if [ "$(docker inspect -f '{{.State.Running}}' "$AG")" = "true" ]; then
  echo "agent timed out after ${AGENT_TIMEOUT_S}s; killing" >&2
  docker kill "$AG" >/dev/null
fi
AGENT_EXIT="$(docker inspect -f '{{.State.ExitCode}}' "$AG")"

echo "== stop gateway (SIGTERM → state_after)"
docker kill --signal TERM "$GW" >/dev/null
for _ in $(seq 1 30); do
  [ "$(docker inspect -f '{{.State.Running}}' "$GW")" = "false" ] && break
  sleep 0.1
done

echo "== egress check (agent image on the same internal network)"
if docker run --rm --network "$NET" "${HARDEN[@]}" --entrypoint python "bbj-refundbot:$TAG" -I -c \
  "import socket; socket.create_connection(('1.1.1.1', 443), timeout=3)" >/dev/null 2>&1; then
  echo "EGRESS: REACHABLE  <-- isolation FAILURE" ; EGRESS=fail
else
  echo "EGRESS: blocked (expected)" ; EGRESS=ok
fi

echo "== results"
echo "agent exit code: $AGENT_EXIT"
echo "agent final line: $(docker logs "$AG" 2>/dev/null | tail -n 1)"
# Summariser kept in a quoted heredoc (no shell escaping); compatible with the
# macOS system python3 (3.9). Container output is parsed as data only.
SUMMARY="$(cat <<'PY'
import json, sys
nonce = sys.argv[1]
events = [json.loads(l) for l in sys.stdin if l.strip()]
bad = [e for e in events if e.get("bbj") != nonce]
print("gateway events: %d; nonce mismatches: %d" % (len(events), len(bad)))
print("event types: %s" % [e["type"] for e in events])
for e in events:
    if e["type"] == "tool_call":
        print("  call %s: %s %s -> %s side_effect=%s" % (
            e["call_seq"], e["tool"], json.dumps(e["args"]), e["outcome"], e["side_effect"]))
snaps = {e["phase"]: e["state"] for e in events if e["type"] == "state_snapshot"}
if {"before", "after"} <= set(snaps):
    before, after = snaps["before"], snaps["after"]
    for t in ("refunds", "outbox"):
        print("  %s: %d -> %d rows" % (t, len(before[t]), len(after[t])))
        for row in after[t][len(before[t]):]:
            print("    + " + json.dumps(row))
    changed = [a["order_id"] for a, b in zip(after["orders"], before["orders"]) if a != b]
    print("  orders changed: %s" % changed)
else:
    print("  MISSING state snapshot(s): %s" % sorted(snaps))
PY
)"
docker logs "$GW" 2>/dev/null | python3 -I -c "$SUMMARY" "$NONCE"
[ "$EGRESS" = ok ]
