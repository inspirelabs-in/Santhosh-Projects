import { Skeleton } from "@/components/skeleton";

export default function AnalyticsLoading() {
  return (
    <div className="flex-1 overflow-auto">
      <div className="border-b border-border bg-background px-8 py-4">
        <Skeleton className="h-6 w-28" />
        <Skeleton className="mt-1.5 h-3 w-48" />
      </div>
      <div className="mx-auto max-w-6xl px-8 py-6 space-y-6">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
        <Skeleton className="h-72 rounded-xl" />
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          <Skeleton className="h-60 rounded-xl" />
          <Skeleton className="h-60 rounded-xl" />
        </div>
      </div>
    </div>
  );
}
