"use client";

import { useQuery } from "@tanstack/react-query";
import { MovieCarousel } from "./movie-carousel";
import { UploadZone } from "@/components/upload-zone";
import { AcidError } from "@/components/ui/acid-error";
import { InfoTooltip } from "./info-tooltip";
import { useLanguage } from "@/components/language-provider";
import { getFeed, FeedResponse, VectorboxUser, FeedItem } from "@/lib/api";

interface FeedContainerProps {
    userId: number;
    scope: "global" | "watchlist";
    countryCode?: string;
    streamingProviders?: number[];
    initialData?: FeedResponse | null;
    registeredUsers?: VectorboxUser[];
    onInspect?: (movie: FeedItem, sectionId?: string) => void;
    filteredResults?: FeedItem[] | null;
    isFiltering?: boolean;
    onClearFilterResults?: () => void;
}

const SECTION_DESCRIPTIONS: Record<string, string> = {
    because_you_watched: "Recommendations based on specific movies you've rated highly (4+ stars).",
    niche_picks: "Genre-coherent niche recommendations based on your taste clusters.",
    wildcard: "Unexpected recommendations to help you discover something new.",
    random_picks: "A diverse selection of highly-rated movies from our database.",
    hidden_gems: "Critically acclaimed movies that you might have missed (fewer than 5k votes).",
    available_now: "Movies from your watchlist that are currently available on your streaming services.",
};

// Map strict IDs to translation keys if they don't match exactly
const TITLE_MAP: Record<string, string> = {
    "popular_movies": "sections.popular_letterboxd",
    "hidden_gems": "sections.hidden_gems",
    "random_picks": "sections.random_picks",
    "wildcard": "sections.wildcard",
    "available_now": "sections.available_now"
};

export function FeedContainer({ userId, scope, countryCode = "ES", streamingProviders = [], initialData, registeredUsers, onInspect, filteredResults, isFiltering, onClearFilterResults }: FeedContainerProps) {
    const { data: feedData, isLoading, error } = useQuery<FeedResponse>({
        queryKey: ["feed", userId, scope, countryCode, streamingProviders],
        queryFn: async () => getFeed(scope, countryCode, streamingProviders),
        staleTime: 5 * 60 * 1000, // 5 minutes
        initialData: initialData ?? undefined, // SSR Prefetched Data
    });



    const { t } = useLanguage();

    if (isFiltering) {
        return (
            <div className="flex items-center justify-center py-20 font-mono text-xs text-zinc-500 uppercase tracking-widest">
                EXECUTING_QUERY...
            </div>
        );
    }

    if (filteredResults !== null && filteredResults !== undefined) {
        return (
            <div className="space-y-4">
                <div className="flex items-center justify-between border border-border px-4 py-2 font-mono text-xs">
                    <span className="text-zinc-400">
                        FILTERED VIEW — {filteredResults.length} RESULTS
                    </span>
                    <button
                        onClick={onClearFilterResults}
                        className="text-primary hover:underline"
                    >
                        [ BACK TO FEED ]
                    </button>
                </div>
                {filteredResults.length > 0 ? (
                    <MovieCarousel
                        title="Filter Results"
                        items={filteredResults}
                        userId={userId}
                        sectionId="filter_results"
                        onInspect={onInspect}
                    />
                ) : (
                    <div className="text-center text-zinc-600 font-mono text-xs py-16 uppercase tracking-widest">
                        NO_RESULTS_FOUND
                    </div>
                )}
            </div>
        );
    }

    if (isLoading) {
        return (
            <div
                className="space-y-12"
                role="status"
                aria-label="Loading recommendations"
                aria-live="polite"
            >
                {[1, 2, 3, 4].map((i) => (
                    <div key={i} className="space-y-4">
                        <div className="flex items-center gap-2 px-1">
                            <div className="h-5 w-40 bg-[oklch(0.14_0_0)]" />
                        </div>
                        <div className="flex gap-4 overflow-hidden px-1">
                            {[1, 2, 3, 4, 5, 6].map((j) => (
                                <div
                                    key={j}
                                    className="relative h-[280px] w-[185px] flex-shrink-0 bg-[oklch(0.08_0_0)] border border-[oklch(0.18_0_0)] overflow-hidden"
                                >
                                    {/* shimmer sweep */}
                                    <div
                                        className="absolute inset-0 bg-gradient-to-r from-transparent via-[oklch(0.9_0.4_110/0.03)] to-transparent animate-shimmer"
                                        style={{ "--shimmer-duration": "1.8s" } as React.CSSProperties}
                                    />
                                    {/* bottom info area */}
                                    <div className="absolute bottom-0 left-0 right-0 p-3 space-y-2">
                                        <div className="h-3 w-3/4 bg-[oklch(0.14_0_0)]" />
                                        <div className="h-2 w-1/2 bg-[oklch(0.12_0_0)]" />
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                ))}
            </div>
        );
    }

    if (error) {
        return (
            <div className="flex justify-center w-full">
                <AcidError
                    message="DATA_STREAM_INTERRUPTED"
                    onRetry={() => window.location.reload()}
                    className="max-w-2xl"
                />
            </div>
        );
    }

    if (!feedData || feedData.feed.length === 0 || feedData.status === "incomplete") {
        const isIncomplete = feedData?.status === "incomplete";

        return (
            <div className="flex flex-col items-center justify-center min-h-[50vh] space-y-8">
                <div className="text-center space-y-2">
                    <h2 className={`text-2xl font-bold font-mono ${isIncomplete ? "text-orange-500" : "text-primary"}`}>
                        {isIncomplete ? "DATA_INCOMPLETE" : "DATA_MISSING"}
                    </h2>
                    <p className="text-muted-foreground max-w-md mx-auto">
                        {isIncomplete
                            ? "Your data import seems to have been interrupted. Please upload your Letterboxd export again to fix your profile."
                            : "VectorBox needs your Letterboxd data to generate recommendations."}
                    </p>
                </div>

                <div className="w-full max-w-xl bg-card border border-border rounded-xl shadow-lg p-6">
                    <UploadZone
                        registeredUsers={registeredUsers || [{ id: userId, username: "" }]}
                        activeSessionUserId={userId}
                        onUploadSuccess={() => window.location.reload()}
                        onUserCreated={() => { }}
                    />
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-12">
            {feedData.feed.map((section, index) => (
                <MovieCarousel
                    key={section.id}
                    title={
                        (section.id === "niche_picks" || section.id === "because_you_watched" || section.type === "wildcard")
                            ? section.title
                            : (t(TITLE_MAP[section.id] || `sections.${section.id}`) || section.title)
                    }
                    items={section.items}
                    userId={userId}
                    sectionId={section.id}
                    type={section.type}
                    forceVectorBoxScore={section.id === "wildcard"}
                    priority={index === 0}
                    onInspect={onInspect}
                    titlePrefix={SECTION_DESCRIPTIONS[section.id] ? (
                        <InfoTooltip
                            id={`feed-section-${section.id}`}
                            title={t(`sections.${section.id}`) || section.title}
                            description={SECTION_DESCRIPTIONS[section.id]} // TODO: Translate descriptions if needed
                        />
                    ) : undefined}
                />
            ))}
        </div>
    );
}
