#!/usr/bin/env bash
# Stop and remove the model servers. Downloaded weights stay in the HF cache.
docker rm -f af-lightning af-super 2>/dev/null || true
echo "stopped"
