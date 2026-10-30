#!/usr/bin/env bash
# Start the local Nemotron server(s) on the DGX Spark. Containers restart on reboot.
#   ./serve.sh lightning   Lightning 3.5 on :8001     -> config/profiles/spark-lightning.toml (recommended)
#   ./serve.sh super       Super 120B on :8000        -> config/profiles/spark-super.toml
#   ./serve.sh mixed       Super :8000 + Lightning :8001 (experimental) -> spark-mixed.toml
#
# Memory: the GPU shares the Spark's 128 GB with the OS and AgentForge's Docker
# sandboxes, so these leave more headroom than the model cards' defaults.
set -euo pipefail
source "$(dirname "$0")/common.sh"
mode="${1:-lightning}"

run_lightning() {  # run_lightning <gpu_mem_util> <max_len> <speculative: yes|no>
  local spec=()
  [ "$3" = yes ] && spec=(--speculative_config.model "$LIGHTNING_DSPARK" --speculative_config.num_speculative_tokens 3)
  docker rm -f af-lightning >/dev/null 2>&1 || true
  docker run -d --name af-lightning --restart unless-stopped --gpus all \
    -v "$HF_CACHE:/root/.cache/huggingface" -e HF_TOKEN="${HF_TOKEN:-}" \
    -p "$BIND:$LIGHTNING_PORT:8000" \
    "$VLLM_IMAGE" \
      --model "$LIGHTNING_MODEL" \
      --served-model-name "$LIGHTNING_NAME" \
      --host 0.0.0.0 --port 8000 \
      --moe-backend marlin \
      --kv-cache-dtype fp8 \
      --enable-prefix-caching \
      --gpu-memory-utilization "$1" \
      --max-model-len "$2" \
      --max-num-seqs 8 \
      --mamba-backend flashinfer \
      --mamba-cache-mode align \
      --reasoning-parser nemotron_v3 \
      --tool-call-parser qwen3_coder \
      --enable-auto-tool-choice \
      "${spec[@]}"
  wait_ready "$LIGHTNING_PORT" af-lightning
}

run_super() {  # run_super <gpu_mem_util> <max_len> <speculative: yes|no>
  [ -f "$SCRIPT_DIR/super_v3_reasoning_parser.py" ] || { echo "run ./setup.sh super first"; exit 1; }
  local spec=()
  [ "$3" = yes ] && spec=(--speculative_config "{\"method\":\"mtp\",\"num_speculative_tokens\":3,\"model\":\"$SUPER_MTP\",\"moe_backend\":\"triton\"}")
  docker rm -f af-super >/dev/null 2>&1 || true
  docker run -d --name af-super --restart unless-stopped --gpus all \
    -e VLLM_NVFP4_GEMM_BACKEND=marlin \
    -e VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 \
    -e VLLM_FLASHINFER_ALLREDUCE_BACKEND=trtllm \
    -e VLLM_USE_FLASHINFER_MOE_FP4=0 \
    -e HF_TOKEN="${HF_TOKEN:-}" \
    -v "$HF_CACHE:/root/.cache/huggingface" \
    -v "$SCRIPT_DIR/super_v3_reasoning_parser.py:/app/super_v3_reasoning_parser.py" \
    -p "$BIND:$SUPER_PORT:8000" \
    "$VLLM_IMAGE" \
      --model "$SUPER_MODEL" \
      --served-model-name "$SUPER_NAME" \
      --host 0.0.0.0 --port 8000 \
      --async-scheduling \
      --dtype auto \
      --kv-cache-dtype fp8 \
      --trust-remote-code \
      --gpu-memory-utilization "$1" \
      --enable-chunked-prefill \
      --max-num-seqs 4 \
      --max-model-len "$2" \
      --moe-backend marlin \
      --mamba_ssm_cache_dtype float16 \
      --quantization fp4 \
      --reasoning-parser-plugin /app/super_v3_reasoning_parser.py \
      --reasoning-parser super_v3 \
      --enable-auto-tool-choice \
      --tool-call-parser qwen3_coder \
      "${spec[@]}"
  wait_ready "$SUPER_PORT" af-super 2400
}

case "$mode" in
  lightning) docker rm -f af-super >/dev/null 2>&1 || true; run_lightning 0.75 262144 yes ;;
  super)     docker rm -f af-lightning >/dev/null 2>&1 || true; run_super 0.85 262144 yes ;;
  mixed)
    echo "EXPERIMENTAL: ~102 GB of weights in 128 GB. Watch 'free -g'; fall back to 'lightning' if a server fails."
    run_super 0.70 65536 no
    run_lightning 0.20 32768 no ;;
  *) echo "usage: $0 lightning|super|mixed"; exit 2 ;;
esac
"$SCRIPT_DIR/check.sh"
