import { Skeleton } from "@/components/skeleton";

export default function AuditLoading() {
  return (
    <div className="flex-1 overflow-auto">
      <div className="border-b border-border bg-background px-8 py-4">
        <Skeleton className="h-6 w-24" />
        <Skeleton className="mt-1.5 h-3 w-72" />
      </div>
      <div className="px-8 py-6 space-y-3">
        <Skeleton className="h-10 w-96" />
        {Array.from({ length: 10 }).map((_, i) => (
          <Skeleton key={i} className="h-14 rounded-lg" />
        ))}
      </div>
    </div>
  );
}
