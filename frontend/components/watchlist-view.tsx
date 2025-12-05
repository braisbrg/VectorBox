"use client";

import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Loader2, Filter, X, SortAsc, Tv } from "lucide-react";
import Image from "next/image";
import { getTMDBImageUrl, type FeedItem } from "@/lib/api";
import { getProvidersForCountry } from "@/lib/constants";
import { MovieCard } from "@/components/ui/movie-card";

interface WatchlistViewProps {
    userId: number;
    username: string;
    countryCode?: string;
    streamingProviders?: number[];
}

import { getUserActivity } from "@/lib/api";

export function WatchlistView({ userId, username, countryCode = "ES", streamingProviders = [] }: WatchlistViewProps) {
    const [showFilters, setShowFilters] = useState(false);
    const [filters, setFilters] = useState({
        runtimeMax: undefined as number | undefined,
        runtimeMin: undefined as number | undefined,
        yearMin: undefined as number | undefined,
        yearMax: undefined as number | undefined,
        genre: undefined as string | undefined,
        sortBy: "date_added" as "date_added" | "title" | "rating",
        streaming: streamingProviders,
        minRating: undefined as number | undefined,
    });

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
        queryKey: ["watchlist", userId, debouncedFilters, countryCode],
        queryFn: async () => {
            const params = new URLSearchParams({
                user_id: userId.toString(),
                country_code: countryCode,
                sort_by: debouncedFilters.sortBy,
            });

            if (debouncedFilters.runtimeMax) params.set("runtime_max", debouncedFilters.runtimeMax.toString());
            if (debouncedFilters.runtimeMin) params.set("runtime_min", debouncedFilters.runtimeMin.toString());
            if (debouncedFilters.yearMin) params.set("year_min", debouncedFilters.yearMin.toString());
            if (debouncedFilters.yearMax) params.set("year_max", debouncedFilters.yearMax.toString());
            if (debouncedFilters.genre) params.set("genres", debouncedFilters.genre);
            if (debouncedFilters.minRating) params.set("min_rating", debouncedFilters.minRating.toString());
            if (debouncedFilters.streaming.length > 0) params.set("streaming_providers", debouncedFilters.streaming.join(","));

            const response = await fetch(
                `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/recommendations/watchlist?${params}`
            );

            if (!response.ok) throw new Error("Failed to fetch watchlist");
            return response.json() as Promise<{ items: FeedItem[]; total: number }>;
        },
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

                <div className="flex gap-2">
                    <button
                        onClick={() => setShowFilters(!showFilters)}
                        className={`flex items-center gap-2 px-4 py-2 rounded-lg border transition-colors ${showFilters ? "bg-primary text-primary-foreground border-primary" : "bg-background hover:bg-muted"
                            }`}
                    >
                        <Filter className="w-4 h-4" />
                        Filters
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
                                onChange={(e) => setFilters((prev) => ({ ...prev, sortBy: e.target.value as any }))}
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
                                    setFilters((prev) => ({ ...prev, runtimeMax: parseInt(e.target.value) }))
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
                                onChange={(e) => setFilters((prev) => ({ ...prev, yearMin: e.target.value ? parseInt(e.target.value) : undefined }))}
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
                                onChange={(e) => setFilters((prev) => ({ ...prev, yearMax: e.target.value ? parseInt(e.target.value) : undefined }))}
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
                            onChange={(e) => setFilters(prev => ({ ...prev, minRating: parseFloat(e.target.value) || undefined }))}
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
                                setFilters((prev) => ({
                                    ...prev,
                                    streaming: prev.streaming.includes(provider.id)
                                        ? prev.streaming.filter((id) => id !== provider.id)
                                        : [...prev.streaming, provider.id],
                                }));
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
                    onClick={() =>
                        setFilters({
                            runtimeMax: undefined,
                            runtimeMin: undefined,
                            yearMin: undefined,
                            yearMax: undefined,
                            genre: undefined,
                            minRating: undefined,
                            sortBy: "date_added",
                            streaming: [],
                        })
                    }
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
                                    imdb_rating={item.imdb_rating}
                                    metacritic_rating={item.metacritic_rating}
                                    rotten_tomatoes_rating={item.rotten_tomatoes_rating}
                                />
                            </motion.div>
                        ))}
                    </div>
                )
            }
        </div >
    );
}
