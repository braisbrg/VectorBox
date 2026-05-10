"use client";

import { useEffect, useState } from "react";
import { Search, Loader2, Film, X } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import Image from "next/image";
import { getTMDBImageUrl, api } from "@/lib/api";
import { MovieCard } from "@/components/ui/movie-card";

interface MoreLikeThisProps {
    userId?: number;
}

interface SearchedMovie {
    tmdb_id: number;
    title: string;
    poster_path?: string;
    year?: number;
    overview?: string;
}

interface SimilarMovie {
    movie_id: number;
    title: string;
    poster_path?: string;
    similarity_score: number;
    year?: number;
    streaming_providers: string[];
    vote_average?: number;
    overview?: string;
    vectorbox_score?: number;
    imdb_rating?: number;
    metacritic_rating?: number;

    title_es?: string;
    overview_es?: string;
}

const MAX_SEEDS = 5;

export function MoreLikeThis({}: MoreLikeThisProps) {
    const [searchQuery, setSearchQuery] = useState("");
    const [searchResults, setSearchResults] = useState<SearchedMovie[]>([]);
    const [seedMovies, setSeedMovies] = useState<SearchedMovie[]>([]);
    const [recommendations, setRecommendations] = useState<SimilarMovie[]>([]);

    const searchMutation = useMutation({
        mutationFn: async (query: string) => {
            // Public TMDB search backing the More Like This modal
            const res = await api.get(`/api/search/autocomplete?q=${encodeURIComponent(query)}`);
            return res.data as Array<{ tmdb_id: number; title: string; year?: number; poster_path?: string; overview?: string }>;
        },
        onSuccess: (data) => {
            const seen = new Set(seedMovies.map((m) => m.tmdb_id));
            setSearchResults(
                data
                    .filter((m) => !seen.has(m.tmdb_id))
                    .map((m) => ({
                        tmdb_id: m.tmdb_id,
                        title: m.title,
                        year: m.year,
                        poster_path: m.poster_path,
                        overview: m.overview,
                    }))
            );
        },
    });

    const similarMutation = useMutation({
        mutationFn: async (tmdbIds: number[]) => {
            const res = await api.post("/api/recommendations/similar/multi", {
                tmdb_ids: tmdbIds,
                limit: 12,
            });
            return res.data;
        },
        onSuccess: (data) => {
            const map = new Map<number, SimilarMovie>();
            (data.recommendations || []).forEach((r: SimilarMovie) => {
                if (!map.has(r.movie_id)) map.set(r.movie_id, r);
            });
            setRecommendations(Array.from(map.values()));
        },
    });

    // Re-run similarity whenever seed set changes (debounced)
    useEffect(() => {
        if (seedMovies.length === 0) {
            setRecommendations([]);
            return;
        }
        const t = setTimeout(() => {
            similarMutation.mutate(seedMovies.map((m) => m.tmdb_id));
        }, 400);
        return () => clearTimeout(t);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [seedMovies]);

    const handleSearch = (e: React.FormEvent) => {
        e.preventDefault();
        if (searchQuery.trim()) searchMutation.mutate(searchQuery);
    };

    const addSeed = (movie: SearchedMovie) => {
        if (seedMovies.length >= MAX_SEEDS) return;
        if (seedMovies.find((m) => m.tmdb_id === movie.tmdb_id)) return;
        setSeedMovies((prev) => [...prev, movie]);
        setSearchResults([]);
        setSearchQuery("");
    };

    const removeSeed = (tmdbId: number) => {
        setSeedMovies((prev) => prev.filter((m) => m.tmdb_id !== tmdbId));
    };

    const seedTitles = seedMovies.map((m) => m.title).join(", ");

    return (
        <div className="space-y-6">
            <div className="text-center mb-8">
                <h2 className="text-3xl font-bold mb-2">More Like This</h2>
                <p className="text-muted-foreground">
                    Add up to {MAX_SEEDS} films and we&apos;ll blend their fingerprints to find similar ones.
                </p>
            </div>

            {/* Seed pills */}
            <div className="max-w-2xl mx-auto flex flex-wrap items-center gap-2">
                    {seedMovies.map((m) => (
                        <div
                            key={m.tmdb_id}
                            className="flex items-center gap-1 border border-border px-2 py-1 font-mono text-xs"
                        >
                            <span className="text-foreground">{m.title}</span>
                            {m.year && <span className="text-muted-foreground">({m.year})</span>}
                            <button
                                onClick={() => removeSeed(m.tmdb_id)}
                                className="text-muted-foreground hover:text-red-500 ml-1"
                                aria-label={`Remove ${m.title}`}
                            >
                                <X className="w-3 h-3" />
                            </button>
                        </div>
                    ))}
                    {seedMovies.length < MAX_SEEDS && (
                        <span className="font-mono text-[10px] text-muted-foreground">
                            {seedMovies.length === 0
                                ? `ADD UP TO ${MAX_SEEDS} FILMS`
                                : `+ ${MAX_SEEDS - seedMovies.length} MORE`}
                        </span>
                    )}
                </div>

            {/* Search Bar */}
            {seedMovies.length < MAX_SEEDS && (
                <form onSubmit={handleSearch} className="relative max-w-2xl mx-auto">
                    <div className="relative flex items-center bg-background border rounded-lg shadow-sm overflow-hidden">
                        <div className="pl-4 text-muted-foreground">
                            <Film className="w-5 h-5" />
                        </div>
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder="Search for a film to add..."
                            className="w-full px-4 py-3 bg-transparent border-none focus:ring-0 placeholder:text-muted-foreground/50"
                        />
                        <button
                            type="submit"
                            disabled={searchMutation.isPending || !searchQuery.trim()}
                            className="px-6 py-3 bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50"
                        >
                            {searchMutation.isPending ? (
                                <Loader2 className="w-5 h-5 animate-spin" />
                            ) : (
                                <Search className="w-5 h-5" />
                            )}
                        </button>
                    </div>

                    <AnimatePresence>
                        {searchResults.length > 0 && (
                            <motion.div
                                initial={{ opacity: 0, y: -10 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -10 }}
                                className="absolute top-full left-0 right-0 mt-2 bg-card border rounded-lg shadow-xl max-h-96 overflow-y-auto z-50"
                            >
                                {searchResults.slice(0, 5).map((movie) => (
                                    <button
                                        key={movie.tmdb_id}
                                        onClick={() => addSeed(movie)}
                                        className="w-full flex items-start gap-4 p-4 hover:bg-muted transition-colors text-left border-b last:border-0"
                                    >
                                        <div className="relative w-16 h-24 flex-shrink-0 rounded-md overflow-hidden bg-muted shadow-sm">
                                            {movie.poster_path ? (
                                                <Image
                                                    src={getTMDBImageUrl(movie.poster_path)}
                                                    alt={movie.title}
                                                    fill
                                                    className="object-cover"
                                                    sizes="64px"
                                                />
                                            ) : (
                                                <div className="w-full h-full flex items-center justify-center">
                                                    <Film className="w-8 h-8 text-muted-foreground" />
                                                </div>
                                            )}
                                        </div>
                                        <div className="flex-1 min-w-0 py-1">
                                            <p className="font-bold text-lg truncate">{movie.title}</p>
                                            {movie.year && (
                                                <p className="text-sm text-muted-foreground mb-1">{movie.year}</p>
                                            )}
                                            {movie.overview && (
                                                <p className="text-xs text-muted-foreground line-clamp-2">
                                                    {movie.overview}
                                                </p>
                                            )}
                                        </div>
                                    </button>
                                ))}
                            </motion.div>
                        )}
                    </AnimatePresence>
                </form>
            )}

            {/* Loading */}
            {similarMutation.isPending && (
                <div className="flex items-center justify-center py-12">
                    <Loader2 className="w-8 h-8 animate-spin text-primary" />
                </div>
            )}

            {/* Recommendations */}
            <AnimatePresence>
                {recommendations.length > 0 && !similarMutation.isPending && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="space-y-4"
                    >
                        <h3 className="text-xl font-bold text-center">
                            Movies similar to{" "}
                            <span className="text-primary">{seedTitles}</span>
                        </h3>
                        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-4">
                            {recommendations.map((movie, index) => (
                                <motion.div
                                    key={movie.movie_id}
                                    initial={{ opacity: 0, scale: 0.9 }}
                                    animate={{ opacity: 1, scale: 1 }}
                                    transition={{ delay: index * 0.05 }}
                                >
                                    <MovieCard
                                        id={movie.movie_id}
                                        title={movie.title}
                                        posterPath={movie.poster_path}
                                        year={movie.year}
                                        badgeType="rating"
                                        rating={movie.vote_average}
                                        matchScore={movie.similarity_score}
                                        providers={movie.streaming_providers}
                                        overview={movie.overview}
                                        href={`https://letterboxd.com/tmdb/${movie.movie_id}`}
                                        variant="grid"
                                        vectorbox_score={movie.vectorbox_score}
                                        imdb_rating={movie.imdb_rating}
                                        metacritic_rating={movie.metacritic_rating}

                                        title_es={movie.title_es}
                                        overview_es={movie.overview_es}
                                        onInspect={() => {}}
                                    />
                                </motion.div>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Empty State */}
            {seedMovies.length === 0 && !searchMutation.isPending && searchResults.length === 0 && (
                <div className="text-center py-12 text-muted-foreground">
                    <Film className="w-16 h-16 mx-auto mb-4 opacity-50" />
                    <p>Search for a movie to get started</p>
                </div>
            )}
        </div>
    );
}
