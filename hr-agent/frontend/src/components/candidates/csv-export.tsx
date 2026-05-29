"use client";

import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import Papa from "papaparse";

interface Row {
  name: string | null;
  email: string | null;
  role_title: string | null;
  current_stage: string;
  screening_score: number | null;
  updated_at: string;
}

export function CsvExportButton({ data, filename = "candidates.csv" }: { data: Row[]; filename?: string }) {
  function download() {
    const rows = data.map((c) => ({
      Name: c.name ?? "",
      Email: c.email ?? "",
      Role: c.role_title ?? "",
      Stage: c.current_stage,
      Score: c.screening_score ?? "",
      Updated: c.updated_at,
    }));
    const csv = Papa.unparse(rows);
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Button variant="outline" size="sm" onClick={download} disabled={data.length === 0}>
      <Download className="mr-1 h-3.5 w-3.5" /> Export CSV
    </Button>
  );
}
