"use client";

import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSettings } from "@/lib/hooks";

import { getRecommendationsByMood, getGeneralRecommendations, getGroupRecommendations, getTMDBImageUrl, RecommendationResponse } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { Star, Filter, X, Clock, Globe, Tv } from "lucide-react";
import Image from "next/image";
import { STREAMING_PROVIDERS, COUNTRIES } from "@/lib/constants";
import { MovieCard } from "@/components/ui/movie-card";
import { useLanguage } from "@/components/language-provider";

interface RecommendationGridProps {
    userId: number;
    userIds?: number[];
    clusterId?: number | null;
    mode?: "cluster" | "general" | "group";
    countryCode?: string;
    streamingProviders?: number[];
    onStreamingProvidersChange?: (providers: number[]) => void;
    scope?: "global" | "watchlist";
    onScopeChange?: (scope: "global" | "watchlist") => void;
}

export function RecommendationGrid({
    userId,
    userIds,
    clusterId,
    mode = "cluster",
    countryCode,
    streamingProviders,
    onStreamingProvidersChange,
    scope = "global",
    onScopeChange
}: RecommendationGridProps) {
    const { settings } = useSettings();
    const { t } = useLanguage();
    const [showFilters, setShowFilters] = useState(false);
    const [filters, setFilters] = useState({
        yearMin: undefined as number | undefined,
        yearMax: undefined as number | undefined,
        genre: undefined as string | undefined,
        runtimeMax: undefined as number | undefined,
        minVoteCount: undefined as number | undefined,
        minRating: undefined as number | undefined,
        originalLanguage: undefined as string | undefined,
        keywords: undefined as string | undefined,
    });

    const { data: recommendations, isLoading, error } = useQuery({
        queryKey: ["recommendations", userId, userIds, clusterId, mode, filters, countryCode, streamingProviders, scope, settings.includeLowQuality],
        queryFn: () => {
            const params = {
                user_id: userId,
                cluster_id: clusterId || undefined,
                year_min: filters.yearMin,
                year_max: filters.yearMax,
                genres: filters.genre ? [filters.genre] : undefined,
                runtime_max: filters.runtimeMax,
                min_vote_count: filters.minVoteCount,
                min_rating: filters.minRating,
                original_language: filters.originalLanguage,
                include_keywords: filters.keywords ? filters.keywords.split(",").map(k => k.trim()) : undefined,
                country_code: countryCode || "ES",
                streaming_providers: streamingProviders && streamingProviders.length > 0 ? streamingProviders : undefined,
                limit: 20,
                watchlist_only: scope === "watchlist",
                include_low_quality: settings.includeLowQuality,
            };

            if (mode === "group" && userIds && userIds.length > 0) {
                return getGroupRecommendations({ ...params, user_ids: userIds });
            } else if (mode === "general") {
                return getGeneralRecommendations(params);
            } else {
                return getRecommendationsByMood(params);
            }
        },
        retry: 1,
        enabled: (mode === "general" || mode === "group" || !!clusterId),
    });

    const handleYearChange = (type: "min" | "max", value: string) => {
        const num = value ? parseInt(value) : undefined;
        setFilters(prev => ({
            ...prev,
            [type === "min" ? "yearMin" : "yearMax"]: num
        }));
    };

    return (
        <div className="space-y-6">
            {/* Filter Bar */}
            <div className="flex items-center gap-4 flex-wrap">
                {/* Scope Toggle */}
                {onScopeChange && (
                    <div className="bg-zinc-900 border border-zinc-800 p-1 rounded-none flex items-center gap-2">
                        <div className="flex gap-1">
                            <button
                                onClick={() => onScopeChange("global")}
                                className={`px-3 py-1.5 text-xs font-bold uppercase tracking-wider rounded-none transition-all font-mono ${scope === "global" ? "bg-primary text-black shadow-[0_0_10px_rgba(204,255,0,0.3)]" : "text-zinc-500 hover:text-primary hover:bg-zinc-800"}`}
                            >
                                {t("grid.scope.global")}
                            </button>
                            <button
                                onClick={() => onScopeChange("watchlist")}
                                className={`px-3 py-1.5 text-xs font-bold uppercase tracking-wider rounded-none transition-all font-mono ${scope === "watchlist" ? "bg-primary text-black shadow-[0_0_10px_rgba(204,255,0,0.3)]" : "text-zinc-500 hover:text-primary hover:bg-zinc-800"}`}
                            >
                                {t("grid.scope.watchlist")}
                            </button>
                        </div>
                    </div>
                )}

                <button
                    onClick={() => setShowFilters(!showFilters)}
                    className={`flex items-center gap-2 px-4 py-2 rounded-none border transition-all uppercase tracking-wider text-xs font-bold font-mono ${showFilters ? "bg-primary text-black border-primary" : "bg-black border-zinc-800 text-zinc-400 hover:border-primary hover:text-primary"}`}
                >
                    <Filter className="w-4 h-4" />
                    {t("grid.filters")}
                    {(Object.values(filters).some(v => v !== undefined) || (streamingProviders && streamingProviders.length > 0)) && (
                        <span className="ml-1 w-2 h-2 rounded-full bg-current" />
                    )}
                </button>
            </div>

            {/* Filters Panel */}
            <AnimatePresence>
                {showFilters && (
                    <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="overflow-hidden"
                    >
                        <div className="p-6 bg-zinc-900/50 border border-zinc-800 rounded-none space-y-6 backdrop-blur-sm">
                            <div className="flex justify-between items-center">
                                <h3 className="font-bold uppercase tracking-wider text-acid-outline text-sm">{t("grid.active_filters")}</h3>
                                <button
                                    onClick={() => {
                                        setFilters({
                                            yearMin: undefined,
                                            yearMax: undefined,
                                            genre: undefined,
                                            runtimeMax: undefined,
                                            minVoteCount: undefined,
                                            minRating: undefined,
                                            originalLanguage: undefined,
                                            keywords: undefined,
                                        });
                                        if (onStreamingProvidersChange) onStreamingProvidersChange([]);
                                    }}
                                    className="text-xs text-zinc-500 hover:text-destructive flex items-center gap-1 uppercase font-mono"
                                >
                                    <X className="w-3 h-3" />
                                    {t("grid.clear_all")}
                                </button>
                            </div>

                            {/* Country and Basic Filters */}
                            <div className="grid sm:grid-cols-2 md:grid-cols-4 gap-4">
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">{t("grid.streaming_services")}</label>
                                    <div className="flex flex-wrap gap-2">
                                        {STREAMING_PROVIDERS.map((provider) => (
                                            <button
                                                key={provider.id}
                                                onClick={() => {
                                                    const current = streamingProviders || [];
                                                    const updated = current.includes(provider.id)
                                                        ? current.filter(p => p !== provider.id)
                                                        : [...current, provider.id];
                                                    if (onStreamingProvidersChange) {
                                                        onStreamingProvidersChange(updated);
                                                    }
                                                }}
                                                className={`px-2 py-1 text-xs border rounded-md transition-colors ${(streamingProviders || []).includes(provider.id)
                                                    ? "bg-primary text-black border-primary"
                                                    : "bg-background text-muted-foreground border-input hover:border-primary"
                                                    }`}
                                            >
                                                {provider.name}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">{t("grid.year_from")}</label>
                                    <input
                                        type="number"
                                        placeholder="1980"
                                        className="w-full px-3 py-2 rounded-md border bg-background"
                                        value={filters.yearMin || ""}
                                        onChange={(e) => handleYearChange("min", e.target.value)}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">{t("grid.year_to")}</label>
                                    <input
                                        type="number"
                                        placeholder="2024"
                                        className="w-full px-3 py-2 rounded-md border bg-background"
                                        value={filters.yearMax || ""}
                                        onChange={(e) => handleYearChange("max", e.target.value)}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">{t("grid.genre")}</label>
                                    <div className="relative">
                                        <select
                                            className="w-full appearance-none px-3 py-2 rounded-md border bg-background pr-8 focus:outline-none focus:ring-2 focus:ring-primary/50 cursor-pointer hover:bg-muted/50 transition-colors"
                                            value={filters.genre || ""}
                                            onChange={(e) => setFilters(prev => ({ ...prev, genre: e.target.value || undefined }))}
                                        >
                                            <option value="">{t("grid.all_genres")}</option>
                                            <option value="Action">Action</option>
                                            <option value="Comedy">Comedy</option>
                                            <option value="Drama">Drama</option>
                                            <option value="Horror">Horror</option>
                                            <option value="Sci-Fi">Sci-Fi</option>
                                            <option value="Thriller">Thriller</option>
                                            <option value="Romance">Romance</option>
                                            <option value="Documentary">Documentary</option>
                                        </select>
                                        <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
                                            <svg className="w-4 h-4 text-muted-foreground" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                                            </svg>
                                        </div>
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-sm font-medium flex justify-between">
                                        <span>{t("grid.max_runtime")}</span>
                                        <span className="text-muted-foreground">{filters.runtimeMax ? `${filters.runtimeMax}m` : "Any"}</span>
                                    </label>
                                    <input
                                        type="range"
                                        min="60"
                                        max="240"
                                        step="10"
                                        className="w-full accent-primary"
                                        value={filters.runtimeMax || 240}
                                        onChange={(e) => setFilters(prev => ({ ...prev, runtimeMax: parseInt(e.target.value) }))}
                                    />
                                </div>

                                {/* Advanced Filters */}
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

                                <div className="space-y-2">
                                    <label className="text-sm font-medium flex justify-between">
                                        <span>{t("grid.min_votes")}</span>
                                        <span className="text-muted-foreground">{filters.minVoteCount ? `${filters.minVoteCount}+` : "Any"}</span>
                                    </label>
                                    <input
                                        type="range"
                                        min="0"
                                        max="10000"
                                        step="100"
                                        className="w-full accent-primary"
                                        value={filters.minVoteCount || 0}
                                        onChange={(e) => setFilters(prev => ({ ...prev, minVoteCount: parseInt(e.target.value) || undefined }))}
                                    />
                                </div>

                                <div className="space-y-2">
                                    <label className="text-sm font-medium">{t("grid.language")}</label>
                                    <div className="relative">
                                        <select
                                            className="w-full appearance-none px-3 py-2 rounded-md border bg-background pr-8 focus:outline-none focus:ring-2 focus:ring-primary/50 cursor-pointer hover:bg-muted/50 transition-colors"
                                            value={filters.originalLanguage || ""}
                                            onChange={(e) => setFilters(prev => ({ ...prev, originalLanguage: e.target.value || undefined }))}
                                        >
                                            <option value="">{t("grid.any_language")}</option>
                                            <option value="en">English</option>
                                            <option value="fr">French</option>
                                            <option value="es">Spanish</option>
                                            <option value="ko">Korean</option>
                                            <option value="ja">Japanese</option>
                                            <option value="de">German</option>
                                            <option value="it">Italian</option>
                                            <option value="zh">Chinese</option>
                                        </select>
                                        <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
                                            <Globe className="w-4 h-4 text-muted-foreground" />
                                        </div>
                                    </div>
                                </div>

                                <div className="space-y-2">
                                    <label className="text-sm font-medium">{t("grid.keywords")}</label>
                                    <input
                                        type="text"
                                        placeholder="time loop, dystopia..."
                                        className="w-full px-3 py-2 rounded-md border bg-background"
                                        value={filters.keywords || ""}
                                        onChange={(e) => setFilters(prev => ({ ...prev, keywords: e.target.value }))}
                                    />
                                </div>
                            </div>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {
                isLoading ? (
                    <div
                        className="grid grid-cols-1 min-[400px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 md:gap-6"
                        role="status"
                        aria-label="Loading recommendations"
                        aria-live="polite"
                    >
                        {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
                            <div
                                key={i}
                                className="relative h-[340px] bg-[oklch(0.08_0_0)] border border-[oklch(0.18_0_0)] overflow-hidden"
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
                                    <div className="h-2 w-1/3 bg-[oklch(0.10_0_0)]" />
                                </div>
                            </div>
                        ))}
                    </div>
                ) : error ? (
                    <div className="p-6 bg-destructive/10 border border-destructive/20 rounded-lg">
                        <p className="text-destructive">
                            Failed to load recommendations. Please try again.
                        </p>
                    </div>
                ) : !recommendations || recommendations.length === 0 ? (
                    <div className="p-6 bg-muted/50 rounded-lg text-center">
                        <p className="text-muted-foreground">
                            No recommendations found. Try adjusting your filters.
                        </p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 min-[400px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 md:gap-6">
                        {recommendations.map((rec, index) => (
                            <motion.div
                                key={rec.movie.tmdb_id}
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: index * 0.05 }}
                            >
                                <MovieCard
                                    id={rec.movie.tmdb_id}
                                    title={rec.movie.title}
                                    posterPath={rec.movie.poster_path}
                                    year={rec.movie.year}
                                    runtime={rec.movie.runtime}
                                    rating={rec.movie.vote_average}
                                    matchScore={rec.similarity_score}
                                    genres={rec.movie.genres}
                                    providers={rec.streaming_providers}
                                    overview={rec.movie.overview}
                                    href={`https://letterboxd.com/tmdb/${rec.movie.tmdb_id}`}
                                    variant="grid"
                                    hideProvidersOnFront={true}
                                    vectorbox_score={rec.movie.vectorbox_score}
                                    imdb_rating={rec.movie.imdb_rating}
                                    metacritic_rating={rec.movie.metacritic_rating}
                                    rotten_tomatoes_rating={rec.movie.rotten_tomatoes_rating}
                                    contributors={rec.contributors}
                                />
                            </motion.div>
                        ))}
                    </div>
                )
            }
        </div >
    );
}
