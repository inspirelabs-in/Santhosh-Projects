"use client";

import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";

import { Topbar } from "@/components/layout/topbar";
import { Button } from "@/components/ui/button";
import { AgenticJourney } from "@/components/agentic-journey";

export default function HRDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  return (
    <>
      <Topbar title="HR review" subtitle={id} />
      <div className="flex-1 overflow-auto px-8 py-6 pb-24">
        <div className="mb-4">
          <Button variant="ghost" size="sm" onClick={() => router.back()} asChild>
            <Link href="/hr">
              <ArrowLeft className="mr-1.5 h-4 w-4" />
              Back to queue
            </Link>
          </Button>
        </div>
        {id && <AgenticJourney applicationId={id} />}
      </div>
    </>
  );
}
