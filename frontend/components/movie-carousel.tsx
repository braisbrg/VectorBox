"use client";

import { m, AnimatePresence } from "framer-motion";
import Image from "next/image";
import { getTMDBImageUrl, getLetterboxdUrl, getWildcardRecommendation, getRandomRecommendation, getHiddenGemsRecommendation, rejectMovie, markWatched, rerollCluster } from "@/lib/api";
import type { Contributor } from "@/types/feed";
import { RefreshCw, ChevronLeft, ChevronRight, ChevronDown } from "lucide-react";
import { useRef, useState, useEffect, useCallback } from "react";
import { MovieCard } from "@/components/ui/movie-card";
import { cn } from "@/lib/utils";
import { useLanguage } from "@/components/language-provider";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { scheduleFeedInvalidation } from "@/lib/feed-invalidation";

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
    onInspect?: (movie: import("@/lib/api").FeedItem, sectionId?: string) => void;
    onReject?: (id: number) => void;
    /** Film shown in the desktop hero — hidden from this row ≥lg only (mobile has no hero). */
    heroId?: number;
}

export function MovieCarousel({ title, items, userId, sectionId, type, titlePrefix, forceVectorBoxScore, priority = false, onInspect, onReject, heroId }: MovieCarouselProps) {
    const scrollContainerRef = useRef<HTMLDivElement>(null);
    const isMounted = useRef(true);
    const [localItems, setLocalItems] = useState<FeedItem[]>(items);
    const [localTitle, setLocalTitle] = useState<string>(title);
    const [isRerolling, setIsRerolling] = useState(false);
    const [collapsed, setCollapsed] = useState(false);
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
    const [watchedIds, setWatchedIds] = useState<Set<number>>(new Set());

    const handleReject = useCallback(async (tmdbId: number) => {
        // Snapshot for rollback
        const previousItems = localItems;

        // Optimistic: remove immediately
        setLocalItems(prev => prev.filter(item => item.id !== tmdbId));
        setRejectingIds(prev => new Set(prev).add(tmdbId));

        try {
            await rejectMovie(tmdbId);
            if (isMounted.current) {
                // Shared 3s debounce — rapid-fire clicks across multiple cards
                // coalesce into ONE /feed refetch. Direct invalidateQueries
                // here bypassed the dashboard's debounce and was the actual
                // cause of the 429 spiral + rejected-film flicker in round 6.
                scheduleFeedInvalidation(queryClient);
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

    const handleMarkWatched = useCallback(async (tmdbId: number) => {
        const previousItems = localItems;

        setLocalItems(prev => prev.filter(item => item.id !== tmdbId));
        setWatchedIds(prev => new Set(prev).add(tmdbId));

        try {
            await markWatched(tmdbId);
            if (isMounted.current) {
                scheduleFeedInvalidation(queryClient);
            }
            onReject?.(tmdbId);
        } catch (error) {
            console.error("Failed to mark watched:", error);
            if (isMounted.current) {
                setLocalItems(previousItems);
            }
        } finally {
            if (isMounted.current) {
                setWatchedIds(prev => {
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
        <m.div
            className="mb-8 space-y-3"
            data-testid="feed-carousel"
            initial={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0, marginBottom: 0, overflow: "hidden" }}
            transition={{ duration: 0.3 }}
        >
            <div className="flex items-center justify-between px-1 md:px-2">
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => setCollapsed((c) => !c)}
                        className="flex size-6 items-center justify-center text-fg-3 transition-colors hover:text-primary"
                        aria-label={collapsed ? "Expand section" : "Collapse section"}
                        aria-expanded={!collapsed}
                    >
                        <ChevronDown className={cn("size-4 transition-transform", collapsed && "-rotate-90")} />
                    </button>
                    {titlePrefix}
                    <h3 className="font-display text-lg uppercase tracking-tight text-fg md:text-xl">{localTitle}</h3>
                    {showReroll && (
                        <button
                            onClick={handleReroll}
                            disabled={isRerolling}
                            className={cn(
                                "flex size-7 items-center justify-center border border-border-2 text-fg-3 transition-colors hover:border-primary hover:text-primary",
                                isRerolling && "cursor-not-allowed opacity-50"
                            )}
                            title={isWildcard ? "Reroll wildcard" : "Get new random picks"}
                            aria-label={isWildcard ? t("aria.reroll_wildcard") : t("aria.reroll_random")}
                        >
                            <RefreshCw className={cn("size-3.5", isRerolling && "animate-spin")} />
                        </button>
                    )}
                    {sectionId === "niche_picks" && (
                        <button
                            onClick={async () => {
                                if (isRerolling) return;
                                setIsRerolling(true);
                                try {
                                    await rerollCluster();
                                    await queryClient.invalidateQueries({ queryKey: ["feed"] });
                                } catch (error) {
                                    console.error("Failed to reroll cluster:", error);
                                } finally {
                                    if (isMounted.current) setIsRerolling(false);
                                }
                            }}
                            disabled={isRerolling}
                            className={cn(
                                "border border-border-2 px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider text-fg-3 transition-colors hover:border-primary hover:text-primary",
                                isRerolling && "cursor-not-allowed opacity-50"
                            )}
                            title="Show next cluster"
                        >
                            [ REROLL ]
                        </button>
                    )}
                </div>
                {!collapsed && (
                    <div className="hidden gap-2 md:flex">
                        <button
                            onClick={() => scroll("left")}
                            className="flex size-8 items-center justify-center border border-border-2 text-fg-3 transition-colors hover:border-primary hover:text-primary"
                            aria-label={t("aria.scroll_left")}
                        >
                            <ChevronLeft className="size-4" />
                        </button>
                        <button
                            onClick={() => scroll("right")}
                            className="flex size-8 items-center justify-center border border-border-2 text-fg-3 transition-colors hover:border-primary hover:text-primary"
                            aria-label={t("aria.scroll_right")}
                        >
                            <ChevronRight className="size-4" />
                        </button>
                    </div>
                )}
            </div>

            {!collapsed && (
                <div
                    ref={scrollContainerRef}
                    className="flex snap-x snap-mandatory gap-2.5 overflow-x-auto px-1 pb-4 scrollbar-hide md:gap-3 md:px-2"
                >
                    {localItems.map((movie) => (
                        // 118px = handoff .pcard width (~3 cards visible at 390px)
                        <div key={movie.id} className={cn("w-[118px] flex-none snap-start md:w-[180px]", movie.id === heroId && "lg:hidden")}>
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
                                href={getLetterboxdUrl(movie.id)}
                                vectorbox_score={movie.vectorbox_score}
                                imdb_rating={movie.imdb_rating}
                                metacritic_rating={movie.metacritic_rating}
                                letterboxd_rating={movie.letterboxd_rating}
                                hudRight={sectionId === "popular_letterboxd" && movie.letterboxd_rating != null ? `★ ${movie.letterboxd_rating.toFixed(1)}` : undefined}
                                providers={movie.streaming_providers}
                                onInspect={() => onInspect?.(movie, sectionId)}
                                onReject={handleReject}
                                onMarkWatched={handleMarkWatched}
                                isRejecting={rejectingIds.has(movie.id)}
                                isMarkingWatched={watchedIds.has(movie.id)}
                            />
                        </div>
                    ))}

                    {localItems.length > 0 && localItems.length < 3 && (
                        <div className="flex flex-none items-center px-4">
                            <span className="whitespace-nowrap font-mono text-xs text-fg-3">
                                [ SECTION REFRESHES ON NEXT LOAD ]
                            </span>
                        </div>
                    )}
                </div>
            )}
        </m.div>
        </AnimatePresence>
    );
}
