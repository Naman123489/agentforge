#!/usr/bin/env bash
# Health check: which servers are up, a tiny test call to each, memory headroom.
set -uo pipefail
source "$(dirname "$0")/common.sh"

for pair in "$LIGHTNING_PORT:$LIGHTNING_NAME" "$SUPER_PORT:$SUPER_NAME"; do
  port=${pair%%:*}; name=${pair#*:}
  if curl -sf "http://127.0.0.1:$port/v1/models" >/dev/null; then
    out=$(curl -sf -w '\n%{time_total}' "http://127.0.0.1:$port/v1/chat/completions" \
      -H 'Content-Type: application/json' -d "{
      \"model\": \"$name\", \"max_tokens\": 20,
      \"messages\": [{\"role\": \"user\", \"content\": \"Reply with exactly: OK\"}],
      \"chat_template_kwargs\": {\"enable_thinking\": false}}")
    secs=$(tail -n1 <<<"$out")
    reply=$(head -n -1 <<<"$out" | python3 -c 'import json,sys; print(json.load(sys.stdin)["choices"][0]["message"]["content"].strip())')
    printf 'UP   :%s %-32s reply=%q  %ss\n' "$port" "$name" "$reply" "$secs"
  else
    printf 'DOWN :%s %s\n' "$port" "$name"
  fi
done
free -g | awk '/Mem:/ {print "memory: " $3 " GB used, " $7 " GB available of " $2}'
docker ps --filter name=af- --format 'container {{.Names}}: {{.Status}}'
