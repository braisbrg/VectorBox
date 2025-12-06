"use client";

import { useQuery } from "@tanstack/react-query";
import { MovieCarousel } from "./movie-carousel";
import { Skeleton } from "@/components/ui/skeleton";
import { InfoTooltip } from "./info-tooltip";
import { useLanguage } from "@/components/language-provider";
import { useSettings } from "@/lib/hooks";
import { getFeed } from "@/lib/api";

interface FeedItem {
    id: number;
    title: string;
    poster_url?: string;
    match_score: number;
    streaming_providers: string[];
    year?: number;
    runtime?: number;
    letterboxd_uri?: string;
    rating?: number;
    overview?: string;
    contributors?: { seed_title: string; contribution: number }[];
    letterboxd_rating?: number;
    vectorbox_score?: number;
}

interface FeedSection {
    id: string;
    title: string;
    type: string;
    items: FeedItem[];
}

interface FeedResponse {
    feed: FeedSection[];
}

interface FeedContainerProps {
    userId: number;
    scope: "global" | "watchlist";
    countryCode?: string;
    streamingProviders?: number[];
}

const SECTION_DESCRIPTIONS: Record<string, string> = {
    because_you_watched: "Recommendations based on specific movies you've rated highly (4+ stars).",
    your_taste: "Movies that align with your overall taste profile and preferred genres.",
    wildcard: "Unexpected recommendations to help you discover something new.",
    random_picks: "A diverse selection of highly-rated movies from our database.",
    hidden_gems: "Critically acclaimed movies that you might have missed (fewer than 5k votes).",
    available_now: "Movies from your watchlist that are currently available on your streaming services.",
};

export function FeedContainer({ userId, scope, countryCode = "ES", streamingProviders = [] }: FeedContainerProps) {
    const { settings } = useSettings();
    const includeLowQuality = settings.includeLowQuality;

    const { data: feedData, isLoading, error } = useQuery<FeedResponse>({
        queryKey: ["feed", userId, scope, countryCode, streamingProviders, includeLowQuality],
        queryFn: async () => getFeed(userId, scope, countryCode, streamingProviders, includeLowQuality),
        staleTime: 5 * 60 * 1000, // 5 minutes
    });



    const { t } = useLanguage();

    if (isLoading) {
        return (
            <div className="space-y-12">
                {[1, 2, 3, 4].map((i) => (
                    <div key={i} className="space-y-4">
                        <div className="flex items-center gap-2 px-1">
                            <Skeleton className="h-6 w-48 bg-muted/60" />
                        </div>
                        <div className="flex gap-4 overflow-hidden px-1">
                            {[1, 2, 3, 4, 5, 6].map((j) => (
                                <Skeleton key={j} className="h-[280px] w-[200px] rounded-lg flex-shrink-0 bg-muted/40" />
                            ))}
                        </div>
                    </div>
                ))}
            </div>
        );
    }

    if (error) {
        return (
            <div className="p-6 bg-destructive/10 border border-destructive/20 rounded-lg flex flex-col items-center gap-4">
                <p className="text-destructive">
                    Failed to load recommendations. Please try again.
                </p>
                <button
                    onClick={() => window.location.reload()}
                    className="px-4 py-2 bg-background border rounded-md text-sm hover:bg-muted transition-colors"
                >
                    Retry
                </button>
            </div>
        );
    }

    if (!feedData || feedData.feed.length === 0) {
        return (
            <div className="p-6 bg-muted/50 rounded-lg text-center">
                <p className="text-muted-foreground">
                    No recommendations available yet. Upload your Letterboxd data to get started!
                </p>
            </div>
        );
    }

    return (
        <div className="space-y-12">
            {feedData.feed.map((section) => (
                <MovieCarousel
                    key={section.id}
                    title={
                        (section.id === "your_taste" || section.id === "because_you_watched" || section.type === "wildcard")
                            ? section.title
                            : (t(`sections.${section.id}`) || section.title)
                    }
                    items={section.items}
                    userId={userId}
                    sectionId={section.id}
                    type={section.type}
                    forceVectorBoxScore={section.id === "wildcard"}
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
