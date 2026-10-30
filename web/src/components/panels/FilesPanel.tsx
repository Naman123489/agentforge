import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { ChevronRight, Download, FileCode2, Folder, FolderOpen } from "lucide-react";
import { api } from "../../api/endpoints";
import { isSafeRelativePath } from "../../api/client";
import type { FileTreeResponse, Stage } from "../../api/types";
import { fmtInt } from "../../lib/format";
import { useFileContent, useFiles } from "../../state/queries";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState, ErrorState, Loading } from "../ui/States";
import { useToast } from "../ui/Toast";
import { CodeViewer } from "./CodeViewer";

interface Node { name: string; path: string; kind: "file" | "directory"; size?: number; children: Node[] }

export function buildTree(entries: FileTreeResponse["entries"]): Node[] {
  const root: Node = { name: "", path: "", kind: "directory", children: [] };
  const dirs = new Map<string, Node>([["", root]]);
  const ensureDir = (p: string): Node => {
    if (dirs.has(p)) return dirs.get(p)!;
    const parent = ensureDir(p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "");
    const n: Node = { name: p.split("/").pop()!, path: p, kind: "directory", children: [] };
    parent.children.push(n);
    dirs.set(p, n);
    return n;
  };
  for (const e of entries) {
    const p = e.path.replace(/\/+$/, "");
    if (!isSafeRelativePath(p)) continue; // never render paths that could escape the workspace
    if (e.kind === "directory") ensureDir(p);
    else ensureDir(p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : "").children.push(
      { name: p.split("/").pop()!, path: p, kind: "file", size: e.size_bytes, children: [] });
  }
  const sort = (n: Node) => {
    n.children.sort((a, b) => (a.kind === b.kind ? a.name.localeCompare(b.name) : a.kind === "directory" ? -1 : 1));
    n.children.forEach(sort);
  };
  sort(root);
  return root.children;
}

function TreeNode({ node, depth, selected, changed, onOpen }: {
  node: Node; depth: number; selected: string | null; changed: Set<string>; onOpen: (p: string) => void;
}) {
  const [open, setOpen] = useState(depth < 1 || (selected?.startsWith(node.path + "/") ?? false));
  const pad = { paddingLeft: `${8 + depth * 14}px` };
  if (node.kind === "directory") {
    return (
      <li role="treeitem" aria-expanded={open} aria-selected={false}>
        <button type="button" onClick={() => setOpen((o) => !o)} style={pad}
          className="flex w-full items-center gap-1.5 rounded py-1 pr-2 text-left text-sm text-muted hover:bg-ink-800">
          <ChevronRight className={`size-3.5 transition ${open ? "rotate-90" : ""}`} aria-hidden />
          {open ? <FolderOpen className="size-4 text-nv-700" aria-hidden /> : <Folder className="size-4 text-nv-700" aria-hidden />}
          {node.name}
        </button>
        {open && <ul role="group">{node.children.map((c) => <TreeNode key={c.path} node={c} depth={depth + 1} selected={selected} changed={changed} onOpen={onOpen} />)}</ul>}
      </li>
    );
  }
  const sel = node.path === selected;
  return (
    <li role="treeitem" aria-selected={sel}>
      <button type="button" onClick={() => onOpen(node.path)} style={pad}
        className={`flex w-full items-center gap-1.5 rounded py-1 pr-2 text-left text-sm ${sel ? "bg-nv-500/15 text-fg" : "text-muted hover:bg-ink-800"}`}>
        <span className="w-3.5" />
        <FileCode2 className="size-4 shrink-0 text-subtle" aria-hidden />
        <span className="truncate">{node.name}</span>
        {changed.has(node.path) && <span className="ml-auto size-1.5 shrink-0 rounded-full bg-amber-400" title="Changed by an agent in this run" />}
      </button>
    </li>
  );
}

export function FilesPanel({ runId, stages }: { runId: string; stages: Stage[] }) {
  const [params, setParams] = useSearchParams();
  const selected = params.get("file");
  const files = useFiles(runId);
  const content = useFileContent(runId, selected && isSafeRelativePath(selected) ? selected : null);
  const [downloading, setDownloading] = useState(false);
  const toast = useToast();
  const tree = useMemo(() => buildTree(files.data?.entries ?? []), [files.data]);
  const changed = useMemo(() => new Set(stages.flatMap((s) => s.result?.files_changed ?? [])), [stages]);
  const open = (p: string) => setParams((q) => { q.set("file", p); return q; }, { replace: true });

  const download = async () => {
    setDownloading(true);
    try {
      const blob = await api.downloadRunZip(runId);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${runId}.zip`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      toast("bad", `Download failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDownloading(false);
    }
  };

  if (files.isLoading) return <Loading label="Loading file tree" />;
  if (files.isError) return <ErrorState error={files.error} what="the file tree" onRetry={() => void files.refetch()} />;
  if (!files.data || tree.length === 0)
    return <EmptyState icon={<FileCode2 className="size-5" />} title="No generated files yet">Files appear once the builders write the generated application.</EmptyState>;

  return (
    <div className="grid min-h-[28rem] grid-cols-1 md:grid-cols-[16rem_1fr]">
      <div className="min-h-0 border-b border-line md:border-r md:border-b-0">
        <div className="flex items-center justify-between gap-2 px-3 py-2">
          <span className="text-xs text-subtle">Generated app · <span className="font-mono">{files.data.root || "workspace"}</span></span>
          <Button size="sm" variant="ghost" loading={downloading} onClick={() => void download()} icon={<Download className="size-3.5" />} aria-label="Download project as ZIP">ZIP</Button>
        </div>
        <ul role="tree" aria-label="Generated files" className="max-h-[32rem] overflow-y-auto px-1 pb-2">
          {tree.map((n) => <TreeNode key={n.path} node={n} depth={0} selected={selected} changed={changed} onOpen={open} />)}
        </ul>
      </div>
      <div className="flex min-h-0 min-w-0 flex-col">
        {!selected ? (
          <EmptyState icon={<FileCode2 className="size-5" />} title="Select a file">Read-only view of the generated application. Amber dots mark files changed by an agent.</EmptyState>
        ) : !isSafeRelativePath(selected) ? (
          <ErrorState error={new Error("This path is outside the run workspace and was not requested.")} what="the file" />
        ) : (
          <>
            <div className="flex items-center gap-2 border-b border-line px-4 py-2">
              <span className="truncate font-mono text-xs text-muted">{selected}</span>
              {changed.has(selected) && <Badge tone="warn">changed</Badge>}
              {content.data?.truncated && <Badge tone="warn">truncated by server</Badge>}
              <span className="num ml-auto text-[11px] text-subtle">{content.data ? `${fmtInt(content.data.content.length)} chars` : ""}</span>
            </div>
            {content.isLoading ? <Loading label="Opening file" />
              : content.isError ? <ErrorState error={content.error} what={selected} onRetry={() => void content.refetch()} />
              : content.data?.encoding === "base64" ? <EmptyState title="Binary file">This file is not text and is not shown.</EmptyState>
              : content.data ? <CodeViewer path={selected} content={content.data.content} /> : null}
          </>
        )}
      </div>
    </div>
  );
}
