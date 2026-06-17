"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import {
  UserCheck,
  Plus,
  Pencil,
  Trash2,
  X,
  Check,
  Loader2,
  ChevronDown,
  ChevronUp,
  Calendar,
  Mail,
  Briefcase,
  Eye,
  EyeOff,
} from "lucide-react";

import { Topbar } from "@/components/layout/topbar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, swrFetcher } from "@/lib/api";
import { cn } from "@/lib/utils";

type RoleType = "technical" | "hr" | "ceo";
type CalendarProvider = "microsoft" | "google" | "none";

interface PanelMember {
  id: string;
  name: string;
  email: string;
  role_type: RoleType;
  job_title: string | null;
  timezone: string;
  calendar_provider: CalendarProvider;
  calendar_id: string | null;
  expertise_tags: string[] | null;
  department: string | null;
  max_interviews_per_week: number;
  seniority_level: string | null;
  is_active: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

type TabKey = "all" | "technical" | "hr" | "ceo";

const ROLE_TYPE_COLORS: Record<RoleType, string> = {
  technical: "bg-blue-500/15 text-blue-400 border-blue-500/20",
  hr: "bg-emerald-500/15 text-emerald-400 border-emerald-500/20",
  ceo: "bg-amber-500/15 text-amber-400 border-amber-500/20",
};

const CALENDAR_LABELS: Record<CalendarProvider, string> = {
  microsoft: "Teams",
  google: "Google",
  none: "None",
};

const EMPTY_FORM = {
  name: "",
  email: "",
  role_type: "technical" as RoleType,
  job_title: "",
  timezone: "Asia/Kolkata",
  calendar_provider: "microsoft" as CalendarProvider,
  calendar_id: "",
  expertise_tags: "",
  department: "",
  max_interviews_per_week: 10,
  seniority_level: "",
  notes: "",
};

type FormState = typeof EMPTY_FORM;

function formFromMember(m: PanelMember): FormState {
  return {
    name: m.name,
    email: m.email,
    role_type: m.role_type,
    job_title: m.job_title ?? "",
    timezone: m.timezone,
    calendar_provider: m.calendar_provider,
    calendar_id: m.calendar_id ?? "",
    expertise_tags: (m.expertise_tags ?? []).join(", "),
    department: m.department ?? "",
    max_interviews_per_week: m.max_interviews_per_week,
    seniority_level: m.seniority_level ?? "",
    notes: m.notes ?? "",
  };
}

function formToPayload(f: FormState) {
  return {
    name: f.name.trim(),
    email: f.email.trim(),
    role_type: f.role_type,
    job_title: f.job_title.trim() || null,
    timezone: f.timezone.trim() || "Asia/Kolkata",
    calendar_provider: f.calendar_provider,
    calendar_id: f.calendar_id.trim() || null,
    expertise_tags: f.expertise_tags
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean),
    department: f.department.trim() || null,
    max_interviews_per_week: f.max_interviews_per_week,
    seniority_level: f.seniority_level.trim() || null,
    notes: f.notes.trim() || null,
  };
}

function PanelForm({
  form,
  setForm,
  onSubmit,
  onCancel,
  saving,
  submitLabel,
}: {
  form: FormState;
  setForm: (fn: (prev: FormState) => FormState) => void;
  onSubmit: () => void;
  onCancel: () => void;
  saving: boolean;
  submitLabel: string;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 rounded-lg border border-border bg-card p-5 sm:grid-cols-2 lg:grid-cols-3">
      <div>
        <Label className="mb-1 text-xs">Name *</Label>
        <Input
          value={form.name}
          onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
          placeholder="John Doe"
        />
      </div>
      <div>
        <Label className="mb-1 text-xs">Email *</Label>
        <Input
          type="email"
          value={form.email}
          onChange={(e) => setForm((p) => ({ ...p, email: e.target.value }))}
          placeholder="john@company.com"
        />
      </div>
      <div>
        <Label className="mb-1 text-xs">Role Type *</Label>
        <select
          value={form.role_type}
          onChange={(e) =>
            setForm((p) => ({ ...p, role_type: e.target.value as RoleType }))
          }
          className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <option value="technical">Technical</option>
          <option value="hr">HR</option>
          <option value="ceo">CEO</option>
        </select>
      </div>
      <div>
        <Label className="mb-1 text-xs">Job Title</Label>
        <Input
          value={form.job_title}
          onChange={(e) => setForm((p) => ({ ...p, job_title: e.target.value }))}
          placeholder="Engineering Manager"
        />
      </div>
      <div>
        <Label className="mb-1 text-xs">Department</Label>
        <Input
          value={form.department}
          onChange={(e) =>
            setForm((p) => ({ ...p, department: e.target.value }))
          }
          placeholder="Engineering"
        />
      </div>
      <div>
        <Label className="mb-1 text-xs">Timezone</Label>
        <Input
          value={form.timezone}
          onChange={(e) => setForm((p) => ({ ...p, timezone: e.target.value }))}
          placeholder="Asia/Kolkata"
        />
      </div>
      <div>
        <Label className="mb-1 text-xs">Calendar Provider</Label>
        <select
          value={form.calendar_provider}
          onChange={(e) =>
            setForm((p) => ({
              ...p,
              calendar_provider: e.target.value as CalendarProvider,
            }))
          }
          className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <option value="microsoft">Microsoft Teams</option>
          <option value="google">Google Calendar</option>
          <option value="none">None</option>
        </select>
      </div>
      <div>
        <Label className="mb-1 text-xs">Calendar ID</Label>
        <Input
          value={form.calendar_id}
          onChange={(e) =>
            setForm((p) => ({ ...p, calendar_id: e.target.value }))
          }
          placeholder="john@company.com"
        />
      </div>
      <div>
        <Label className="mb-1 text-xs">Max Interviews / Week</Label>
        <Input
          type="number"
          min={1}
          max={50}
          value={form.max_interviews_per_week}
          onChange={(e) =>
            setForm((p) => ({
              ...p,
              max_interviews_per_week: parseInt(e.target.value) || 10,
            }))
          }
        />
      </div>
      <div>
        <Label className="mb-1 text-xs">Seniority Level</Label>
        <Input
          value={form.seniority_level}
          onChange={(e) =>
            setForm((p) => ({ ...p, seniority_level: e.target.value }))
          }
          placeholder="Senior / Lead / Director"
        />
      </div>
      <div className="sm:col-span-2">
        <Label className="mb-1 text-xs">Expertise Tags (comma-separated)</Label>
        <Input
          value={form.expertise_tags}
          onChange={(e) =>
            setForm((p) => ({ ...p, expertise_tags: e.target.value }))
          }
          placeholder="Python, System Design, React"
        />
      </div>
      <div className="sm:col-span-2 lg:col-span-3">
        <Label className="mb-1 text-xs">Notes</Label>
        <Input
          value={form.notes}
          onChange={(e) => setForm((p) => ({ ...p, notes: e.target.value }))}
          placeholder="Preferred for backend roles, available after 2 PM"
        />
      </div>
      <div className="flex items-center gap-2 sm:col-span-2 lg:col-span-3">
        <Button onClick={onSubmit} disabled={saving} size="sm">
          {saving ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Check className="mr-1.5 h-3.5 w-3.5" />
          )}
          {submitLabel}
        </Button>
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={saving}>
          <X className="mr-1.5 h-3.5 w-3.5" />
          Cancel
        </Button>
      </div>
    </div>
  );
}

export default function PanelsPage() {
  const [tab, setTab] = useState<TabKey>("all");
  const [showInactive, setShowInactive] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState<FormState>({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<FormState>({ ...EMPTY_FORM });
  const [editSaving, setEditSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const queryParams = new URLSearchParams();
  if (tab !== "all") queryParams.set("role_type", tab);
  if (showInactive) queryParams.set("include_inactive", "true");
  const qs = queryParams.toString();

  const { data, isLoading, mutate } = useSWR<PanelMember[]>(
    `/dashboard/panels${qs ? `?${qs}` : ""}`,
    swrFetcher,
    { refreshInterval: 30_000 },
  );

  const members = data ?? [];

  const counts = useMemo(() => {
    const all = data ?? [];
    return {
      all: all.length,
      technical: all.filter((m) => m.role_type === "technical").length,
      hr: all.filter((m) => m.role_type === "hr").length,
      ceo: all.filter((m) => m.role_type === "ceo").length,
    };
  }, [data]);

  async function handleAdd() {
    if (!addForm.name.trim() || !addForm.email.trim()) {
      setError("Name and email are required");
      return;
    }
    setError(null);
    setSaving(true);
    try {
      await api.post("/dashboard/panels", formToPayload(addForm));
      setShowAdd(false);
      setAddForm({ ...EMPTY_FORM });
      mutate();
    } catch (e: any) {
      setError(e.message || "Failed to create");
    } finally {
      setSaving(false);
    }
  }

  async function handleEdit(id: string) {
    if (!editForm.name.trim() || !editForm.email.trim()) {
      setError("Name and email are required");
      return;
    }
    setError(null);
    setEditSaving(true);
    try {
      await api.patch(`/dashboard/panels/${id}`, formToPayload(editForm));
      setEditId(null);
      mutate();
    } catch (e: any) {
      setError(e.message || "Failed to update");
    } finally {
      setEditSaving(false);
    }
  }

  async function handleDelete(id: string, name: string) {
    if (!confirm(`Deactivate ${name}? They won't be assigned to new interviews.`))
      return;
    try {
      await api.del(`/dashboard/panels/${id}`);
      mutate();
    } catch (e: any) {
      setError(e.message || "Failed to deactivate");
    }
  }

  function startEdit(m: PanelMember) {
    setEditId(m.id);
    setEditForm(formFromMember(m));
    setExpandedId(m.id);
    setError(null);
  }

  return (
    <>
      <Topbar title="Panel Members" subtitle="interview panel directory" />
      <div className="flex-1 overflow-auto">
        <div className="mx-auto max-w-6xl px-8 py-6 pb-24">
          {/* Header row */}
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
            <Tabs value={tab} onValueChange={(v) => setTab(v as TabKey)}>
              <TabsList>
                <TabsTrigger value="all">
                  All{" "}
                  <span className="ml-1 text-xs text-muted-foreground">
                    {counts.all}
                  </span>
                </TabsTrigger>
                <TabsTrigger value="technical">
                  Technical{" "}
                  <span className="ml-1 text-xs text-muted-foreground">
                    {counts.technical}
                  </span>
                </TabsTrigger>
                <TabsTrigger value="hr">
                  HR{" "}
                  <span className="ml-1 text-xs text-muted-foreground">
                    {counts.hr}
                  </span>
                </TabsTrigger>
                <TabsTrigger value="ceo">
                  CEO{" "}
                  <span className="ml-1 text-xs text-muted-foreground">
                    {counts.ceo}
                  </span>
                </TabsTrigger>
              </TabsList>
            </Tabs>

            <div className="flex items-center gap-3">
              <button
                onClick={() => setShowInactive(!showInactive)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
                  showInactive
                    ? "border-primary/30 bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {showInactive ? (
                  <Eye className="h-3.5 w-3.5" />
                ) : (
                  <EyeOff className="h-3.5 w-3.5" />
                )}
                {showInactive ? "Showing inactive" : "Hide inactive"}
              </button>
              <Button
                size="sm"
                onClick={() => {
                  setShowAdd(!showAdd);
                  setError(null);
                }}
              >
                <Plus className="mr-1.5 h-3.5 w-3.5" />
                Add Member
              </Button>
            </div>
          </div>

          {/* Error banner */}
          {error && (
            <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-2.5 text-sm text-destructive">
              {error}
            </div>
          )}

          {/* Add form */}
          {showAdd && (
            <div className="mb-6">
              <PanelForm
                form={addForm}
                setForm={setAddForm}
                onSubmit={handleAdd}
                onCancel={() => {
                  setShowAdd(false);
                  setError(null);
                }}
                saving={saving}
                submitLabel="Add Panel Member"
              />
            </div>
          )}

          {/* Loading */}
          {isLoading && (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {/* Empty state */}
          {!isLoading && members.length === 0 && (
            <div className="flex flex-col items-center justify-center gap-3 py-20 text-center">
              <UserCheck className="h-10 w-10 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">
                No panel members{tab !== "all" ? ` for ${tab}` : ""}.
              </p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowAdd(true)}
              >
                <Plus className="mr-1.5 h-3.5 w-3.5" /> Add your first member
              </Button>
            </div>
          )}

          {/* Table */}
          {!isLoading && members.length > 0 && (
            <div className="overflow-hidden rounded-lg border border-border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/40">
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">
                      Name
                    </th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">
                      Email
                    </th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">
                      Type
                    </th>
                    <th className="hidden px-4 py-2.5 text-left text-xs font-medium text-muted-foreground md:table-cell">
                      Department
                    </th>
                    <th className="hidden px-4 py-2.5 text-center text-xs font-medium text-muted-foreground lg:table-cell">
                      Max/wk
                    </th>
                    <th className="hidden px-4 py-2.5 text-left text-xs font-medium text-muted-foreground lg:table-cell">
                      Calendar
                    </th>
                    <th className="px-4 py-2.5 text-center text-xs font-medium text-muted-foreground">
                      Status
                    </th>
                    <th className="px-4 py-2.5 text-right text-xs font-medium text-muted-foreground">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((m) => (
                    <MemberRow
                      key={m.id}
                      member={m}
                      isExpanded={expandedId === m.id}
                      isEditing={editId === m.id}
                      editForm={editForm}
                      setEditForm={setEditForm}
                      editSaving={editSaving}
                      onToggle={() =>
                        setExpandedId(expandedId === m.id ? null : m.id)
                      }
                      onEdit={() => startEdit(m)}
                      onCancelEdit={() => {
                        setEditId(null);
                        setError(null);
                      }}
                      onSaveEdit={() => handleEdit(m.id)}
                      onDelete={() => handleDelete(m.id, m.name)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function MemberRow({
  member: m,
  isExpanded,
  isEditing,
  editForm,
  setEditForm,
  editSaving,
  onToggle,
  onEdit,
  onCancelEdit,
  onSaveEdit,
  onDelete,
}: {
  member: PanelMember;
  isExpanded: boolean;
  isEditing: boolean;
  editForm: FormState;
  setEditForm: (fn: (prev: FormState) => FormState) => void;
  editSaving: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <>
      <tr
        className={cn(
          "cursor-pointer border-b border-border transition-colors hover:bg-muted/30",
          !m.is_active && "opacity-50",
          isExpanded && "bg-muted/20",
        )}
        onClick={onToggle}
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            {isExpanded ? (
              <ChevronUp className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            )}
            <div>
              <span className="font-medium">{m.name}</span>
              {m.job_title && (
                <p className="text-xs text-muted-foreground">{m.job_title}</p>
              )}
            </div>
          </div>
        </td>
        <td className="px-4 py-3 text-muted-foreground">{m.email}</td>
        <td className="px-4 py-3">
          <Badge
            className={cn(
              "text-[10px] uppercase",
              ROLE_TYPE_COLORS[m.role_type],
            )}
          >
            {m.role_type}
          </Badge>
        </td>
        <td className="hidden px-4 py-3 text-muted-foreground md:table-cell">
          {m.department || "-"}
        </td>
        <td className="hidden px-4 py-3 text-center lg:table-cell">
          {m.max_interviews_per_week}
        </td>
        <td className="hidden px-4 py-3 lg:table-cell">
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <Calendar className="h-3 w-3" />
            {CALENDAR_LABELS[m.calendar_provider]}
          </span>
        </td>
        <td className="px-4 py-3 text-center">
          <span
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              m.is_active ? "bg-emerald-500" : "bg-zinc-500",
            )}
            title={m.is_active ? "Active" : "Inactive"}
          />
        </td>
        <td className="px-4 py-3 text-right">
          <div
            className="inline-flex gap-1"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={onEdit}
              className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              title="Edit"
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={onDelete}
              className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              title="Deactivate"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        </td>
      </tr>

      {/* Expanded detail / edit form */}
      {isExpanded && (
        <tr>
          <td colSpan={8} className="border-b border-border bg-muted/10 px-4 py-4">
            {isEditing ? (
              <PanelForm
                form={editForm}
                setForm={setEditForm}
                onSubmit={onSaveEdit}
                onCancel={onCancelEdit}
                saving={editSaving}
                submitLabel="Save Changes"
              />
            ) : (
              <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm sm:grid-cols-3 lg:grid-cols-4">
                <Detail label="Timezone" value={m.timezone} />
                <Detail label="Calendar" value={CALENDAR_LABELS[m.calendar_provider]} />
                <Detail label="Calendar ID" value={m.calendar_id} />
                <Detail
                  label="Max Interviews/wk"
                  value={String(m.max_interviews_per_week)}
                />
                <Detail label="Seniority" value={m.seniority_level} />
                <Detail label="Department" value={m.department} />
                <Detail
                  label="Expertise"
                  value={(m.expertise_tags ?? []).join(", ") || null}
                />
                <Detail label="Notes" value={m.notes} />
                <Detail
                  label="Added"
                  value={new Date(m.created_at).toLocaleDateString("en-IN", {
                    day: "numeric",
                    month: "short",
                    year: "numeric",
                  })}
                />
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function Detail({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <p className="text-sm">{value || "-"}</p>
    </div>
  );
}
