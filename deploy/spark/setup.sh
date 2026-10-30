#!/usr/bin/env bash
# One-time DGX Spark setup: check the machine, pull the vLLM image, download weights.
#   ./setup.sh lightning     (~23 GB)   recommended
#   ./setup.sh super         (~85 GB)
#   ./setup.sh all
set -euo pipefail
source "$(dirname "$0")/common.sh"
what="${1:-lightning}"

echo "== checks"
[ "$(uname -m)" = "aarch64" ] || echo "WARN: expected aarch64 (DGX Spark); got $(uname -m)"
command -v nvidia-smi >/dev/null || { echo "ERROR: nvidia-smi missing (is this DGX OS?)"; exit 1; }
command -v docker >/dev/null || { echo "ERROR: docker missing"; exit 1; }
docker info >/dev/null 2>&1 || { echo "ERROR: cannot talk to docker. Run: sudo usermod -aG docker \$USER, then log out/in"; exit 1; }
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
free -g | awk '/Mem:/ {print "memory: " $2 " GB total, " $7 " GB available"}'
df -h "$HOME" | awk 'NR==2 {print "disk free in $HOME: " $4}'

echo "== pulling $VLLM_IMAGE"
docker pull "$VLLM_IMAGE"
echo "== GPU visible inside docker?"
docker run --rm --gpus all --entrypoint nvidia-smi "$VLLM_IMAGE" -L

download() {
  echo "== downloading $1"
  mkdir -p "$HF_CACHE"
  docker run --rm -v "$HF_CACHE:/root/.cache/huggingface" -e HF_TOKEN="${HF_TOKEN:-}" \
    --entrypoint python3 "$VLLM_IMAGE" -c "from huggingface_hub import snapshot_download as d; print(d('$1'))"
}

case "$what" in
  lightning|all) download "$LIGHTNING_MODEL"; download "$LIGHTNING_DSPARK" ;;&
  super|all)
    download "$SUPER_MODEL"; download "$SUPER_MTP"
    curl -fsSL -o "$SCRIPT_DIR/super_v3_reasoning_parser.py" \
      "https://huggingface.co/$SUPER_MODEL/raw/main/super_v3_reasoning_parser.py"
    echo "saved super_v3_reasoning_parser.py" ;;
  lightning) ;;
  *) echo "usage: $0 lightning|super|all"; exit 2 ;;
esac
echo "== done. Next: ./serve.sh $what"
