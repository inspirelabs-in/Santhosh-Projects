"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import useSWR from "swr";
import {
  CheckCircle2,
  Circle,
  Clock,
  Loader2,
  XCircle,
} from "lucide-react";

interface TimelineEvent {
  date: string;
  label: string;
  description?: string;
}

interface PortalStatus {
  candidate_name: string;
  role_title: string;
  current_stage_label: string;
  current_stage_description: string;
  next_steps: string;
  applied_at: string;
  last_updated: string;
  timeline: TimelineEvent[];
  progress_percent: number;
}

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

async function portalFetcher(url: string) {
  const res = await fetch(`${BASE}${url}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Error ${res.status}`);
  }
  return res.json();
}

function PortalContent() {
  const params = useSearchParams();
  const token = params.get("token");

  const { data, error, isLoading } = useSWR<PortalStatus>(
    token ? `/portal/status?token=${encodeURIComponent(token)}` : null,
    portalFetcher,
    { refreshInterval: 60_000 }
  );

  if (!token) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50">
        <div className="rounded-lg border bg-white p-8 shadow-sm max-w-md text-center">
          <XCircle className="mx-auto h-12 w-12 text-red-400 mb-4" />
          <h2 className="text-lg font-semibold text-gray-900">Invalid Link</h2>
          <p className="mt-2 text-sm text-gray-600">
            This link appears to be invalid. Please use the link from your
            application email.
          </p>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50">
        <Loader2 className="h-8 w-8 animate-spin text-blue-500" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50">
        <div className="rounded-lg border bg-white p-8 shadow-sm max-w-md text-center">
          <XCircle className="mx-auto h-12 w-12 text-red-400 mb-4" />
          <h2 className="text-lg font-semibold text-gray-900">
            Unable to Load Status
          </h2>
          <p className="mt-2 text-sm text-gray-600">{error.message}</p>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const isRejected = data.current_stage_label === "Application Closed";
  const isHired = data.current_stage_label === "Welcome Aboard!";

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="border-b bg-white">
        <div className="mx-auto max-w-2xl px-4 py-6">
          <h1 className="text-xl font-bold text-gray-900">
            Application Status
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            {data.candidate_name} &middot; {data.role_title}
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-2xl px-4 py-8 space-y-6">
        {/* Current Status Card */}
        <div className="rounded-lg border bg-white p-6 shadow-sm">
          <div className="flex items-start gap-4">
            <div
              className={`mt-1 rounded-full p-2 ${
                isRejected
                  ? "bg-red-100"
                  : isHired
                  ? "bg-green-100"
                  : "bg-blue-100"
              }`}
            >
              {isRejected ? (
                <XCircle className="h-6 w-6 text-red-600" />
              ) : isHired ? (
                <CheckCircle2 className="h-6 w-6 text-green-600" />
              ) : (
                <Clock className="h-6 w-6 text-blue-600" />
              )}
            </div>
            <div className="flex-1">
              <h2 className="text-lg font-semibold text-gray-900">
                {data.current_stage_label}
              </h2>
              <p className="mt-1 text-sm text-gray-600">
                {data.current_stage_description}
              </p>
            </div>
          </div>

          {/* Progress bar */}
          {!isRejected && (
            <div className="mt-6">
              <div className="flex justify-between text-xs text-gray-500 mb-1">
                <span>Applied</span>
                <span>{data.progress_percent}% complete</span>
              </div>
              <div className="h-2 w-full rounded-full bg-gray-200">
                <div
                  className={`h-2 rounded-full transition-all ${
                    isHired ? "bg-green-500" : "bg-blue-500"
                  }`}
                  style={{ width: `${data.progress_percent}%` }}
                />
              </div>
            </div>
          )}
        </div>

        {/* Next Steps */}
        {!isRejected && !isHired && (
          <div className="rounded-lg border bg-blue-50 p-4">
            <h3 className="text-sm font-medium text-blue-900">Next Steps</h3>
            <p className="mt-1 text-sm text-blue-700">{data.next_steps}</p>
          </div>
        )}

        {/* Timeline */}
        {data.timeline.length > 0 && (
          <div className="rounded-lg border bg-white p-6 shadow-sm">
            <h3 className="text-sm font-medium text-gray-900 mb-4">
              Timeline
            </h3>
            <div className="space-y-4">
              {data.timeline.map((event, i) => (
                <div key={i} className="flex gap-3">
                  <div className="flex flex-col items-center">
                    <div
                      className={`rounded-full p-1 ${
                        i === data.timeline.length - 1
                          ? "bg-blue-500"
                          : "bg-gray-300"
                      }`}
                    >
                      <Circle
                        className={`h-2 w-2 ${
                          i === data.timeline.length - 1
                            ? "text-white"
                            : "text-white"
                        }`}
                        fill="currentColor"
                      />
                    </div>
                    {i < data.timeline.length - 1 && (
                      <div className="w-px flex-1 bg-gray-200 mt-1" />
                    )}
                  </div>
                  <div className="pb-4">
                    <p className="text-sm font-medium text-gray-900">
                      {event.label}
                    </p>
                    <p className="text-xs text-gray-500">{event.date}</p>
                    {event.description && (
                      <p className="mt-1 text-xs text-gray-600">
                        {event.description}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Footer info */}
        <div className="text-center text-xs text-gray-400 py-4">
          <p>Applied: {data.applied_at} &middot; Last updated: {data.last_updated}</p>
          <p className="mt-1">
            This page updates automatically. No need to refresh.
          </p>
        </div>
      </main>
    </div>
  );
}

export default function PortalPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-gray-50">
          <Loader2 className="h-8 w-8 animate-spin text-blue-500" />
        </div>
      }
    >
      <PortalContent />
    </Suspense>
  );
}
