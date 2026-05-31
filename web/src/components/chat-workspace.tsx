"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import { motion, AnimatePresence } from "framer-motion";
import {
  Send,
  Square,
  Sparkles,
  Brain,
  Zap,
  FileText,
  AlertTriangle,
  Clock,
  Search,
  BarChart3,
  Mail,
  ArrowRight,
  Trash2,
  CheckCircle2,
  History,
  Copy,
  Check,
  RotateCcw,
  ChevronDown,
  MessageSquare,
} from "lucide-react";

type ChatEvent = {
  type: "intent" | "action" | "result" | "answer" | "error" | "progress" | "done" | "token";
  data: Record<string, unknown>;
};

type Message = {
  role: "user" | "assistant";
  content: string;
  events?: ChatEvent[];
  streaming?: boolean;
};

const SUGGESTED = [
  { text: "Research mamaearth.in", desc: "Full brand dossier", icon: Search, color: "text-cyan-400", bg: "bg-cyan-500/10", border: "border-cyan-500/20", hover: "hover:border-cyan-500/30 hover:bg-cyan-500/[0.08]" },
  { text: "Research boat-lifestyle.com", desc: "Electronics D2C analysis", icon: BarChart3, color: "text-violet-400", bg: "bg-violet-500/10", border: "border-violet-500/20", hover: "hover:border-violet-500/30 hover:bg-violet-500/[0.08]" },
  { text: "Draft a 5-touch sequence for plumgoodness.com", desc: "Cold outreach", icon: Mail, color: "text-emerald-400", bg: "bg-emerald-500/10", border: "border-emerald-500/20", hover: "hover:border-emerald-500/30 hover:bg-emerald-500/[0.08]" },
  { text: "Research mcaffeine.com", desc: "Caffeine beauty brand", icon: Sparkles, color: "text-amber-400", bg: "bg-amber-500/10", border: "border-amber-500/20", hover: "hover:border-amber-500/30 hover:bg-amber-500/[0.08]" },
];

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const STORAGE_KEY = "grabon_chat_history";
const MAX_STORED = 100;

function loadHistory(): Message[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Message[];
    return parsed.map((m) => ({ ...m, streaming: false }));
  } catch {
    return [];
  }
}

function saveHistory(msgs: Message[]) {
  try {
    const trimmed = msgs.slice(-MAX_STORED).map(({ streaming, ...rest }) => rest);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {}
}

function buildHistoryPayload(msgs: Message[]): { role: string; content: string }[] {
  return msgs
    .filter((m) => m.content && !m.streaming)
    .slice(-10)
    .map((m) => ({ role: m.role, content: m.content.slice(0, 2000) }));
}

export function ChatWorkspace() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);

  useEffect(() => {
    const saved = loadHistory();
    if (saved.length > 0) setMessages(saved);
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (loaded && messages.length > 0) saveHistory(messages);
  }, [messages, loaded]);

  useEffect(() => {
    if (scrollRef.current) {
      const el = scrollRef.current;
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      if (nearBottom) el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      setShowScrollBtn(el.scrollHeight - el.scrollTop - el.clientHeight > 200);
    };
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  }, []);

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim() || busy) return;
    const userMsg: Message = { role: "user", content: text.trim() };
    const historyPayload = buildHistoryPayload([...messages, userMsg].slice(0, -1));

    setMessages((m) => [...m, userMsg]);
    setInput("");
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }
    setBusy(true);

    const assistantMsg: Message = { role: "assistant", content: "", events: [], streaming: true };
    setMessages((m) => [...m, assistantMsg]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const resp = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": process.env.NEXT_PUBLIC_API_KEY ?? "",
        },
        body: JSON.stringify({
          message: text.trim(),
          user: "web",
          history: historyPayload,
        }),
        signal: controller.signal,
      });

      if (!resp.ok) {
        const errText = await resp.text().catch(() => "");
        setMessages((m) => {
          const copy = [...m];
          const last = { ...copy[copy.length - 1] };
          last.content = `Error: ${resp.status} ${errText.slice(0, 200)}`;
          last.streaming = false;
          copy[copy.length - 1] = last;
          return copy;
        });
        setBusy(false);
        return;
      }

      const reader = resp.body?.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      if (!reader) { setBusy(false); return; }

      let currentEventType = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (line.startsWith("event:")) {
            currentEventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            const dataStr = line.slice(5).trim();
            if (!dataStr) continue;
            try {
              const data = JSON.parse(dataStr);
              updateAssistantMessage({ type: currentEventType, data });
            } catch { /* skip */ }
          } else if (line === "") {
            currentEventType = "";
          }
        }
      }

      setMessages((m) => {
        const copy = [...m];
        const last = { ...copy[copy.length - 1] };
        last.streaming = false;
        if (!last.content) last.content = "Done.";
        copy[copy.length - 1] = last;
        return copy;
      });
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setMessages((m) => {
          const copy = [...m];
          const last = { ...copy[copy.length - 1] };
          last.content = `Connection error: ${(err as Error).message}`;
          last.streaming = false;
          copy[copy.length - 1] = last;
          return copy;
        });
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }, [busy, messages]);

  function updateAssistantMessage(eventData: { type: string; data: Record<string, unknown> }) {
    setMessages((m) => {
      const copy = [...m];
      const last = { ...copy[copy.length - 1] };
      const events = [...(last.events ?? [])];
      const { type, data } = eventData;

      if (type !== "token") {
        events.push({ type: type as ChatEvent["type"], data });
      }

      if (type === "token") {
        last.content += (data.text as string) || "";
      } else if (type === "intent") {
        last.content = `Analyzing: **${data.intent}** (${Math.round(Number(data.confidence ?? 0) * 100)}% confidence)\n`;
      } else if (type === "action") {
        const brand = data.brand_name || data.brand_domain || "";
        last.content += `\nRunning **${data.action}** ${brand ? `for ${brand}` : ""}\n`;
      } else if (type === "progress") {
        const msg = (data.message || data.step || "") as string;
        if (data.node) {
          last.content += `\n${msg}\n`;
        } else {
          last.content += `\n_${msg}_\n`;
        }
      } else if (type === "result") {
        if (data.status === "completed" && data.workflow_result) {
          const wr = data.workflow_result as Record<string, unknown>;
          const brandId = wr.brand_id;
          const tier = String(wr.tier ?? "").toUpperCase();
          const score = wr.score_total ?? "—";
          const nodes = (wr.nodes ?? []) as string[];
          last.content += `\n### Research Complete\n\n`;
          last.content += `| | |\n|---|---|\n`;
          last.content += `| **Score** | ${score}/100 ${tier ? `— **${tier}**` : ""} |\n`;
          last.content += `| **Pipeline** | ${nodes.join(" → ") || "—"} |\n`;
          last.content += `| **Cost** | ${wr.total_cost_cents ?? 0}¢ |\n\n`;
          if (brandId) {
            last.content += `[Open full dossier →](/?brand=${brandId})\n`;
          }
        } else if (data.status === "started_async" || (data.status === "started" && data.workflow_id)) {
          const brandId = data.brand_id;
          last.content += `\n⏳ Research running in background`;
          if (data.message) last.content += ` — ${data.message}`;
          last.content += `\n\n`;
          if (brandId) {
            last.content += `[Watch progress →](/?brand=${brandId})\n`;
          } else {
            last.content += `Check the brand panel for live progress.\n`;
          }
        } else if (data.draft) {
          last.content += `\n${formatDraft(data.draft as string)}\n`;
        } else if (data.research) {
          last.content += formatDossier(data);
        } else if (data.status === "completed_inline") {
          last.content += `\n✓ Collection complete: **${data.collector}** — ${data.found ?? 0} results\n`;
        } else if (data.streamed) {
          // Streaming answer finished
        } else {
          last.content += `\n\`\`\`json\n${JSON.stringify(data, null, 2)}\n\`\`\`\n`;
        }
      } else if (type === "answer") {
        if (!data.streamed) {
          last.content += `\n${data.text ?? ""}`;
        }
      } else if (type === "error") {
        last.content += `\n> **Error:** ${data.message ?? "Unknown error"}\n`;
      }

      last.events = events;
      copy[copy.length - 1] = last;
      return copy;
    });
  }

  function handleStop() {
    abortRef.current?.abort();
    setBusy(false);
  }

  function handleCopy(text: string, idx: number) {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedIdx(idx);
      setTimeout(() => setCopiedIdx(null), 1500);
    });
  }

  function handleRegenerate(idx: number) {
    const userMsg = messages.slice(0, idx).reverse().find((m) => m.role === "user");
    if (userMsg) {
      setMessages((m) => m.slice(0, idx));
      setTimeout(() => sendMessage(userMsg.content), 50);
    }
  }

  const eventSteps = (events: ChatEvent[]) => {
    return events.filter(e => ["intent", "action", "progress", "result", "error"].includes(e.type));
  };

  function clearChat() {
    setMessages([]);
    localStorage.removeItem(STORAGE_KEY);
  }

  function scrollToBottom() {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }

  return (
    <section className="flex flex-col h-full bg-[var(--bg-primary)]">
      {/* Chat header */}
      {messages.length > 0 && (
        <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-2 bg-[var(--surface-elevated)]/60 backdrop-blur-sm">
          <div className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
            <MessageSquare size={12} />
            {messages.filter((m) => m.role === "user").length} messages
          </div>
          <button
            onClick={clearChat}
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] text-[var(--text-muted)] hover:text-rose-400 hover:bg-rose-500/10 transition-all"
          >
            <Trash2 size={11} />
            Clear
          </button>
        </div>
      )}

      {/* Messages area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto relative">
        <div className="mx-auto max-w-3xl px-4 py-6">
          <AnimatePresence mode="popLayout">
            {messages.length === 0 && (
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                className="flex flex-col items-center justify-center min-h-[calc(100vh-200px)]"
              >
                <div className="mb-6 text-center">
                  <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-600/15 to-violet-600/15 border border-blue-500/10 shadow-lg shadow-blue-500/5">
                    <Sparkles size={24} className="text-blue-400" />
                  </div>
                  <h2 className="text-xl font-bold text-[var(--text-primary)] tracking-tight">Lead Intelligence Agent</h2>
                  <p className="text-sm text-[var(--text-muted)] mt-1.5 max-w-md leading-relaxed">
                    Research brands, score leads, and generate outreach — all from one prompt.
                  </p>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 w-full max-w-xl">
                  {SUGGESTED.map((s, i) => {
                    const Icon = s.icon;
                    return (
                      <motion.button
                        key={s.text}
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: 0.1 + i * 0.05 }}
                        whileHover={{ y: -2 }}
                        whileTap={{ scale: 0.98 }}
                        onClick={() => { setInput(s.text); sendMessage(s.text); }}
                        className={`group flex items-center gap-3 rounded-xl border ${s.border} ${s.bg} px-3 py-3 text-left transition-all ${s.hover} shadow-sm hover:shadow-md`}
                      >
                        <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--surface)]/80`}>
                          <Icon size={14} className={s.color} />
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="text-[13px] font-medium text-[var(--text-primary)] truncate">{s.text}</div>
                          <div className="text-[11px] text-[var(--text-muted)] mt-0.5">{s.desc}</div>
                        </div>
                        <ArrowRight size={13} className="shrink-0 text-[var(--text-subtle)] group-hover:text-[var(--text-secondary)] transition-colors" />
                      </motion.button>
                    );
                  })}
                </div>
              </motion.div>
            )}

            {messages.map((m, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.15 }}
                className={`mb-5 group ${m.role === "user" ? "flex justify-end" : ""}`}
              >
                {m.role === "user" ? (
                  <div className="max-w-[75%] rounded-2xl rounded-br-md bg-blue-600 px-4 py-3 text-sm text-white shadow-sm shadow-blue-600/20">
                    {m.content}
                  </div>
                ) : (
                  <div className="w-full">
                    {/* Step indicators */}
                    {m.events && m.events.length > 0 && (
                      <div className="flex items-center gap-1.5 mb-2.5 flex-wrap">
                        {eventSteps(m.events).map((ev, j) => {
                          const isNode = ev.type === "progress" && ev.data.node;
                          return (
                            <span
                              key={j}
                              className={`inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] font-semibold transition-all ${
                                isNode ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" :
                                ev.type === "intent" ? "bg-purple-500/10 text-purple-400 border border-purple-500/20" :
                                ev.type === "action" ? "bg-blue-500/10 text-blue-400 border border-blue-500/20" :
                                ev.type === "progress" ? "bg-amber-500/10 text-amber-400 border border-amber-500/20" :
                                ev.type === "result" ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" :
                                ev.type === "error" ? "bg-rose-500/10 text-rose-400 border border-rose-500/20" :
                                "bg-[var(--surface-overlay)] text-[var(--text-muted)] border border-[var(--border)]"
                              }`}
                            >
                              {isNode ? <CheckCircle2 size={10} /> :
                               ev.type === "intent" ? <Brain size={10} /> :
                               ev.type === "action" ? <Zap size={10} /> :
                               ev.type === "progress" ? <Clock size={10} /> :
                               ev.type === "result" ? <FileText size={10} /> :
                               ev.type === "error" ? <AlertTriangle size={10} /> : null}
                              {isNode ? ev.data.node as string : ev.type}
                            </span>
                          );
                        })}
                        {m.streaming && (
                          <span className="inline-flex items-center gap-1.5 text-[10px] text-[var(--text-muted)]">
                            <span className="flex gap-0.5">
                              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
                              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse [animation-delay:200ms]" />
                              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse [animation-delay:400ms]" />
                            </span>
                            processing
                          </span>
                        )}
                      </div>
                    )}
                    {/* Message content */}
                    <div className="rounded-2xl bg-[var(--surface-elevated)] border border-[var(--border)] px-5 py-4 shadow-sm">
                      <div className="prose prose-invert prose-sm max-w-none
                        prose-headings:text-[var(--text-primary)] prose-headings:font-semibold
                        prose-h2:text-[15px] prose-h2:mt-5 prose-h2:mb-2
                        prose-h3:text-[13px] prose-h3:mt-4 prose-h3:mb-1.5
                        prose-p:my-1.5 prose-p:text-[var(--text-secondary)] prose-p:text-[13px] prose-p:leading-relaxed
                        prose-li:my-0.5 prose-li:text-[var(--text-secondary)] prose-li:text-[13px]
                        prose-strong:text-[var(--text-primary)] prose-strong:font-semibold
                        prose-em:text-[var(--text-muted)]
                        prose-hr:border-[var(--border)] prose-hr:my-3
                        prose-code:text-blue-400 prose-code:bg-blue-500/10 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded-md prose-code:text-xs prose-code:font-normal
                        prose-blockquote:border-l-rose-500/50 prose-blockquote:bg-rose-500/5 prose-blockquote:py-1 prose-blockquote:px-3 prose-blockquote:rounded-r-lg
                        prose-a:text-blue-400 prose-a:no-underline hover:prose-a:underline
                      ">
                        <Markdown>{m.content || (m.streaming ? "_Thinking..._" : "")}</Markdown>
                      </div>
                    </div>
                    {/* Action buttons */}
                    {!m.streaming && m.content && (
                      <div className="flex items-center gap-1 mt-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={() => handleCopy(m.content, i)}
                          className="flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-elevated)] transition-all"
                          title="Copy response"
                        >
                          {copiedIdx === i ? <Check size={11} className="text-emerald-400" /> : <Copy size={11} />}
                          {copiedIdx === i ? "Copied" : "Copy"}
                        </button>
                        <button
                          onClick={() => handleRegenerate(i)}
                          disabled={busy}
                          className="flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--surface-elevated)] transition-all disabled:opacity-30"
                          title="Regenerate response"
                        >
                          <RotateCcw size={11} />
                          Retry
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </motion.div>
            ))}
          </AnimatePresence>
        </div>

        {/* Scroll-to-bottom button */}
        <AnimatePresence>
          {showScrollBtn && (
            <motion.button
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 10 }}
              onClick={scrollToBottom}
              className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-1.5 rounded-full bg-[var(--surface-elevated)] border border-[var(--border)] px-4 py-2 text-xs text-[var(--text-secondary)] shadow-lg hover:bg-[var(--surface-overlay)] transition-colors"
            >
              <ChevronDown size={12} />
              Scroll to bottom
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      {/* Input area */}
      <div className="border-t border-[var(--border)] bg-[var(--surface-elevated)]/80 backdrop-blur-xl">
        <div className="mx-auto max-w-3xl px-4 py-4">
          <div className="flex items-end gap-3 rounded-2xl border border-[var(--border)] bg-[var(--bg-primary)] p-2 focus-within:border-blue-500/30 focus-within:ring-2 focus-within:ring-blue-500/10 transition-all shadow-sm">
            <textarea
              ref={inputRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage(input);
                }
              }}
              rows={1}
              placeholder="Research a brand, draft outreach, or ask a question..."
              className="flex-1 min-h-[40px] max-h-[160px] resize-none bg-transparent px-3 py-2 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-subtle)]"
            />
            {busy ? (
              <motion.button
                whileTap={{ scale: 0.95 }}
                onClick={handleStop}
                className="flex h-10 items-center gap-1.5 rounded-xl bg-rose-600 px-4 text-sm font-medium text-white shadow-sm hover:bg-rose-500 transition-colors shrink-0"
              >
                <Square size={14} />
                Stop
              </motion.button>
            ) : (
              <motion.button
                whileTap={{ scale: 0.95 }}
                onClick={() => sendMessage(input)}
                className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600 text-white shadow-sm hover:bg-blue-500 disabled:opacity-30 disabled:hover:bg-blue-600 transition-colors shrink-0"
                disabled={!input.trim()}
              >
                <Send size={16} />
              </motion.button>
            )}
          </div>
          <p className="mt-2 text-center text-[10px] text-[var(--text-subtle)]">
            Shift+Enter for new line · Powered by SearXNG + NVIDIA NIM
          </p>
        </div>
      </div>
    </section>
  );
}

function formatDossier(data: Record<string, unknown>): string {
  const r = (data.research ?? {}) as Record<string, unknown>;
  const co = (r.company ?? {}) as Record<string, unknown>;
  const pos = (r.positioning ?? {}) as Record<string, unknown>;
  const df = (r.digital_footprint ?? {}) as Record<string, unknown>;
  const comp = (data.competitor ?? {}) as Record<string, unknown>;
  const competitors = (comp.competitors ?? []) as Record<string, unknown>[];
  const gaps = (comp.gap_map ?? {}) as Record<string, string>;
  const opp = (data.opportunity ?? {}) as Record<string, unknown>;
  const svcs = (opp.services_recommended ?? []) as Record<string, unknown>[];
  const sc = (data.score ?? {}) as Record<string, unknown>;
  const out = (data.outreach ?? {}) as Record<string, unknown>;
  const subjects = (out.subjects ?? []) as string[];
  const bodies = (out.bodies ?? []) as string[];
  const nodes = (data.nodes_completed ?? []) as string[];

  let s = "\n";

  s += `## ${co.brand_name || data.brand_name || "Brand"}\n\n`;
  if (co.legal_name) s += `*${co.legal_name}*\n\n`;

  s += `| | |\n|---|---|\n`;
  s += `| **Domain** | ${co.domain || data.brand_domain || "—"} |\n`;
  s += `| **HQ** | ${co.hq || "—"} |\n`;
  s += `| **Employees** | ${co.employees_est ?? "—"} |\n`;
  s += `| **Revenue** | ${co.revenue_band || co.revenue_estimate_inr || "—"} |\n`;
  s += `| **Funding** | ${co.funding_stage || "—"} |\n`;
  s += `| **Founded** | ${co.founded_year ?? "—"} |\n`;
  s += `| **Category** | ${pos.category || "—"} > ${pos.sub_category || "—"} |\n`;
  s += `\n**USP:** ${pos.USP_summary || "—"}\n`;

  if (df.martech_stack && Array.isArray(df.martech_stack) && df.martech_stack.length > 0) {
    s += `\n**Tech:** ${(df.martech_stack as string[]).join(" · ")}\n`;
  }

  s += `\n### Competitors\n\n`;
  if (competitors.length > 0) {
    competitors.forEach((c) => {
      const dot = c.threat_level === "high" ? "**HIGH**" : c.threat_level === "medium" ? "MEDIUM" : "LOW";
      s += `- **${c.name}** ${c.domain ? `(${c.domain})` : ""} — ${dot} — ${c.positioning || ""}\n`;
    });
  } else {
    s += "_No competitors identified_\n";
  }

  if (Object.keys(gaps).length > 0) {
    s += `\n### Gap Analysis\n\n`;
    Object.entries(gaps).forEach(([svc, reason]) => {
      s += `- **${svc.replace(/_/g, " ")}:** ${reason}\n`;
    });
  }

  s += `\n### Opportunity\n\n`;
  s += `${opp.diagnosis || "—"}\n\n`;
  if (svcs.length > 0) {
    svcs.forEach((svc) => {
      s += `- **${svc.service}** — ${svc.rationale} _(Impact: ${svc.estimated_impact}, Confidence: ${svc.confidence})_\n`;
    });
  }
  s += `\n**Deal Size:** ₹${Number(opp.estimated_deal_size_inr ?? 0).toLocaleString("en-IN")}\n`;

  const tier = String(sc.tier ?? "—").toUpperCase();
  s += `\n### Score: ${sc.total ?? "—"}/100 — ${tier}\n\n`;
  s += `Conversion: **${sc.predicted_conversion_probability ?? "—"}** · Deal Value: **₹${Number(sc.estimated_deal_value_inr ?? 0).toLocaleString("en-IN")}**\n\n`;
  const whys = (sc.why ?? []) as string[];
  if (whys.length > 0) {
    whys.forEach((w) => { s += `- ${w}\n`; });
  }
  const flags = (sc.red_flags ?? []) as string[];
  if (flags.length > 0) {
    s += `\n> **Red Flags:**\n`;
    flags.forEach((f) => { s += `> - ${f}\n`; });
  }

  if (subjects.length > 0 && !(out as Record<string, unknown>).skipped) {
    s += `\n### Outreach (${subjects.length}-touch)\n\n`;
    subjects.forEach((subj, i) => {
      s += `**${i + 1}. ${subj}** — ${bodies[i] || ""}\n\n`;
    });
    if (out.linkedin_inmail) s += `**LinkedIn:** ${out.linkedin_inmail}\n\n`;
    if (out.voicemail_script) s += `**Voicemail:** ${out.voicemail_script}\n`;
  }

  const brandId = data.brand_id;
  s += `\n---\n\n_Pipeline: ${nodes.join(" → ")} · Cost: ${data.total_cost_cents ?? 0}¢_`;
  if (brandId) s += ` · [View full profile →](/brands/${brandId})`;
  s += "\n";
  return s;
}

function formatDraft(raw: string): string {
  try {
    const parsed = JSON.parse(raw);
    let out = "";
    if (parsed.subject) out += `**Subject:** ${parsed.subject}\n\n`;
    if (parsed.body) out += parsed.body + "\n";
    if (parsed.cta) out += `\n_CTA: ${parsed.cta}_`;
    if (parsed.subjects && Array.isArray(parsed.subjects)) {
      parsed.subjects.forEach((s: string, i: number) => {
        out += `\n**Step ${i + 1}:** ${s}\n${parsed.bodies?.[i] ?? ""}\n`;
      });
    }
    return out || raw;
  } catch {
    return raw;
  }
}
