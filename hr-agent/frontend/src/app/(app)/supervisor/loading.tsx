import { Skeleton } from "@/components/skeleton";

export default function SupervisorLoading() {
  return (
    <div className="flex-1 overflow-auto">
      <div className="border-b border-border bg-background px-8 py-4">
        <Skeleton className="h-6 w-52" />
        <Skeleton className="mt-1.5 h-3 w-64" />
      </div>
      <div className="px-8 py-6 space-y-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </div>
        <Skeleton className="h-10 w-full rounded-lg" />
        <Skeleton className="h-96 rounded-xl" />
      </div>
    </div>
  );
}
