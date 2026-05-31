#!/bin/sh
# SearXNG entrypoint — direct connections by default.
# Set SEARXNG_USE_PROXY=true to inject CloudProxy IPs into outgoing config.

set -u

SETTINGS="/etc/searxng/settings.yml"
CLOUDPROXY_URL="${CLOUDPROXY_URL:-https://cloudproxy.grabcash.in}"
SYNC_INTERVAL="${PROXY_SYNC_INTERVAL:-90}"
USE_PROXY="${SEARXNG_USE_PROXY:-false}"

inject_proxies() {
  if [ "$USE_PROXY" != "true" ]; then
    echo "[proxy-sync] Proxy disabled (SEARXNG_USE_PROXY!=true) — using direct connections"
    return 0
  fi

  RAW=$(wget -qO- --no-check-certificate "$CLOUDPROXY_URL/" 2>/dev/null)
  if [ -z "$RAW" ]; then
    echo "[proxy-sync] CloudProxy unreachable — using direct connections"
    return 1
  fi

  PROXY_URLS=$(echo "$RAW" | python3 -c "
import sys, json
data = json.load(sys.stdin)
ips = data.get('ips', [])
for ip in ips:
    print(ip)
" 2>/dev/null)

  if [ -z "$PROXY_URLS" ]; then
    echo "[proxy-sync] No proxies from CloudProxy — using direct connections"
    return 1
  fi

  COUNT=$(echo "$PROXY_URLS" | wc -l | tr -d ' ')

  TMPFILE=$(mktemp)
  python3 -c "
import sys, json

settings_text = open('$SETTINGS').read()
raw = '''$RAW'''
data = json.loads(raw)
ips = data.get('ips', [])

lines = settings_text.split('\n')
new_lines = []
skip = False
for line in lines:
    stripped = line.lstrip()
    if stripped.startswith('proxies:'):
        skip = True
        continue
    if skip:
        if stripped.startswith('- ') or stripped.startswith('all://'):
            continue
        else:
            skip = False
    new_lines.append(line)

result = []
in_outgoing = False
outgoing_done = False
for i, line in enumerate(new_lines):
    result.append(line)
    stripped = line.lstrip()
    if stripped == 'outgoing:' or stripped.startswith('outgoing:'):
        in_outgoing = True
    elif in_outgoing and not outgoing_done:
        if stripped and not line.startswith(' ') and not line.startswith('\t'):
            result.pop()
            result.append('  proxies:')
            result.append('    all://:')
            for ip in ips:
                result.append(f'      - {ip}')
            result.append(line)
            outgoing_done = True

if in_outgoing and not outgoing_done:
    result.append('  proxies:')
    result.append('    all://:')
    for ip in ips:
        result.append(f'      - {ip}')

open('$TMPFILE', 'w').write('\n'.join(result))
print(f'{len(ips)} proxies injected')
" 2>&1

  if [ $? -eq 0 ] && [ -s "$TMPFILE" ]; then
    cat "$TMPFILE" > "$SETTINGS"
    rm -f "$TMPFILE"
    echo "[proxy-sync] $COUNT proxy(ies) active"
    return 0
  else
    rm -f "$TMPFILE"
    echo "[proxy-sync] Injection failed — using direct connections"
    return 1
  fi
}

background_sync() {
  while true; do
    sleep "$SYNC_INTERVAL"
    echo "[proxy-sync] Refreshing proxies..."
    inject_proxies
  done
}

echo "[proxy-sync] Initializing (USE_PROXY=$USE_PROXY)..."
inject_proxies

if [ "$USE_PROXY" = "true" ]; then
  background_sync &
fi

exec /usr/local/searxng/entrypoint.sh
