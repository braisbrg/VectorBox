import { Skeleton } from "@/components/ui/skeleton";
import { GridPattern } from "@/components/tweak/grid-pattern";

/**
 * Streaming Loading State - Shown by Next.js while page.tsx awaits server data.
 * Renders the App Shell + Feed Skeleton for seamless UX.
 */
export default function Loading() {
    return (
        <main className="min-h-screen bg-zinc-950 text-primary relative">
            {/* Ambient Background - matches Dashboard */}
            <GridPattern className="opacity-10 fixed inset-0 z-0" strokeColor="rgba(204, 255, 0, 0.08)" />

            {/* App Shell Skeleton */}
            <div className="lg:pl-[80px] transition-all duration-300 min-h-screen flex flex-col pt-[60px] lg:pt-0">
                {/* Hero Section Skeleton */}
                <section className="relative py-12 overflow-hidden border-b border-zinc-800">
                    <div className="container relative z-10 px-6 mx-auto">
                        <div className="space-y-4">
                            <Skeleton className="h-16 w-80 bg-zinc-800/60" />
                            <Skeleton className="h-6 w-64 bg-zinc-800/40" />
                        </div>
                    </div>
                </section>

                {/* Filter Bar Skeleton */}
                <div className="container px-4 mx-auto pb-20">
                    <div className="flex flex-col md:flex-row gap-6 mb-8 p-6 bg-zinc-900/50 backdrop-blur-sm border border-zinc-800/50 rounded-xl shadow-sm mt-8">
                        <div className="flex-1 flex flex-col gap-4">
                            <div className="flex flex-wrap gap-4 items-center">
                                <Skeleton className="h-10 w-48 rounded-lg bg-zinc-800/60" />
                                <Skeleton className="h-10 w-32 rounded-lg bg-zinc-800/40" />
                            </div>
                            <div className="flex flex-wrap gap-2">
                                {[1, 2, 3, 4].map((i) => (
                                    <Skeleton key={`tag-${i}`} className="h-8 w-24 rounded-full bg-zinc-800/30" />
                                ))}
                            </div>
                        </div>
                    </div>

                    {/* Feed Sections Skeleton */}
                    <div className="space-y-12">
                        {[1, 2, 3, 4].map((i) => (
                            <div key={`section-${i}`} className="space-y-4">
                                <div className="flex items-center gap-2 px-1">
                                    <Skeleton className="h-6 w-48 bg-zinc-800/60" />
                                </div>
                                <div className="flex gap-4 overflow-hidden px-1">
                                    {[1, 2, 3, 4, 5, 6].map((j) => (
                                        <Skeleton
                                            key={j}
                                            className="h-[280px] w-[200px] rounded-lg flex-shrink-0 bg-zinc-800/40 animate-pulse"
                                        />
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </main>
    );
}
