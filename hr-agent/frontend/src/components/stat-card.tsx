import { Card, CardContent } from "@/components/ui/card";
import type { LucideIcon } from "lucide-react";

export function StatCard({
  title,
  value,
  subtitle,
  icon: Icon,
  accent = "primary",
}: {
  title: string;
  value: string | number;
  subtitle?: string;
  icon?: LucideIcon;
  accent?: "primary" | "success" | "warning" | "destructive" | "muted";
}) {
  const accentColor = {
    primary: "text-primary",
    success: "text-success",
    warning: "text-warning",
    destructive: "text-destructive",
    muted: "text-muted-foreground",
  }[accent];

  return (
    <Card className="overflow-hidden">
      <CardContent className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <p className="text-sm font-medium text-muted-foreground">{title}</p>
            <p className="text-2xl font-semibold tracking-tight tabular-nums">{value}</p>
            {subtitle ? <p className="text-xs text-muted-foreground">{subtitle}</p> : null}
          </div>
          {Icon ? <Icon className={`h-5 w-5 ${accentColor}`} /> : null}
        </div>
      </CardContent>
    </Card>
  );
}
