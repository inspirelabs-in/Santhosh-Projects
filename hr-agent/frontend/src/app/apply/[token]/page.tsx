"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import {
  CheckCircle2,
  Clock,
  ExternalLink,
  FileText,
  Loader2,
  Send,
  AlertTriangle,
} from "lucide-react";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

interface ApplyContext {
  application_id: string;
  action: string;
  role_title: string;
  candidate_name: string | null;
  current_stage: string;
  already_submitted: boolean;
  questions: { id: string; question: string; required?: boolean }[] | null;
  assignment_brief: string | null;
  assignment_instructions: string | null;
  deadline_days: number | null;
  problem_doc_url: string | null;
  problem_doc_filename: string | null;
  expires_at: string;
}

async function applyFetcher(path: string): Promise<ApplyContext> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return res.json();
}

function ScreeningForm({
  ctx,
  token,
}: {
  ctx: ApplyContext;
  token: string;
}) {
  const questions = ctx.questions ?? [];
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    setError(null);
    const payload = questions.map((q) => ({
      question_id: q.id,
      answer: (answers[q.id] || "").trim(),
    }));
    const missing = questions.filter(
      (q) => q.required !== false && !(answers[q.id] || "").trim()
    );
    if (missing.length) {
      setError(`Please answer all required questions (${missing.length} remaining)`);
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch(`${BASE}/apply/${token}/screening`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          answers: payload,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `Error ${res.status}`);
      }
      setSubmitted(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <div className="flex flex-col items-center gap-4 py-16 text-center">
        <CheckCircle2 className="h-12 w-12 text-emerald-500" />
        <h2 className="text-xl font-bold">Screening Submitted</h2>
        <p className="text-muted-foreground">
          Thank you! The hiring team will review your responses and reach out
          with next steps.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-muted-foreground">
        Please answer the following questions. Take your time — there are no
        trick questions.
      </p>
      {questions.map((q, i) => (
        <div key={q.id} className="space-y-2">
          <label className="block text-sm font-medium">
            {i + 1}. {q.question}
            {q.required !== false && (
              <span className="ml-1 text-destructive">*</span>
            )}
          </label>
          <textarea
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 min-h-[100px]"
            placeholder="Type your answer..."
            value={answers[q.id] || ""}
            onChange={(e) =>
              setAnswers((p) => ({ ...p, [q.id]: e.target.value }))
            }
          />
        </div>
      ))}
      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {error}
        </div>
      )}
      <button
        onClick={handleSubmit}
        disabled={submitting}
        className="inline-flex items-center gap-2 rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        {submitting ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Send className="h-4 w-4" />
        )}
        Submit Screening
      </button>
    </div>
  );
}

function AssignmentForm({
  ctx,
  token,
}: {
  ctx: ApplyContext;
  token: string;
}) {
  const [form, setForm] = useState({
    project_choice: "",
    github_url: "",
    loom_url: "",
    deployed_url: "",
    notes: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    setError(null);
    if (!form.project_choice.trim()) {
      setError("Please enter your project choice");
      return;
    }
    if (!form.github_url.trim()) {
      setError("GitHub URL is required");
      return;
    }
    if (!form.loom_url.trim()) {
      setError("Loom walkthrough URL is required");
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch(`${BASE}/apply/${token}/assignment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_choice: form.project_choice.trim(),
          github_url: form.github_url.trim(),
          loom_url: form.loom_url.trim(),
          deployed_url: form.deployed_url.trim() || null,
          notes: form.notes.trim() || null,
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `Error ${res.status}`);
      }
      setSubmitted(true);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <div className="flex flex-col items-center gap-4 py-16 text-center">
        <CheckCircle2 className="h-12 w-12 text-emerald-500" />
        <h2 className="text-xl font-bold">Assignment Submitted</h2>
        <p className="text-muted-foreground">
          Thank you! The hiring team will review your submission and reach out
          with next steps.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {ctx.assignment_brief && (
        <div className="rounded-lg border bg-muted/30 p-4">
          <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
            <FileText className="h-4 w-4" /> Assignment Brief
          </h3>
          <p className="whitespace-pre-wrap text-sm text-muted-foreground">
            {ctx.assignment_brief}
          </p>
        </div>
      )}

      {ctx.assignment_instructions && (
        <div className="rounded-lg border bg-blue-50 dark:bg-blue-950/20 p-4">
          <h3 className="mb-2 text-sm font-semibold text-blue-700 dark:text-blue-300">
            Instructions
          </h3>
          <p className="whitespace-pre-wrap text-sm">
            {ctx.assignment_instructions}
          </p>
        </div>
      )}

      {ctx.deadline_days && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Clock className="h-4 w-4" />
          Deadline: {ctx.deadline_days} days from assignment date
        </div>
      )}

      {ctx.problem_doc_filename && ctx.problem_doc_url && (
        <a
          href={ctx.problem_doc_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 text-sm text-primary hover:underline"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          {ctx.problem_doc_filename}
        </a>
      )}

      <div className="space-y-4">
        <div>
          <label className="mb-1 block text-sm font-medium">
            Project Choice <span className="text-destructive">*</span>
          </label>
          <input
            type="text"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
            placeholder="Which project/challenge did you pick?"
            value={form.project_choice}
            onChange={(e) =>
              setForm((p) => ({ ...p, project_choice: e.target.value }))
            }
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">
            GitHub Repository URL <span className="text-destructive">*</span>
          </label>
          <input
            type="url"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
            placeholder="https://github.com/username/repo"
            value={form.github_url}
            onChange={(e) =>
              setForm((p) => ({ ...p, github_url: e.target.value }))
            }
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">
            Loom Walkthrough URL <span className="text-destructive">*</span>
          </label>
          <input
            type="url"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
            placeholder="https://www.loom.com/share/..."
            value={form.loom_url}
            onChange={(e) =>
              setForm((p) => ({ ...p, loom_url: e.target.value }))
            }
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">
            Deployed URL (optional)
          </label>
          <input
            type="url"
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
            placeholder="https://your-app.vercel.app"
            value={form.deployed_url}
            onChange={(e) =>
              setForm((p) => ({ ...p, deployed_url: e.target.value }))
            }
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">
            Additional Notes (optional)
          </label>
          <textarea
            className="w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 min-h-[80px]"
            placeholder="Any context, trade-offs, or things you'd do differently..."
            value={form.notes}
            onChange={(e) =>
              setForm((p) => ({ ...p, notes: e.target.value }))
            }
          />
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <AlertTriangle className="h-4 w-4 shrink-0" /> {error}
        </div>
      )}

      <button
        onClick={handleSubmit}
        disabled={submitting}
        className="inline-flex items-center gap-2 rounded-lg bg-primary px-6 py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
      >
        {submitting ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Send className="h-4 w-4" />
        )}
        Submit Assignment
      </button>
    </div>
  );
}

export default function ApplyPage() {
  const { token } = useParams<{ token: string }>();
  const { data, error, isLoading } = useSWR<ApplyContext>(
    token ? `/apply/${token}` : null,
    applyFetcher
  );

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !data) {
    const msg =
      error?.message?.includes("invalid_token") || error?.message?.includes("401")
        ? "This link has expired or is invalid."
        : error?.message || "Something went wrong.";
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <div className="max-w-md text-center">
          <AlertTriangle className="mx-auto mb-4 h-10 w-10 text-destructive" />
          <h1 className="text-lg font-bold">Unable to load</h1>
          <p className="mt-2 text-sm text-muted-foreground">{msg}</p>
        </div>
      </div>
    );
  }

  if (data.already_submitted) {
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <div className="max-w-md text-center">
          <CheckCircle2 className="mx-auto mb-4 h-10 w-10 text-emerald-500" />
          <h1 className="text-lg font-bold">Already Submitted</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            You have already submitted your{" "}
            {data.action === "screening" ? "screening answers" : "assignment"}.
            The hiring team will review and follow up via email.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-10">
      <div className="mb-8">
        <h1 className="text-2xl font-bold">
          {data.action === "screening"
            ? "Screening Questions"
            : "Assignment Submission"}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {data.role_title}
          {data.candidate_name && ` — ${data.candidate_name}`}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          Expires:{" "}
          {new Date(data.expires_at).toLocaleDateString("en-IN", {
            day: "numeric",
            month: "long",
            year: "numeric",
          })}
        </p>
      </div>

      {data.action === "screening" ? (
        <ScreeningForm ctx={data} token={token} />
      ) : (
        <AssignmentForm ctx={data} token={token} />
      )}
    </div>
  );
}
