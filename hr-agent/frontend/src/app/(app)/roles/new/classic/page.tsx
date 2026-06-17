"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import Image from "next/image";
import { ArrowRight } from "lucide-react";
import { Topbar } from "@/components/layout/topbar";
import { RoleForm } from "@/components/role-form";
import { api } from "@/lib/api";
import { getDashboardKey } from "@/lib/auth";
import type { Role } from "@/lib/types";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

const empty: Omit<Role, "id" | "created_at"> = {
  title: "",
  jd_text: "",
  screening_questions: [],
  scoring_rubric: {},
  cut_line: 60,
  interviewer_panel: [],
  status: "open",
  ctc_min_lpa: null,
  ctc_max_lpa: null,
  max_notice_days: null,
  location: null,
  remote_policy: null,
  assignment_brief: null,
  assignment_instructions: null,
  assignment_deadline_days: 7,
  screening_modality: "voice",
  pipeline_template: null,
};

export default function ClassicRolePage() {
  const router = useRouter();
  return (
    <>
      <Topbar title="New role · classic form" subtitle="every field, manually" />
      <div className="flex-1 overflow-auto bg-muted/30">
        <div className="mx-auto max-w-5xl px-8 py-6 pb-24">
          <Link
            href="/roles/new"
            className="group mb-4 flex items-center justify-between gap-3 rounded-xl border border-primary/30 bg-gradient-to-r from-primary/10 via-primary/5 to-transparent px-4 py-3 transition hover:border-primary/60 hover:shadow-card"
          >
            <span className="flex items-center gap-3">
              <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/15 ring-1 ring-primary/30">
                <span
                  className="absolute inset-0 animate-ping rounded-full bg-primary/20"
                  aria-hidden
                />
                <Image
                  src="/brand/icon/icon-on-light.svg"
                  alt=""
                  width={20}
                  height={20}
                  className="relative z-10"
                />
              </span>
              <span className="flex flex-col">
                <span className="text-sm font-bold text-foreground">
                  Skip the form — chat with the agent
                </span>
                <span className="text-[11px] text-muted-foreground">
                  describe the role in one line, the agent fills the rest
                </span>
              </span>
            </span>
            <ArrowRight className="h-4 w-4 text-primary transition group-hover:translate-x-0.5" />
          </Link>
          <RoleForm
            initial={empty}
            submitLabel="Create role"
            onSubmit={async (r, file) => {
              const created = await api.post<Role>("/dashboard/roles", r);
              if (file) {
                const fd = new FormData();
                fd.append("file", file);
                const key = getDashboardKey();
                const resp = await fetch(
                  `${BASE}/dashboard/roles/${created.id}/problem-doc`,
                  {
                    method: "POST",
                    headers: key ? { "X-Dashboard-Key": key } : {},
                    body: fd,
                  },
                );
                if (!resp.ok) {
                  const b = await resp.json().catch(() => ({}));
                  throw new Error(b.detail ?? `doc upload failed: ${resp.status}`);
                }
              }
              router.push(`/roles/${created.id}`);
            }}
          />
        </div>
      </div>
    </>
  );
}

