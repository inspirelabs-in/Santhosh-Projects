"use client";

import { useState } from "react";
import { Download, FileSpreadsheet, FileText, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getDashboardKey } from "@/lib/auth";
import { cn } from "@/lib/utils";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export interface ExportOption {
  label: string;
  path: string;
  filename: string;
  icon?: "spreadsheet" | "text";
}

interface ExportMenuProps {
  options: ExportOption[];
  label?: string;
  className?: string;
}

export function ExportMenu({ options, label = "Export", className }: ExportMenuProps) {
  const [downloading, setDownloading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleExport(option: ExportOption) {
    setDownloading(option.path);
    setError(null);
    try {
      const key = getDashboardKey();
      const res = await fetch(`${BASE}${option.path}`, {
        headers: { "X-Dashboard-Key": key || "" },
      });
      if (!res.ok) {
        setError(res.status === 401 ? "Unauthorized" : `Export failed (${res.status})`);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = option.filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("Export failed");
    } finally {
      setDownloading(null);
    }
  }

  const isDownloading = downloading !== null;

  if (options.length === 1) {
    const opt = options[0];
    return (
      <Button
        variant="outline"
        size="sm"
        disabled={isDownloading}
        onClick={() => handleExport(opt)}
        className={className}
      >
        {isDownloading ? (
          <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
        ) : (
          <Download className="mr-1.5 h-3.5 w-3.5" />
        )}
        {opt.label}
      </Button>
    );
  }

  return (
    <div className={cn("relative inline-flex", className)}>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" disabled={isDownloading}>
            {isDownloading ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="mr-1.5 h-3.5 w-3.5" />
            )}
            {label}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {options.map((opt) => {
            const Icon = opt.icon === "text" ? FileText : FileSpreadsheet;
            const busy = downloading === opt.path;
            return (
              <DropdownMenuItem
                key={opt.path}
                disabled={isDownloading}
                onSelect={() => handleExport(opt)}
              >
                {busy ? (
                  <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Icon className="mr-2 h-3.5 w-3.5" />
                )}
                {opt.label}
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>
      {error && (
        <span className="absolute right-0 top-full mt-1 whitespace-nowrap rounded border border-destructive/30 bg-destructive/10 px-2 py-0.5 font-mono text-[10px] text-destructive">
          {error}
        </span>
      )}
    </div>
  );
}
