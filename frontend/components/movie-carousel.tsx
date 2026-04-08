"use client";

import { motion, AnimatePresence } from "framer-motion";
import Image from "next/image";
import { getTMDBImageUrl, getWildcardRecommendation, getRandomRecommendation, getHiddenGemsRecommendation, rejectMovie } from "@/lib/api";
import type { Contributor } from "@/types/feed";
import { RefreshCw, ChevronLeft, ChevronRight } from "lucide-react";
import { useRef, useState, useEffect, useCallback } from "react";
import { MovieCard } from "@/components/ui/movie-card";
import { useLanguage } from "@/components/language-provider";
import { useMutation, useQueryClient } from "@tanstack/react-query";

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
    contributors?: Contributor[];
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
    onInspect?: (id: number, sectionId?: string, contributors?: Contributor[]) => void;
    onReject?: (id: number) => void;
}

export function MovieCarousel({ title, items, userId, sectionId, type, titlePrefix, forceVectorBoxScore, priority = false, onInspect, onReject }: MovieCarouselProps) {
    const scrollContainerRef = useRef<HTMLDivElement>(null);
    const isMounted = useRef(true);
    const [localItems, setLocalItems] = useState<FeedItem[]>(items);
    const [localTitle, setLocalTitle] = useState<string>(title);
    const [isRerolling, setIsRerolling] = useState(false);
    const { t } = useLanguage();

    const queryClient = useQueryClient();

    // Update local state when props change
    useEffect(() => {
        setLocalItems(items);
        setLocalTitle(title);
    }, [items, title]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            isMounted.current = false;
        };
    }, []);

    // FIX 3: Optimistic reject with rollback
    const [rejectingIds, setRejectingIds] = useState<Set<number>>(new Set());

    const handleReject = useCallback(async (tmdbId: number) => {
        // Snapshot for rollback
        const previousItems = localItems;

        // Optimistic: remove immediately
        setLocalItems(prev => prev.filter(item => item.id !== tmdbId));
        setRejectingIds(prev => new Set(prev).add(tmdbId));

        try {
            await rejectMovie(tmdbId);
            if (isMounted.current) {
                // Invalidate feed query so next load reflects the rejection
                queryClient.invalidateQueries({ queryKey: ["feed"] });
            }
            onReject?.(tmdbId);
        } catch (error) {
            console.error("Failed to reject movie:", error);
            // Rollback: restore previous items
            if (isMounted.current) {
                setLocalItems(previousItems);
            }
        } finally {
            if (isMounted.current) {
                setRejectingIds(prev => {
                    const next = new Set(prev);
                    next.delete(tmdbId);
                    return next;
                });
            }
        }
    }, [localItems, onReject, queryClient, isMounted]);

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

            if (newSection && isMounted.current) {
                setLocalItems(newSection.items);
                setLocalTitle(newSection.title);
            }
        } catch (error) {
            console.error("Failed to reroll:", error);
        } finally {
            if (isMounted.current) {
                setIsRerolling(false);
            }
        }
    };

    // FIX 3: Auto-hide empty rows with fade-out
    if (localItems.length === 0) {
        return null;
    }

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
        <AnimatePresence>
        <motion.div
            className="space-y-4 mb-8"
            data-testid="feed-carousel"
            initial={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0, marginBottom: 0, overflow: "hidden" }}
            transition={{ duration: 0.3 }}
        >
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
                            onInspect={(id, contribs) => onInspect?.(id, sectionId, contribs)}
                            onReject={handleReject}
                            isRejecting={rejectingIds.has(movie.id)}
                        />
                    </div>
                ))}

                {/* FIX 3: Low item threshold message */}
                {localItems.length > 0 && localItems.length < 3 && (
                    <div className="flex-none flex items-center px-4">
                        <span className="text-zinc-600 font-mono text-xs whitespace-nowrap">
                            [ SECTION REFRESHES ON NEXT LOAD ]
                        </span>
                    </div>
                )}
            </div>
        </motion.div>
        </AnimatePresence>
    );
}
