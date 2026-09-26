#!/bin/sh
# Continuously mutate always-changing.example via direct DDNS and the REST API.
# Each tick uses exactly one mode, cycling: DDNS → API → BOTH → DDNS → …
#   DDNS: update ddns-* via nsupdate
#   API:  update api-* via PUT /v1/.../rrsets
#   BOTH: update both-* via nsupdate then API
set -eu

ZONE="${ZONE:-always-changing.example.}"
BIND_HOST="${BIND_HOST:-bind}"
BIND_PORT="${BIND_PORT:-15353}"
API_BASE="${API_BASE:-http://dev:8000}"
API_KEY="${API_KEY:-}"
INTERVAL_SEC="${INTERVAL_SEC:-5}"
TSIG_NAME="${TSIG_NAME:-dns-api-key}"
TSIG_SECRET="${TSIG_SECRET:-K8vC2mP9nQ4rT6wX1yB3fG5hJ7kL0mN2pR4sU6vW8xY=}"
TSIG_ALG="${TSIG_ALG:-hmac-sha256}"
TTL="${TTL:-60}"

case "$API_BASE" in
  */) API_BASE="${API_BASE%/}" ;;
esac

ZONE_PATH="$ZONE"
case "$ZONE_PATH" in
  *.) ZONE_PATH="${ZONE_PATH%.}" ;;
esac

log() {
  echo "[zone-churn] $*"
}

wait_for_bind() {
  i=0
  while [ "$i" -lt 90 ]; do
    if dig @"${BIND_HOST}" -p "${BIND_PORT}" "${ZONE}" SOA +time=2 +tries=1 >/dev/null 2>&1; then
      log "BIND ready at ${BIND_HOST}:${BIND_PORT}"
      return 0
    fi
    i=$((i + 1))
    sleep 2
  done
  log "ERROR: BIND not ready after waiting"
  return 1
}

wait_for_api() {
  i=0
  while [ "$i" -lt 90 ]; do
    if curl -sf --max-time 2 "${API_BASE}/health" >/dev/null 2>&1; then
      log "API ready at ${API_BASE}"
      return 0
    fi
    i=$((i + 1))
    sleep 2
  done
  log "ERROR: API not ready after waiting at ${API_BASE}"
  return 1
}

ddns_set() {
  name="$1"
  addr="$2"
  nsupdate -y "${TSIG_ALG}:${TSIG_NAME}:${TSIG_SECRET}" <<EOF
server ${BIND_HOST} ${BIND_PORT}
zone ${ZONE}
update delete ${name}.${ZONE} A
update add ${name}.${ZONE} ${TTL} A ${addr}
send
EOF
}

api_set() {
  name="$1"
  addr="$2"
  body="{\"name\":\"${name}\",\"ttl\":${TTL},\"type\":\"A\",\"rdclass\":\"IN\",\"records\":[\"${addr}\"]}"
  if [ -n "$API_KEY" ]; then
    curl -sf --max-time 10 -X PUT \
      "${API_BASE}/v1/zones/${ZONE_PATH}/rrsets" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: ${API_KEY}" \
      -d "$body" \
      >/dev/null
  else
    curl -sf --max-time 10 -X PUT \
      "${API_BASE}/v1/zones/${ZONE_PATH}/rrsets" \
      -H "Content-Type: application/json" \
      -d "$body" \
      >/dev/null
  fi
}

# Drop and reload zone cache so API PUT prerequisites match BIND after external DDNS.
refresh_zone() {
  if [ -n "$API_KEY" ]; then
    curl -sf --max-time 10 -X DELETE \
      "${API_BASE}/v1/zones/${ZONE_PATH}/cache" \
      -H "X-API-Key: ${API_KEY}" \
      >/dev/null || true
    curl -sf --max-time 30 -X POST \
      "${API_BASE}/v1/zones/${ZONE_PATH}/refresh" \
      -H "X-API-Key: ${API_KEY}" \
      >/dev/null
  else
    curl -sf --max-time 10 -X DELETE \
      "${API_BASE}/v1/zones/${ZONE_PATH}/cache" \
      >/dev/null || true
    curl -sf --max-time 30 -X POST \
      "${API_BASE}/v1/zones/${ZONE_PATH}/refresh" \
      >/dev/null
  fi
}

log "starting ZONE=${ZONE} BIND=${BIND_HOST}:${BIND_PORT} API=${API_BASE} interval=${INTERVAL_SEC}s cycle=DDNS,API,BOTH"
wait_for_bind
wait_for_api

n=0
while true; do
  n=$((n + 1))
  octet=$(( (n % 200) + 20 ))
  addr="203.0.113.${octet}"
  # Cycle: 1=DDNS, 2=API, 3=BOTH
  phase=$(( (n - 1) % 3 ))

  status=ok
  case "$phase" in
    0)
      mode=DDNS
      for i in 1 2 3; do
        if ! ddns_set "ddns-${i}" "$addr"; then
          status=fail
        fi
      done
      ;;
    1)
      mode=API
      if ! refresh_zone; then
        log "tick=${n} warning: zone refresh failed"
      fi
      for i in 1 2 3; do
        if ! api_set "api-${i}" "$addr"; then
          status=fail
        fi
      done
      ;;
    2)
      mode=BOTH
      for i in 1 2 3; do
        if ! ddns_set "both-${i}" "$addr"; then
          status=fail
        fi
      done
      if ! refresh_zone; then
        log "tick=${n} warning: zone refresh failed"
      fi
      for i in 1 2 3; do
        if ! api_set "both-${i}" "$addr"; then
          status=fail
        fi
      done
      ;;
  esac

  log "tick=${n} mode=${mode} addr=${addr} status=${status}"
  sleep "$INTERVAL_SEC"
done
