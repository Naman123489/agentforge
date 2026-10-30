// Fails if the production bundle contains anything that looks like a provider credential or a demo mock.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const PATTERNS = [
  [/NEBIUS_API_KEY/, "provider key variable name"],
  [/\bv1\.[A-Za-z0-9_-]{40,}/, "Nebius-style API key"],
  [/\bsk-[A-Za-z0-9]{20,}/, "OpenAI-style API key"],
  [/Bearer\s+[A-Za-z0-9._-]{20,}/, "hard-coded bearer token"],
  [/installDemo|demo\/fixtures/, "demo mock code in a non-demo build"],
];
const files = [];
const walk = (d) => readdirSync(d).forEach((f) => { const p = join(d, f); statSync(p).isDirectory() ? walk(p) : files.push(p); });
walk("dist");
let bad = 0;
for (const f of files.filter((f) => /\.(js|html|css|map)$/.test(f))) {
  const text = readFileSync(f, "utf8");
  for (const [re, what] of PATTERNS) if (re.test(text)) { console.error(`✗ ${f}: ${what}`); bad++; }
}
console.log(bad ? `${bad} problem(s) found` : `✓ ${files.length} files scanned: no secrets, no demo mock`);
process.exit(bad ? 1 : 0);
