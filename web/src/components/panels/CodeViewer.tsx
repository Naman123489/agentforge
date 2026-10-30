import { useMemo } from "react";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

for (const [n, l] of Object.entries({ bash, css, javascript, json, markdown, python, typescript, xml, yaml })) hljs.registerLanguage(n, l);

const EXT: Record<string, string> = {
  py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", mjs: "javascript", json: "json",
  yaml: "yaml", yml: "yaml", html: "xml", xml: "xml", svg: "xml", css: "css", md: "markdown", sh: "bash", toml: "bash",
};
const MAX_HIGHLIGHT = 300_000;

export function languageFor(path: string): string | undefined {
  return EXT[path.split(".").pop()?.toLowerCase() ?? ""];
}

/** Read-only viewer: the platform API has no file-write endpoint, so there is deliberately no editor. */
export function CodeViewer({ path, content }: { path: string; content: string }) {
  const lang = languageFor(path);
  const html = useMemo(() => {
    if (!lang || content.length > MAX_HIGHLIGHT) return null;
    try {
      return hljs.highlight(content, { language: lang, ignoreIllegals: true }).value; // escapes input
    } catch {
      return null;
    }
  }, [content, lang]);
  const lines = useMemo(() => content.split("\n").length, [content]);
  return (
    <div className="code-scroll flex min-h-0 overflow-auto text-[12.5px]">
      <div aria-hidden className="num select-none border-r border-line/60 px-3 py-3 text-right text-subtle/70">
        {Array.from({ length: lines }, (_, i) => <div key={i}>{i + 1}</div>)}
      </div>
      <pre className="hljs min-w-0 flex-1 px-4 py-3">
        {html !== null ? <code dangerouslySetInnerHTML={{ __html: html }} /> : <code>{content}</code>}
      </pre>
    </div>
  );
}
