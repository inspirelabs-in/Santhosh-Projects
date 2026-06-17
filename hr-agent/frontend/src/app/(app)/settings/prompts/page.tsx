"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  Check,
  ChevronDown,
  ChevronUp,
  Cloud,
  HardDrive,
  Loader2,
  RefreshCw,
  Save,
  Search,
} from "lucide-react";
import { Topbar } from "@/components/layout/topbar";
import { api, swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface PromptEntry {
  name: string;
  label: string;
  stage: string;
  source: string;
  version: number | null;
  content: string;
  labels: string[];
}

const STAGE_COLORS: Record<string, string> = {
  intake: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  parse: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  fit_score: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
  screening: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
  voice_screen: "bg-pink-100 text-pink-800 dark:bg-pink-900/40 dark:text-pink-300",
  assignment: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300",
  interview: "bg-teal-100 text-teal-800 dark:bg-teal-900/40 dark:text-teal-300",
  ceo_interview: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  report: "bg-slate-100 text-slate-800 dark:bg-slate-900/40 dark:text-slate-300",
  rejection: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  chat_agent: "bg-cyan-100 text-cyan-800 dark:bg-cyan-900/40 dark:text-cyan-300",
};

function PromptCard({
  prompt,
  onSaved,
}: {
  prompt: PromptEntry;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(prompt.content);
  const [commitMsg, setCommitMsg] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const save = async () => {
    setSaving(true);
    try {
      await api.put(`/dashboard/settings/prompts/${prompt.name}`, {
        content: draft,
        commit_message: commitMsg || `Updated ${prompt.label}`,
      });
      setMsg({ type: "ok", text: `Saved as v${(prompt.version ?? 0) + 1}` });
      setEditing(false);
      setCommitMsg("");
      onSaved();
      setTimeout(() => setMsg(null), 3000);
    } catch (e: any) {
      setMsg({ type: "err", text: e?.message ?? "Save failed" });
      setTimeout(() => setMsg(null), 5000);
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => {
    setEditing(false);
    setDraft(prompt.content);
    setCommitMsg("");
  };

  const stageColor = STAGE_COLORS[prompt.stage] ?? STAGE_COLORS.report;

  return (
    <div className="rounded-lg border bg-card">
      {/* Header — always visible */}
      <button
        className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-accent/30 transition-colors"
        onClick={() => { setOpen((o) => !o); if (!open) setDraft(prompt.content); }}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm truncate">{prompt.label}</span>
            <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-mono font-semibold uppercase", stageColor)}>
              {prompt.stage.replace(/_/g, " ")}
            </span>
          </div>
          <div className="mt-0.5 flex items-center gap-3 text-xs text-muted-foreground">
            <span className="font-mono">{prompt.name}</span>
            {prompt.version != null && (
              <span>v{prompt.version}</span>
            )}
            <span className="flex items-center gap-1">
              {prompt.source === "langfuse" ? (
                <><Cloud className="h-3 w-3" /> Langfuse</>
              ) : (
                <><HardDrive className="h-3 w-3" /> Local</>
              )}
            </span>
          </div>
        </div>
        {open ? (
          <ChevronUp className="h-4 w-4 text-muted-foreground shrink-0" />
        ) : (
          <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />
        )}
      </button>

      {/* Content — expanded */}
      {open && (
        <div className="border-t px-4 py-3 space-y-3">
          {msg && (
            <div
              className={cn(
                "rounded-md border px-3 py-1.5 text-xs",
                msg.type === "ok"
                  ? "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300"
                  : "border-red-200 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300",
              )}
            >
              {msg.text}
            </div>
          )}

          {editing ? (
            <>
              <Textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={Math.min(25, Math.max(8, draft.split("\n").length + 2))}
                className="font-mono text-xs leading-relaxed"
              />
              <Input
                value={commitMsg}
                onChange={(e) => setCommitMsg(e.target.value)}
                placeholder="Commit message (optional)"
                className="text-xs"
              />
              <div className="flex items-center gap-2 justify-end">
                <Button variant="ghost" size="sm" onClick={cancel}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={save}
                  disabled={saving || draft === prompt.content}
                >
                  {saving ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Save className="mr-1.5 h-3.5 w-3.5" />
                  )}
                  Save to Langfuse
                </Button>
              </div>
            </>
          ) : (
            <>
              <pre className="max-h-96 overflow-auto rounded-md bg-muted/50 p-3 text-xs font-mono leading-relaxed whitespace-pre-wrap">
                {prompt.content || "(empty)"}
              </pre>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  {prompt.content.length.toLocaleString()} characters
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setEditing(true)}
                >
                  Edit prompt
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function PromptsPage() {
  const { data: prompts, isLoading, mutate } = useSWR<PromptEntry[]>(
    "/dashboard/settings/prompts",
    swrFetcher,
  );
  const [search, setSearch] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const refreshCache = async () => {
    setRefreshing(true);
    try {
      await api.post("/dashboard/settings/prompts/refresh");
      await mutate();
    } catch {
      // ignore
    } finally {
      setRefreshing(false);
    }
  };

  const filtered = (prompts ?? []).filter((p) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      p.name.toLowerCase().includes(q) ||
      p.label.toLowerCase().includes(q) ||
      p.stage.toLowerCase().includes(q)
    );
  });

  const langfuseCount = (prompts ?? []).filter((p) => p.source === "langfuse").length;

  return (
    <>
      <Topbar title="Prompts" subtitle="Manage LLM prompts (Langfuse)" />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-5xl px-8 py-6 pb-24 space-y-6">
          {/* Header stats */}
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle>Prompt management</CardTitle>
                  <CardDescription>
                    Edit prompts here — changes push to Langfuse and go live within 60 seconds.
                  </CardDescription>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={refreshCache}
                  disabled={refreshing}
                >
                  <RefreshCw className={cn("mr-1.5 h-3.5 w-3.5", refreshing && "animate-spin")} />
                  Flush cache
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="flex items-center gap-4 text-sm">
                <div className="flex items-center gap-1.5">
                  <Cloud className="h-4 w-4 text-primary" />
                  <span className="font-medium">{langfuseCount}</span>
                  <span className="text-muted-foreground">from Langfuse</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <HardDrive className="h-4 w-4 text-muted-foreground" />
                  <span className="font-medium">{(prompts ?? []).length - langfuseCount}</span>
                  <span className="text-muted-foreground">local fallback</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <Check className="h-4 w-4 text-emerald-500" />
                  <span className="font-medium">{(prompts ?? []).length}</span>
                  <span className="text-muted-foreground">total</span>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search prompts by name, label, or stage..."
              className="pl-9"
            />
          </div>

          {/* Prompt list */}
          {isLoading ? (
            <div className="space-y-3">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="h-16 animate-pulse rounded-lg bg-muted" />
              ))}
            </div>
          ) : (
            <div className="space-y-2">
              {filtered.map((p) => (
                <PromptCard key={p.name} prompt={p} onSaved={() => mutate()} />
              ))}
              {filtered.length === 0 && (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  No prompts match &quot;{search}&quot;
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
