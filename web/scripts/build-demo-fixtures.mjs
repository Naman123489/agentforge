// Builds src/demo/fixtures.json for the labelled demo mode from REAL artifacts in this repo:
//   data/backend_runs/contract1  - a live Backend Agent build on Nebius Token Factory (events, BuildResult, files)
//   extras/demo_app/frontend     - reference arcade frontend (also served as the demo preview)
//   examples/arcade_openapi.yaml - the per-run generated-app contract
// Usage: node scripts/build-demo-fixtures.mjs
import { copyFileSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const run = join(root, "data/backend_runs/contract1");
const fe = join(root, "extras/demo_app/frontend");
const read = (p) => readFileSync(p, "utf8");

const files = {};
for (const f of ["app.py", "test_app.py"]) files[`backend/${f}`] = read(join(run, "backend", f));
for (const f of readdirSync(fe)) files[`frontend/${f}`] = read(join(fe, f));
const contract = read(join(root, "examples/arcade_openapi.yaml"));
files["contract/openapi.yaml"] = contract;

const fixtures = {
  source: "Recorded live Backend Agent build on Nebius Token Factory, 2026-10-29 (data/backend_runs/contract1)",
  backendEvents: read(join(run, "events.jsonl")).trim().split("\n").map((l) => JSON.parse(l)),
  backendResult: JSON.parse(read(join(run, "build_result.json"))),
  backendUsage: JSON.parse(read(join(run, "usage_summary.json"))),
  files,
  contract,
};
writeFileSync(new URL("../src/demo/fixtures.json", import.meta.url), JSON.stringify(fixtures));

const pub = new URL("../demo-public/demo-preview/", import.meta.url);
mkdirSync(pub, { recursive: true });
for (const f of readdirSync(fe)) copyFileSync(join(fe, f), new URL(f, pub));
copyFileSync(new URL("../public/favicon.svg", import.meta.url), new URL("../demo-public/favicon.svg", import.meta.url));
console.log(`fixtures: ${Object.keys(files).length} files, ${fixtures.backendEvents.length} recorded events`);
