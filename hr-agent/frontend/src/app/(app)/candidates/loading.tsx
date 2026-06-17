import { Skeleton } from "@/components/skeleton";

export default function CandidatesLoading() {
  return (
    <div className="flex-1 overflow-auto">
      <div className="border-b border-border bg-background px-8 py-4">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="mt-1.5 h-3 w-56" />
      </div>
      <div className="px-8 py-6 space-y-3">
        <div className="flex gap-3">
          <Skeleton className="h-10 w-80" />
          <Skeleton className="h-10 w-32" />
        </div>
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-16 rounded-lg" />
        ))}
      </div>
    </div>
  );
}
