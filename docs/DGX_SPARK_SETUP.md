# Running AgentForge's models locally on a DGX Spark ($0 per token)

## Read this first

1. **Hackathon rule H1.** The rules say the project must run on Nebius Token Factory or
   Nebius AI Cloud. Local models are fine for development, benchmarks and the bake-off.
   For the **submitted demo**, switch back to Token Factory (one env var, see the end of
   this guide) unless the organisers confirm that local inference is allowed.
2. **Ultra cannot run on a Spark.** Nemotron 3 Ultra is 352 GB even in NVFP4. One
   Spark has 128 GB, and two have 256 GB. Slot A uses the same local model with full
   reasoning instead.
3. **Unified memory.** The GPU shares 128 GB with the OS and AgentForge's Docker
   sandboxes (npm builds, Playwright). Leave headroom.

## What fits

| Profile | Models | Memory (weights) | Speed | Use |
|---|---|---|---|---|
| `spark-lightning` | Nemotron 3.5 Lightning 30B-A3B NVFP4 + DSpark draft | ~23 GB | fast | **Recommended.** Lots of room for sandboxes |
| `spark-super` | Nemotron 3 Super 120B-A12B NVFP4 + MTP draft | ~85 GB | ~15-23 tok/s | Best quality, slow builds |
| `spark-mixed` | Super (A, B) + Lightning (C) | ~102 GB | mixed | Experimental, very tight |

With one model, the three router slots are three **reasoning modes** of that model
(sent as `chat_template_kwargs`, per the NVIDIA model cards):

| Slot | Mode | Used for |
|---|---|---|
| A | `enable_thinking: true` | plan, API contract, critical review, escalations |
| B | `enable_thinking: true, low_effort: true` | backend/frontend code, debugging |
| C | `enable_thinking: false` | log summaries, error classification, commit messages |

The routing logic is unchanged. Escalation still moves a failing task from B (light
thinking) to A (full thinking). Every slot costs $0, so the router skips the
dollar-based comparison and the token limits act as the GPU-time budget.

## 1. On the Spark: copy the project

From your laptop (or `git clone` once the repo exists):

```bash
scp -r agentforge <user>@<spark-ip>:~/
```

Then on the Spark:

```bash
cd ~/agentforge/deploy/spark && chmod +x *.sh
```

## 2. Download and start the model (Spark)

```bash
./setup.sh lightning
```
This checks the GPU and Docker, pulls `vllm/vllm-openai:v0.27.1` (the image the NVIDIA
model cards specify for DGX Spark) and downloads about 23 GB of weights to `~/.cache/huggingface`.
If Docker says "permission denied", run `sudo usermod -aG docker $USER` and log in again.

```bash
./serve.sh lightning
```
This starts the `af-lightning` container on port 8001, waits until it is ready, then
runs `check.sh`. The container restarts automatically after a reboot.

Other commands: `./check.sh` (status, test call, free memory), `./stop.sh`, and
`./setup.sh super && ./serve.sh super` for the 120B model.

## 3. Python environment for AgentForge (Spark)

```bash
cd ~/agentforge && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Create `~/agentforge/.env`, or export these variables:

```bash
AF_ROUTER_CONFIG=config/profiles/spark-lightning.toml
# SPARK_HOST is not needed when AgentForge runs on the Spark (defaults to localhost)
```

Verify:

```bash
AF_ROUTER_CONFIG=config/profiles/spark-lightning.toml .venv/bin/python scripts/verify_models.py
```
Expect `[OK]` for slots A, B and C, the latency, and whether `json_schema` works. If
`supports_json_schema` comes back false, set it to false in the profile.

```bash
.venv/bin/python -m pytest -q
```

## 4. Option: run AgentForge on your Windows laptop instead

The model servers listen only on the Spark's localhost (safer). Open an SSH tunnel from
the laptop and keep it running:

```bash
ssh -N -L 8001:localhost:8001 -L 8000:localhost:8000 <user>@<spark-ip>
```

In `agentforge\.env` on the laptop:

```
AF_ROUTER_CONFIG=config/profiles/spark-lightning.toml
SPARK_HOST=localhost
```

Running everything on the Spark is still the better choice. The Docker sandboxes need
Linux, and the Spark has both Docker and the GPU.

## 5. Switching back to Token Factory (for the submission)

Unset `AF_ROUTER_CONFIG`, or point it at `config/models.toml`, and keep
`NEBIUS_API_KEY` in `.env`. No code changes are needed. Agents call `router.call(...)`
the same way in both modes.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Container exits during startup | `docker logs af-lightning`. Out of memory: stop other GPU jobs, or lower `--gpu-memory-utilization` in `serve.sh` |
| `mixed`: second server won't start | Not enough free memory. Use `lightning` |
| Thinking text appears inside answers | Wrong reasoning parser. Keep `nemotron_v3` (Lightning) and `super_v3` plus the plugin file (Super) |
| Very slow first start | Normal: weights load and CUDA graphs compile. Super can take about 10 min |
| Router timeouts | Raise `timeout_s` in the profile. Full-thinking calls on Super are slow |
| `verify_models.py` cannot reach the server | `./check.sh` on the Spark. Laptop: is the SSH tunnel open? |

These scripts follow the NVIDIA model cards' "DGX Spark (GB10)" recipes as of
October 2026. They have **not been run on a Spark yet**, so treat the first run as
the test.
