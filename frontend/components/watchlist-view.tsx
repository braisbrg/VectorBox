"use client";

import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import type { Contributor } from "@/types/feed";
import { motion } from "framer-motion";
import { Loader2, Filter, X, SortAsc, Tv } from "lucide-react";
import { getTMDBImageUrl, type FeedItem, getWatchlist, getUserActivity } from "@/lib/api";
import { getProvidersForCountry } from "@/lib/constants";
import { MovieCard } from "@/components/ui/movie-card";

interface WatchlistViewProps {
    userId: number;
    username: string;
    countryCode?: string;
    streamingProviders?: number[];
    onInspect?: (id: number, sectionId?: string, contributors?: Contributor[]) => void;
}

const WATCHLIST_FILTERS_KEY = "watchlist_filters";

interface WatchlistFilters {
    runtimeMax?: number;
    runtimeMin?: number;
    yearMin?: number;
    yearMax?: number;
    genre?: string;
    sortBy: "date_added" | "title" | "rating";
    streaming: number[];
    minRating?: number;
}

const getPersistedFilters = (): Partial<WatchlistFilters> | null => {
    try {
        const saved = localStorage.getItem(WATCHLIST_FILTERS_KEY);
        if (saved) return JSON.parse(saved);
    } catch { }
    return null;
};

export function WatchlistView({ userId, username, countryCode = "ES", streamingProviders = [], onInspect }: WatchlistViewProps) {
    const [showFilters, setShowFilters] = useState(false);
    const [page, setPage] = useState(1);
    const LIMIT = 20;
    const [filters, setFilters] = useState<WatchlistFilters>(() => {
        const persisted = getPersistedFilters();
        const base: WatchlistFilters = {
            runtimeMax: undefined,
            runtimeMin: undefined,
            yearMin: undefined,
            yearMax: undefined,
            genre: undefined,
            sortBy: "date_added",
            streaming: streamingProviders,
            minRating: undefined,
            ...persisted,
        };
        // streaming always from prop, never from localStorage
        base.streaming = streamingProviders;
        return base;
    });

    const updateFilters = (updates: Partial<typeof filters>) => {
        setFilters(prev => {
            const next = { ...prev, ...updates };
            // Persist only preference filters (not streaming which comes from props)
            const { streaming, ...toPersist } = next;
            localStorage.setItem(WATCHLIST_FILTERS_KEY, JSON.stringify(toPersist));
            return next;
        });
        setPage(1);
    };

    const { data: activity } = useQuery({
        queryKey: ["user-activity", username],
        queryFn: () => getUserActivity(username),
        enabled: !!username,
    });


    const [debouncedFilters, setDebouncedFilters] = useState(filters);

    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedFilters(filters);
        }, 500);

        return () => clearTimeout(timer);
    }, [filters]);

    const { data, isLoading, error } = useQuery({
        queryKey: ["watchlist", userId, debouncedFilters, countryCode, page],
        queryFn: () => getWatchlist(
            page,
            LIMIT,
            countryCode,
            {
                sort_by: debouncedFilters.sortBy,
                runtime_min: debouncedFilters.runtimeMin,
                runtime_max: debouncedFilters.runtimeMax,
                year_min: debouncedFilters.yearMin,
                year_max: debouncedFilters.yearMax,
                genres: debouncedFilters.genre,
                min_rating: debouncedFilters.minRating,
                streaming_providers: debouncedFilters.streaming.length > 0
                    ? debouncedFilters.streaming.join(",")
                    : undefined,
            }
        ),
    });

    if (isLoading) {
        return (
            <div className="flex items-center justify-center py-20">
                <Loader2 className="w-12 h-12 animate-spin text-primary" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="p-6 bg-destructive/10 border border-destructive/20 rounded-lg">
                <p className="text-destructive">Failed to load watchlist. Please try again.</p>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <h2 className="text-3xl font-bold">My Watchlist</h2>
                    <div className="flex items-center gap-4 text-sm text-muted-foreground mt-1">
                        <span>{data?.total || 0} movies</span>
                        {activity?.last_watched && (
                            <span className="flex items-center gap-1">
                                • Last watched: <span className="text-primary">{activity.last_watched.title}</span>
                            </span>
                        )}
                        {activity?.last_rated && activity.last_rated.title !== activity?.last_watched?.title && (
                            <span className="flex items-center gap-1">
                                • Last rated: <span className="text-primary">{activity.last_rated.title}</span>
                            </span>
                        )}
                    </div>
                </div>

                <div className="flex flex-wrap gap-2 items-center">
                    {(() => {
                        const activeFilters = [];
                        if (filters.runtimeMax) activeFilters.push(`Max ${filters.runtimeMax}m`);
                        if (filters.yearMin || filters.yearMax) {
                            if (filters.yearMin && filters.yearMax) activeFilters.push(`${filters.yearMin}-${filters.yearMax}`);
                            else if (filters.yearMin) activeFilters.push(`Since ${filters.yearMin}`);
                            else activeFilters.push(`Until ${filters.yearMax}`);
                        }
                        if (filters.minRating) activeFilters.push(`Rating ${filters.minRating}+`);
                        if (filters.genre) activeFilters.push(filters.genre);
                        
                        // Compare current streaming with initial streamingProviders to see if it's "filtered"
                        // Note: If streaming is an empty array but initial was 10 providers, it means "none" (filtered)
                        if (filters.streaming.length !== streamingProviders.length) {
                             activeFilters.push(`${filters.streaming.length} Providers`);
                        }

                        if (activeFilters.length > 0) {
                            return (
                                <div className="flex flex-wrap gap-1.5 mr-3">
                                    {activeFilters.map((tag, idx) => (
                                        <span key={idx} className="px-2 py-0.5 bg-amber-500/10 border border-amber-500/20 text-[10px] font-mono text-amber-500 uppercase tracking-wider rounded-sm">
                                            {tag}
                                        </span>
                                    ))}
                                    <button
                                        onClick={() => {
                                            localStorage.removeItem(WATCHLIST_FILTERS_KEY);
                                            setFilters({
                                                runtimeMax: undefined,
                                                runtimeMin: undefined,
                                                yearMin: undefined,
                                                yearMax: undefined,
                                                genre: undefined,
                                                minRating: undefined,
                                                sortBy: "date_added",
                                                streaming: streamingProviders,
                                            });
                                            setPage(1);
                                        }}
                                        className="text-[10px] font-mono text-zinc-500 hover:text-primary uppercase tracking-wider underline underline-offset-2 ml-1"
                                    >
                                        Clear
                                    </button>
                                </div>
                            );
                        }
                        return null;
                    })()}
                    <button
                        onClick={() => setShowFilters(!showFilters)}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg border transition-colors ${showFilters ? "bg-primary text-primary-foreground border-primary" : "bg-background hover:bg-muted"
                            }`}
                    >
                        <Filter className="w-4 h-4" />
                        Filters
                        {Object.values({
                            runtime: filters.runtimeMax,
                            yearMin: filters.yearMin,
                            yearMax: filters.yearMax,
                            minRating: filters.minRating,
                            streaming: filters.streaming.length > 0 ? true : undefined
                        }).filter(Boolean).length > 0 && (
                            <span className="ml-1 px-1.5 py-0.5 bg-primary-foreground text-primary text-[10px] font-bold rounded-full">
                                {Object.values({
                                    runtime: filters.runtimeMax,
                                    yearMin: filters.yearMin,
                                    yearMax: filters.yearMax,
                                    minRating: filters.minRating,
                                    streaming: filters.streaming.length > 0 ? true : undefined
                                }).filter(Boolean).length}
                            </span>
                        )}
                    </button>
                </div>
            </div>

            {/* Filters */}
            {showFilters && (
                <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    className="p-4 rounded-lg border bg-muted/30 space-y-4"
                >
                    <div className="grid sm:grid-cols-2 md:grid-cols-4 gap-4">
                        {/* Sort */}
                        <div className="space-y-2">
                            <label className="text-sm font-medium flex items-center gap-2">
                                <SortAsc className="w-4 h-4" />
                                Sort By
                            </label>
                            <select
                                className="w-full px-3 py-2 rounded-md border bg-background"
                                value={filters.sortBy}
                                onChange={(e) => updateFilters({ sortBy: e.target.value as any })}
                            >
                                <option value="date_added">Date Added</option>
                                <option value="title">Title</option>
                                <option value="rating">Rating</option>
                            </select>
                        </div>

                        {/* Runtime */}
                        <div className="space-y-2">
                            <label className="text-sm font-medium flex justify-between">
                                <span>Max Runtime</span>
                                <span className="text-muted-foreground">{filters.runtimeMax ? `${filters.runtimeMax}m` : "Any"}</span>
                            </label>
                            <input
                                type="range"
                                min="60"
                                max="240"
                                step="10"
                                className="w-full"
                                value={filters.runtimeMax || 240}
                                onChange={(e) =>
                                    updateFilters({ runtimeMax: parseInt(e.target.value) })
                                }
                            />
                        </div>

                        {/* Year Min */}
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Year From</label>
                            <input
                                type="number"
                                placeholder="1980"
                                className="w-full px-3 py-2 rounded-md border bg-background"
                                value={filters.yearMin || ""}
                                onChange={(e) => updateFilters({ yearMin: e.target.value ? parseInt(e.target.value) : undefined })}
                            />
                        </div>

                        {/* Year Max */}
                        <div className="space-y-2">
                            <label className="text-sm font-medium">Year To</label>
                            <input
                                type="number"
                                placeholder="2024"
                                className="w-full px-3 py-2 rounded-md border bg-background"
                                value={filters.yearMax || ""}
                                onChange={(e) => updateFilters({ yearMax: e.target.value ? parseInt(e.target.value) : undefined })}
                            />
                        </div>
                    </div>

                    {/* Min Rating */}
                    <div className="space-y-2">
                        <label className="text-sm font-medium flex justify-between">
                            <span>Min VectorBox Score</span>
                            <span className="text-muted-foreground">{filters.minRating ? `${filters.minRating}+` : "Any"}</span>
                        </label>
                        <input
                            type="range"
                            min="0"
                            max="100"
                            step="5"
                            className="w-full accent-primary"
                            value={filters.minRating || 0}
                            onChange={(e) => updateFilters({ minRating: parseFloat(e.target.value) || undefined })}
                        />
                    </div>
                </motion.div>
            )}

            {/* Streaming Filters */}
            <div className="space-y-2">
                <label className="text-sm font-medium flex items-center gap-2">
                    <Tv className="w-4 h-4" />
                    Available On
                </label>
                <div className="flex flex-wrap gap-2">
                    {getProvidersForCountry(countryCode).map((provider) => (
                        <button
                            key={provider.id}
                            onClick={() => {
                                const newStreaming = filters.streaming.includes(provider.id)
                                    ? filters.streaming.filter((id) => id !== provider.id)
                                    : [...filters.streaming, provider.id];
                                updateFilters({ streaming: newStreaming });
                            }}
                            className={`px-3 py-1.5 rounded-full text-xs font-medium transition-all ${filters.streaming.includes(provider.id) ? "bg-primary text-primary-foreground" : "bg-muted hover:bg-muted/80"
                                }`}
                        >
                            {provider.name}
                        </button>
                    ))}
                </div>
            </div>

            {/* Clear */}
            {(filters.runtimeMax || filters.yearMin || filters.yearMax || filters.streaming.length > 0) && (
                <button
                    onClick={() => {
                        localStorage.removeItem(WATCHLIST_FILTERS_KEY);
                        setFilters({
                            runtimeMax: undefined,
                            runtimeMin: undefined,
                            yearMin: undefined,
                            yearMax: undefined,
                            genre: undefined,
                            minRating: undefined,
                            sortBy: "date_added",
                            streaming: [],
                        });
                        setPage(1);
                    }}
                    className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
                >
                    <X className="w-3 h-3" />
                </button>
            )}

            {/* Grid */}
            {
                !data || data.items.length === 0 ? (
                    <div className="p-6 bg-muted/50 rounded-lg text-center">
                        <p className="text-muted-foreground">No watchlist items found. Try adjusting your filters.</p>
                    </div>
                ) : (
                    <div className="space-y-8">
                        <div className="grid sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                            {data.items.map((item, index) => (
                                <motion.div
                                    key={item.id}
                                    initial={{ opacity: 0, y: 20 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: index * 0.05 }}
                                >
                                    <MovieCard
                                        id={item.id}
                                        title={item.title}
                                        posterPath={item.poster_url}
                                        year={item.year}
                                        runtime={item.runtime}
                                        rating={item.rating}
                                        matchScore={item.match_score}
                                        providers={item.streaming_providers}
                                        href={item.letterboxd_uri}
                                        variant="grid"
                                        badgeType="rating"
                                        overview={item.overview}
                                        vectorbox_score={item.vectorbox_score}
                                        metacritic_rating={item.metacritic_rating}
                                        rotten_tomatoes_rating={item.rotten_tomatoes_rating}
                                        onInspect={(id, contribs) => onInspect?.(id, undefined, contribs)}
                                    />
                                </motion.div>
                            ))}
                        </div>

                        {/* Pagination Controls */}
                        {data.total > LIMIT && (
                            <div className="flex justify-center items-center gap-4 pt-4 border-t">
                                <button
                                    onClick={() => setPage(p => Math.max(1, p - 1))}
                                    disabled={page === 1}
                                    className="px-4 py-2 rounded-lg border hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    Previous
                                </button>
                                <span className="text-sm text-muted-foreground">
                                    Page {page} of {Math.ceil(data.total / LIMIT)}
                                </span>
                                <button
                                    onClick={() => setPage(p => p + 1)}
                                    disabled={page >= Math.ceil(data.total / LIMIT)}
                                    className="px-4 py-2 rounded-lg border hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    Next
                                </button>
                            </div>
                        )}
                    </div>
                )
            }
        </div >
    );
}
