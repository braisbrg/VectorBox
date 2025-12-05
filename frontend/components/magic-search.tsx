"use client";

import { useState } from "react";
import { Sparkles, Search, Loader2, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useMutation } from "@tanstack/react-query";
import { MovieCard } from "@/components/ui/movie-card";

interface MagicSearchProps {
    userId: number;
}

interface SearchResult {
    movie_id: number;
    title: string;
    overview: string;
    poster_path: string | null;
    score: number;
    year: number;
    runtime?: number;
    genres: string[];
    vote_average?: number;
    // Phase 12 Fields
    vectorbox_score?: number;
    imdb_rating?: number;
    metacritic_rating?: number;
    rotten_tomatoes_rating?: number;
    title_es?: string;
    overview_es?: string;
    streaming_providers?: string[];
}

import { useLanguage } from "@/components/language-provider";

export function MagicSearch({ userId }: MagicSearchProps) {
    const { t } = useLanguage();
    const [query, setQuery] = useState("");
    const [results, setResults] = useState<SearchResult[]>([]);
    const [intent, setIntent] = useState<any>(null);
    const [showResults, setShowResults] = useState(false);

    const searchMutation = useMutation({
        mutationFn: async (text: string) => {
            const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/search/natural`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: text, user_id: userId }),
            });
            if (!res.ok) throw new Error(t("search.error"));
            return res.json();
        },
        onSuccess: (data) => {
            setResults(data.results || []);
            setIntent(data.intent);
            setShowResults(true);
        },
    });

    const handleSearch = (e: React.FormEvent) => {
        e.preventDefault();
        if (query.trim()) {
            searchMutation.mutate(query);
        }
    };

    const clearSearch = () => {
        setQuery("");
        setResults([]);
        setIntent(null);
        setShowResults(false);
    };

    return (
        <div className="w-full max-w-5xl mx-auto mb-12 space-y-8">
            <div className="text-center space-y-2">
                <h2 className="text-4xl font-black font-space uppercase tracking-tighter text-acid-outline" data-text="AI_SEARCH_MODULE">
                    AI_SEARCH_MODULE
                </h2>
                <p className="text-zinc-500 font-mono text-sm uppercase tracking-widest">
                    // NEURAL_NET_ACTIVE //
                </p>
            </div>

            <form onSubmit={handleSearch} className="relative group">
                <div className="relative flex items-center bg-black border-2 border-primary shadow-[0_0_20px_rgba(204,255,0,0.15)] transition-all focus-within:shadow-[0_0_40px_rgba(204,255,0,0.3)]">
                    <div className="pl-6 text-primary animate-pulse">
                        <Sparkles className="w-6 h-6" />
                    </div>
                    <input
                        type="text"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder={t("search.placeholder")}
                        className="w-full px-6 py-6 bg-transparent border-none focus:ring-0 text-xl font-mono text-primary placeholder:text-zinc-700 uppercase"
                    />
                    {query && (
                        <button
                            type="button"
                            onClick={clearSearch}
                            className="px-4 text-zinc-600 hover:text-primary transition-colors"
                        >
                            <X className="w-6 h-6" />
                        </button>
                    )}
                    <button
                        type="submit"
                        disabled={searchMutation.isPending || !query.trim()}
                        className="px-8 py-6 bg-primary text-black font-black uppercase tracking-wider hover:bg-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed border-l-2 border-primary"
                    >
                        {searchMutation.isPending ? (
                            <Loader2 className="w-6 h-6 animate-spin" />
                        ) : (
                            <Search className="w-6 h-6" />
                        )}
                    </button>
                </div>
            </form>

            {/* Results Display */}
            <AnimatePresence>
                {showResults && results.length > 0 && (
                    <motion.div
                        initial={{ opacity: 0, y: -20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -20 }}
                        className="bg-card border rounded-xl p-6 shadow-lg"
                    >
                        <div className="flex items-center justify-between mb-4">
                            <div>
                                <h3 className="text-lg font-bold">Search Results</h3>
                                {intent && (
                                    <p className="text-sm text-muted-foreground mt-1">
                                        {intent.mood && <span>Mood: {intent.mood} • </span>}
                                        {intent.year_range && <span>Years: {intent.year_range} • </span>}
                                        {intent.runtime && <span>Runtime: {intent.runtime} • </span>}
                                        {results.length} movies found
                                    </p>
                                )}
                            </div>
                            <button
                                onClick={clearSearch}
                                className="text-sm text-muted-foreground hover:text-foreground underline"
                            >
                                Clear Results
                            </button>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
                            {results.slice(0, 6).map((movie, index) => (
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
                                        matchScore={movie.score ? Math.round(movie.score) : undefined}
                                        overview={movie.overview}
                                        genres={movie.genres}
                                        href={`https://letterboxd.com/tmdb/${movie.movie_id}`}
                                        variant="grid"
                                        rating={movie.vote_average}
                                        runtime={movie.runtime}
                                        providers={movie.streaming_providers}
                                        // Phase 12 Props
                                        vectorbox_score={movie.vectorbox_score}
                                        imdb_rating={movie.imdb_rating}
                                        metacritic_rating={movie.metacritic_rating}
                                        rotten_tomatoes_rating={movie.rotten_tomatoes_rating}
                                        title_es={movie.title_es}
                                        overview_es={movie.overview_es}
                                    />
                                </motion.div>
                            ))}
                        </div>

                        {results.length > 6 && (
                            <div className="mt-6 text-center">
                                <p className="text-sm text-muted-foreground">
                                    Showing top 6 of {results.length} results
                                </p>
                            </div>
                        )}
                    </motion.div>
                )}
            </AnimatePresence>

            {/* No Results Message */}
            <AnimatePresence>
                {showResults && results.length === 0 && !searchMutation.isPending && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="bg-muted/30 border border-dashed rounded-xl p-8 text-center"
                    >
                        <p className="text-muted-foreground">
                            No movies found matching your search. Try adjusting your query!
                        </p>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
