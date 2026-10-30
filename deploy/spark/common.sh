# Shared settings for the DGX Spark scripts. Sourced, not run.
# Image and flags follow the NVIDIA model cards' "DGX Spark (GB10)" recipes (Oct 2026).

VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.27.1}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"
# 127.0.0.1 = only reachable from the Spark itself (use an SSH tunnel from your laptop).
# Set BIND=0.0.0.0 to expose on your LAN (anyone on the network can then use the GPU).
BIND="${BIND:-127.0.0.1}"

SUPER_MODEL="nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4"
SUPER_MTP="nvidia/Nemotron-3-Super-120B-A12B-BF16-MTPv2"
SUPER_NAME="nvidia/nemotron-3-super"
SUPER_PORT=8000

LIGHTNING_MODEL="nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4"
LIGHTNING_DSPARK="nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark"
LIGHTNING_NAME="nvidia/nemotron-3.5-lightning"
LIGHTNING_PORT=8001

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

wait_ready() {  # wait_ready <port> <name> [timeout_s]
  local port=$1 name=$2 timeout=${3:-1800} t=0
  echo "waiting for $name on :$port (first start loads weights; Super can take ~10 min)..."
  until curl -sf "http://127.0.0.1:$port/v1/models" >/dev/null; do
    if ! docker ps --format '{{.Names}}' | grep -qx "$name"; then
      echo "ERROR: container $name exited. Last log lines:"; docker logs --tail 40 "$name"; return 1
    fi
    sleep 10; t=$((t + 10))
    if [ "$t" -ge "$timeout" ]; then echo "ERROR: $name not ready after ${timeout}s"; return 1; fi
  done
  echo "$name ready on :$port"
}
