"use client";

import { useState } from "react";
import { Search, Loader2, Film } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import Image from "next/image";
import { getTMDBImageUrl, api } from "@/lib/api";
import { MovieCard } from "@/components/ui/movie-card";

interface MoreLikeThisProps {
    userId: number;
}

interface Movie {
    movie_id: number;
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
    // Phase 12 Fields
    vectorbox_score?: number;
    imdb_rating?: number;
    metacritic_rating?: number;
    rotten_tomatoes_rating?: number;
    title_es?: string;
    overview_es?: string;
}

export function MoreLikeThis({ userId }: MoreLikeThisProps) {
    const [searchQuery, setSearchQuery] = useState("");
    const [selectedMovie, setSelectedMovie] = useState<Movie | null>(null);
    const [searchResults, setSearchResults] = useState<Movie[]>([]);
    const [recommendations, setRecommendations] = useState<SimilarMovie[]>([]);

    // Search for a movie
    const searchMutation = useMutation({
        mutationFn: async (query: string) => {
            const res = await api.get(`/api/search/movies?query=${encodeURIComponent(query)}`);
            return res.data;
        },
        onSuccess: (data) => {
            const results = data.results || [];
            // Deduplicate by ID
            const uniqueResults = Array.from(new Map(results.map((m: Movie) => [m.movie_id, m])).values());
            setSearchResults(uniqueResults as Movie[]);
        },
    });

    // Get similar movies
    const similarMutation = useMutation({
        mutationFn: async (tmdbId: number) => {
            const res = await api.get(`/api/recommendations/similar/${tmdbId}?limit=12`);
            return res.data;
        },
        onSuccess: (data) => {
            // Deduplicate recommendations just in case
            const uniqueRecs = new Map();
            (data.recommendations || []).forEach((rec: SimilarMovie) => {
                if (!uniqueRecs.has(rec.movie_id)) {
                    uniqueRecs.set(rec.movie_id, rec);
                }
            });
            setRecommendations(Array.from(uniqueRecs.values()));
        },
    });

    const handleSearch = (e: React.FormEvent) => {
        e.preventDefault();
        if (searchQuery.trim()) {
            searchMutation.mutate(searchQuery);
        }
    };

    const handleSelectMovie = (movie: Movie) => {
        setSelectedMovie(movie);
        setSearchResults([]);
        setSearchQuery("");
        similarMutation.mutate(movie.movie_id);
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="text-center mb-8">
                <h2 className="text-3xl font-bold mb-2">More Like This</h2>
                <p className="text-muted-foreground">
                    Search for a movie and discover similar films you might love
                </p>
            </div>

            {/* Search Bar */}
            <form onSubmit={handleSearch} className="relative max-w-2xl mx-auto">
                <div className="relative flex items-center bg-background border rounded-lg shadow-sm overflow-hidden">
                    <div className="pl-4 text-muted-foreground">
                        <Film className="w-5 h-5" />
                    </div>
                    <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder="Search for a movie... (e.g., 'Inception', 'The Matrix')"
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

                {/* Search Results Dropdown */}
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
                                    key={movie.movie_id}
                                    onClick={() => handleSelectMovie(movie)}
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
                                            <p className="text-sm text-muted-foreground mb-1">
                                                {movie.year}
                                            </p>
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

            {/* Selected Movie */}
            {selectedMovie && (
                <div className="max-w-2xl mx-auto bg-card border rounded-lg p-4 flex items-center gap-4">
                    <div className="relative w-20 h-32 flex-shrink-0 rounded overflow-hidden bg-muted">
                        {selectedMovie.poster_path ? (
                            <Image
                                src={getTMDBImageUrl(selectedMovie.poster_path)}
                                alt={selectedMovie.title}
                                fill
                                className="object-cover"
                                sizes="80px"
                            />
                        ) : (
                            <div className="w-full h-full flex items-center justify-center">
                                <Film className="w-8 h-8 text-muted-foreground" />
                            </div>
                        )}
                    </div>
                    <div className="flex-1">
                        <h3 className="font-bold text-lg">{selectedMovie.title}</h3>
                        {selectedMovie.year && (
                            <p className="text-sm text-muted-foreground">{selectedMovie.year}</p>
                        )}
                        <p className="text-sm text-muted-foreground mt-1">
                            Finding similar movies...
                        </p>
                    </div>
                </div>
            )}

            {/* Loading State */}
            {similarMutation.isPending && (
                <div className="flex items-center justify-center py-12">
                    <Loader2 className="w-8 h-8 animate-spin text-primary" />
                </div>
            )}

            {/* Recommendations Grid */}
            <AnimatePresence>
                {recommendations.length > 0 && !similarMutation.isPending && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        className="space-y-4"
                    >
                        <h3 className="text-xl font-bold text-center">
                            Movies Similar to {selectedMovie?.title}
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
                                        // Phase 12 Props
                                        vectorbox_score={movie.vectorbox_score}
                                        imdb_rating={movie.imdb_rating}
                                        metacritic_rating={movie.metacritic_rating}
                                        rotten_tomatoes_rating={movie.rotten_tomatoes_rating}
                                        title_es={movie.title_es}
                                        overview_es={movie.overview_es}
                                        onInspect={() => {}} // Placeholder
                                    />
                                </motion.div>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Empty State */}
            {!selectedMovie && !searchMutation.isPending && searchResults.length === 0 && (
                <div className="text-center py-12 text-muted-foreground">
                    <Film className="w-16 h-16 mx-auto mb-4 opacity-50" />
                    <p>Search for a movie to get started</p>
                </div>
            )}
        </div>
    );
}
