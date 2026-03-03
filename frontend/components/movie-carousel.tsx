"use client";

import { motion } from "framer-motion";
import Image from "next/image";
import { getTMDBImageUrl, getWildcardRecommendation, getRandomRecommendation, getHiddenGemsRecommendation } from "@/lib/api";
import { RefreshCw, ChevronLeft, ChevronRight } from "lucide-react";
import { useRef, useState, useEffect } from "react";
import { MovieCard } from "@/components/ui/movie-card";
import { useLanguage } from "@/components/language-provider";

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
    // Phase 12 Fields
    vectorbox_score?: number;
    imdb_rating?: number;
    metacritic_rating?: number;
    rotten_tomatoes_rating?: number;
    letterboxd_rating?: number;
}

interface MovieCarouselProps {
    title: string;
    items: FeedItem[];
    userId?: number;
    sectionId?: string;
    type?: string;
    titlePrefix?: React.ReactNode;
    forceVectorBoxScore?: boolean;
    priority?: boolean;
}

export function MovieCarousel({ title, items, userId, sectionId, type, titlePrefix, forceVectorBoxScore, priority = false }: MovieCarouselProps) {
    const scrollContainerRef = useRef<HTMLDivElement>(null);
    const [localItems, setLocalItems] = useState<FeedItem[]>(items);
    const [localTitle, setLocalTitle] = useState<string>(title);
    const [isRerolling, setIsRerolling] = useState(false);
    const { t } = useLanguage();

    // Update local state when props change
    useEffect(() => {
        setLocalItems(items);
        setLocalTitle(title);
    }, [items, title]);

    if (localItems.length === 0) return null;

    const isWildcard = type === "wildcard" || sectionId?.startsWith("wildcard_");
    const isRandom = type === "random" || sectionId === "random_picks";
    const isHiddenGems = type === "hidden_gems" || sectionId === "hidden_gems";
    const showReroll = isWildcard || isRandom || isHiddenGems;

    const handleReroll = async () => {
        if (isRerolling) return;

        setIsRerolling(true);
        try {
            let newSection;
            if (isWildcard) {
                newSection = await getWildcardRecommendation();
            } else if (isRandom) {
                newSection = await getRandomRecommendation();
            } else if (isHiddenGems) {
                newSection = await getHiddenGemsRecommendation();
            }

            if (newSection) {
                setLocalItems(newSection.items);
                setLocalTitle(newSection.title);
            }
        } catch (error) {
            console.error("Failed to reroll:", error);
        } finally {
            setIsRerolling(false);
        }
    };

    const scroll = (direction: "left" | "right") => {
        if (scrollContainerRef.current) {
            const { current } = scrollContainerRef;
            const scrollAmount = direction === "left" ? -current.offsetWidth / 2 : current.offsetWidth / 2;
            current.scrollBy({ left: scrollAmount, behavior: "smooth" });
        }
    };

    // Determine badge type
    const isWatchlist = type === "watchlist" || sectionId?.includes("watchlist");
    const badgeType = (
        isWatchlist ||
        sectionId === "available_now" ||
        type === "watchlist_top" ||
        type === "watchlist_short" ||
        type === "watchlist_random" ||
        sectionId === "random_picks" ||
        sectionId === "hidden_gems"
    ) ? "rating" : (sectionId === "popular_letterboxd" ? "letterboxd" : "match");

    return (
        <div className="space-y-4 mb-8">
            <div className="flex items-center justify-between px-4 md:px-8">
                <div className="flex items-center gap-3">
                    {titlePrefix}
                    <h3 className="text-3xl font-bold font-space uppercase tracking-wider text-acid-outline" data-text={localTitle}>{localTitle}</h3>
                    {showReroll && (
                        <button
                            onClick={handleReroll}
                            disabled={isRerolling}
                            className={`p-2 rounded-full bg-primary/10 hover:bg-primary/20 text-primary transition-all ${isRerolling ? "opacity-50 cursor-not-allowed" : ""}`}
                            title={isWildcard ? "Reroll wildcard" : "Get new random picks"}
                            aria-label={isWildcard ? t("aria.reroll_wildcard") : t("aria.reroll_random")}
                        >
                            <RefreshCw className={`w-4 h-4 ${isRerolling ? "animate-spin" : ""}`} />
                        </button>
                    )}
                </div>
                <div className="hidden md:flex gap-2">
                    <button
                        onClick={() => scroll("left")}
                        className="p-2 rounded-none border border-zinc-800 hover:bg-zinc-900 hover:border-primary hover:text-primary transition-all"
                        aria-label={t("aria.scroll_left")}
                    >
                        <ChevronLeft className="w-5 h-5" />
                    </button>
                    <button
                        onClick={() => scroll("right")}
                        className="p-2 rounded-none border border-zinc-800 hover:bg-zinc-900 hover:border-primary hover:text-primary transition-all"
                        aria-label={t("aria.scroll_right")}
                    >
                        <ChevronRight className="w-5 h-5" />
                    </button>
                </div>
            </div>

            <div
                ref={scrollContainerRef}
                className="flex gap-4 overflow-x-auto px-4 md:px-8 pb-4 scrollbar-hide snap-x snap-mandatory"
            >
                {localItems.map((movie, index) => (
                    <div key={movie.id} className="flex-none w-[160px] md:w-[200px] snap-start">
                        <MovieCard
                            id={movie.id}
                            title={movie.title}
                            posterPath={movie.poster_url}
                            matchScore={movie.match_score}
                            rating={movie.rating}
                            year={movie.year}
                            runtime={movie.runtime}
                            overview={movie.overview}
                            variant="overlay"
                            badgeType={badgeType}
                            contributors={movie.contributors}
                            href={movie.letterboxd_uri}
                            vectorbox_score={movie.vectorbox_score}
                            imdb_rating={movie.imdb_rating}
                            metacritic_rating={movie.metacritic_rating}
                            rotten_tomatoes_rating={movie.rotten_tomatoes_rating}
                            letterboxd_rating={movie.letterboxd_rating}
                            providers={movie.streaming_providers}
                            forceVectorBoxScore={forceVectorBoxScore}
                            priority={priority && index < 4}
                        />
                    </div>
                ))}
            </div>
        </div>
    );
}
