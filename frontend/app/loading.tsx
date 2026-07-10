import { Skeleton } from "@/components/ui/skeleton";

// Route-level loading — states gallery 01 "loading — vector compute":
// skeleton feed rows + the compute line, pure ACID tokens.
export default function Loading() {
    return (
        <main className="min-h-screen bg-bg p-6 lg:pl-[204px] lg:pr-10" role="status" aria-label="Loading" aria-live="polite">
            <div className="space-y-10 pt-6">
                {[1, 2, 3, 4].map((i) => (
                    <div key={i} className="space-y-3">
                        <Skeleton className="h-5 w-40" />
                        <div className="flex gap-2.5 overflow-hidden md:gap-3">
                            {[1, 2, 3, 4, 5, 6].map((j) => (
                                <Skeleton key={j} className="h-[210px] w-[118px] shrink-0 md:h-[270px] md:w-[180px]" />
                            ))}
                        </div>
                    </div>
                ))}
                <p className="font-mono text-[10px] uppercase tracking-widest text-fg-3">computing_taste_vector…</p>
            </div>
        </main>
    );
}
