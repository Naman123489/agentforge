# Sandbox image for generated code: Python (FastAPI app + pytest) and Node (JS syntax checks).
# Build once on the machine running AgentForge:  docker build -t agentforge-sandbox extras/deploy/sandbox
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir fastapi==0.115.* "uvicorn[standard]" pytest httpx pydantic
WORKDIR /work
