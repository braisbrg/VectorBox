"use client";

import { useState } from "react";
import { Sparkles, Search, Loader2, X, BrainCircuit } from "lucide-react";
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
    ai_reason?: string; // Phase 4
}

import { useLanguage } from "@/components/language-provider";

export function MagicSearch({ userId }: MagicSearchProps) {
    const { t } = useLanguage();
    const [query, setQuery] = useState("");
    const [isDeepAnalysis, setIsDeepAnalysis] = useState(false);
    const [results, setResults] = useState<SearchResult[]>([]);
    const [intent, setIntent] = useState<any>(null);

    const [showResults, setShowResults] = useState(false);
    const [page, setPage] = useState(0);
    const MESSAGES_PER_PAGE = 6;

    const searchMutation = useMutation({
        mutationFn: async (text: string) => {
            const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/search/natural`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    query: text,
                    user_id: userId,
                    use_deep_analysis: isDeepAnalysis
                }),
            });
            if (!res.ok) throw new Error(t("search.error"));
            return res.json();
        },
        onSuccess: (data) => {
            // Deduplicate results
            const uniqueResults = data.results ? Array.from(new Map(data.results.map((item: any) => [item.movie_id, item])).values()) : [];
            setResults(uniqueResults as SearchResult[]);
            setIntent(data.intent);
            setShowResults(true);
            setPage(0);
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
                <h2 className="text-4xl font-black font-space uppercase tracking-tighter text-acid-outline" data-text={t("search.module_title")}>
                    {t("search.module_title")}
                </h2>
                <div className="flex items-center justify-center gap-2">
                    <p className="text-zinc-500 font-mono text-sm uppercase tracking-widest">
                        {t("search.status_active")}
                    </p>
                    {isDeepAnalysis && (
                        <span className="text-[10px] px-2 py-0.5 border border-primary text-primary font-mono uppercase bg-primary/10 rounded-full animate-pulse">
                            High Intelligence
                        </span>
                    )}
                </div>
            </div>

            <form onSubmit={handleSearch} className="relative group">
                <div className={`relative flex items-center bg-black border-2 ${isDeepAnalysis ? 'border-primary shadow-[0_0_30px_rgba(204,255,0,0.2)]' : 'border-zinc-800 focus-within:border-primary'} shadow-[0_0_20px_rgba(204,255,0,0.15)] transition-all duration-300`}>

                    {/* Deep Analysis Toggle */}
                    <button
                        type="button"
                        onClick={() => setIsDeepAnalysis(!isDeepAnalysis)}
                        className={`pl-6 pr-4 focus:outline-none transition-colors ${isDeepAnalysis ? 'text-primary' : 'text-zinc-600 hover:text-zinc-400'}`}
                        title="Toggle Deep Analysis (Slower, Smarter)"
                    >
                        <BrainCircuit className="w-6 h-6" />
                    </button>

                    <div className="h-8 w-[1px] bg-zinc-800 mr-2" />

                    <input
                        type="text"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder={isDeepAnalysis ? "Ask complex questions..." : t("search.placeholder")}
                        className="w-full px-4 py-6 bg-transparent border-none focus:ring-0 text-xl font-mono text-primary placeholder:text-zinc-700 uppercase"
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
                {/* Helper Text */}
                <div className="absolute -bottom-6 left-0 right-0 text-center opacity-0 group-hover:opacity-100 transition-opacity">
                    <span className="text-[10px] text-zinc-500 font-mono uppercase tracking-widest">
                        {isDeepAnalysis ? "Powered by Llama 3.3 70B (Tier 2)" : "Powered by Llama 4 Scout (Tier 1)"}
                    </span>
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
                                <h3 className="text-lg font-bold flex items-center gap-2">
                                    Search Results
                                    {isDeepAnalysis && <BrainCircuit className="w-4 h-4 text-primary" />}
                                </h3>
                                {intent && (
                                    <p className="text-sm text-muted-foreground mt-1">
                                        {intent.semantic_query && <span className="italic block mb-1">"{intent.semantic_query}"</span>}
                                        {intent.reasoning && <span className="block text-xs text-primary/80 font-mono border-l-2 border-primary pl-2">{intent.reasoning}</span>}
                                    </p>
                                )}
                            </div>
                            <button
                                onClick={clearSearch}
                                className="text-sm text-muted-foreground hover:text-foreground underline"
                            >
                                {t("search.clear_results")}
                            </button>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
                            {results.slice(page * MESSAGES_PER_PAGE, (page + 1) * MESSAGES_PER_PAGE).map((movie, index) => (
                                <motion.div
                                    key={movie.movie_id}
                                    initial={{ opacity: 0, scale: 0.9 }}
                                    animate={{ opacity: 1, scale: 1 }}
                                    transition={{ delay: index * 0.05 }}
                                    className="relative group"
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
                                    {/* AI Reason Overlay for Deep Analysis */}
                                    {movie.ai_reason && (
                                        <div className="absolute bottom-0 left-0 right-0 bg-black/90 border-t border-primary p-3 transform translate-y-full group-hover:translate-y-0 transition-transform z-20">
                                            <p className="text-[10px] text-primary font-mono leading-tight">
                                                <BrainCircuit className="w-3 h-3 inline mr-1" />
                                                {movie.ai_reason}
                                            </p>
                                        </div>
                                    )}
                                </motion.div>
                            ))}
                        </div>

                        {results.length > MESSAGES_PER_PAGE && (
                            <div className="flex justify-center items-center gap-4 pt-4 border-t mt-4">
                                <button
                                    onClick={() => setPage(p => Math.max(0, p - 1))}
                                    disabled={page === 0}
                                    className="px-4 py-2 rounded-lg border hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    Previous
                                </button>
                                <span className="text-sm text-muted-foreground">
                                    Page {page + 1} of {Math.ceil(results.length / MESSAGES_PER_PAGE)}
                                </span>
                                <button
                                    onClick={() => setPage(p => p + 1)}
                                    disabled={page >= Math.ceil(results.length / MESSAGES_PER_PAGE) - 1}
                                    className="px-4 py-2 rounded-lg border hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    Next
                                </button>
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
                            {t("search.no_results")}
                        </p>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
