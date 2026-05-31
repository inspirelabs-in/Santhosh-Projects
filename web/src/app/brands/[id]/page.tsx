"use client";

import { useParams, useRouter } from "next/navigation";
import { useState, useCallback } from "react";
import { BrandDetail } from "@/components/brand-panel";
import { PipelineRail } from "@/components/pipeline-rail";

export default function BrandPage() {
  const params = useParams();
  const router = useRouter();
  const initialId = Number(params.id);
  const [selectedBrand, setSelectedBrand] = useState<number>(isNaN(initialId) ? 0 : initialId);

  const handleSelectBrand = useCallback((id: number) => {
    setSelectedBrand(id);
    window.history.replaceState(null, "", `/brands/${id}`);
  }, []);

  if (!selectedBrand) {
    return (
      <main className="flex h-full items-center justify-center bg-[var(--surface)]">
        <div className="text-[var(--text-muted)] text-sm">Select a brand from the sidebar</div>
      </main>
    );
  }

  return (
    <main className="flex h-full bg-[var(--surface)]">
      <aside className="hidden md:flex w-[300px] shrink-0 border-r border-[var(--border)]">
        <PipelineRail onSelectBrand={handleSelectBrand} selectedBrandId={selectedBrand} />
      </aside>
      <div className="flex-1 min-w-0">
        <BrandDetail brandId={selectedBrand} fullPage />
      </div>
    </main>
  );
}
