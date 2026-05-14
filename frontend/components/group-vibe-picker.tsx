"use client";

import { useState } from "react";
import { Users, Search, X, Sparkles } from "lucide-react";
import { m, AnimatePresence } from "framer-motion";
import { getGroupVibe, RecommendationResponse } from "@/lib/api";
import { MovieCard } from "@/components/ui/movie-card";

export function GroupVibePicker() {
    const [usernames, setUsernames] = useState<string[]>([]);
    const [currentInput, setCurrentInput] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const [recommendations, setRecommendations] = useState<RecommendationResponse[]>([]);
    const [error, setError] = useState<string | null>(null);

    const handleAddUser = (e?: React.FormEvent) => {
        e?.preventDefault();
        if (currentInput.trim() && !usernames.includes(currentInput.trim())) {
            setUsernames([...usernames, currentInput.trim()]);
            setCurrentInput("");
        }
    };

    const handleRemoveUser = (username: string) => {
        setUsernames(usernames.filter(u => u !== username));
    };

    const handleAnalyze = async () => {
        if (usernames.length === 0) return;

        setIsLoading(true);
        setError(null);
        setRecommendations([]);

        try {
            const results = await getGroupVibe(usernames);
            setRecommendations(results);
        } catch (err) {
            console.error(err);
            setError("Failed to analyze group vibe. Make sure users are synced.");
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="space-y-6">
            <div className="bg-card border rounded-xl p-6">
                <div className="flex items-center gap-2 mb-4">
                    <Users className="size-5 text-primary" />
                    <h2 className="text-xl font-semibold font-space">Group Vibe Check</h2>
                </div>

                <p className="text-sm text-muted-foreground mb-4">
                    Enter multiple Letterboxd usernames to find movies that match everyone's taste.
                </p>

                <div className="space-y-4">
                    <form onSubmit={handleAddUser} className="flex gap-2">
                        <input
                            type="text"
                            placeholder="Add username..."
                            value={currentInput}
                            onChange={(e) => setCurrentInput(e.target.value)}
                            className="flex-1 px-3 py-2 rounded-md border bg-background"
                        />
                        <button
                            type="submit"
                            disabled={!currentInput.trim()}
                            className="px-4 py-2 bg-secondary text-secondary-foreground rounded-md hover:bg-secondary/80"
                        >
                            Add
                        </button>
                    </form>

                    <div className="flex flex-wrap gap-2">
                        <AnimatePresence>
                            {usernames.map(username => (
                                <m.div
                                    key={username}
                                    initial={{ opacity: 0, scale: 0.8 }}
                                    animate={{ opacity: 1, scale: 1 }}
                                    exit={{ opacity: 0, scale: 0.8 }}
                                    className="flex items-center gap-1.5 px-3 py-1 bg-primary/10 text-primary border border-primary/20 rounded-full text-sm"
                                >
                                    <span>{username}</span>
                                    <button
                                        onClick={() => handleRemoveUser(username)}
                                        className="hover:text-destructive"
                                    >
                                        <X className="size-3" />
                                    </button>
                                </m.div>
                            ))}
                        </AnimatePresence>
                    </div>

                    <button
                        onClick={handleAnalyze}
                        disabled={usernames.length < 2 || isLoading}
                        className="w-full py-3 bg-primary text-black font-bold rounded-md hover:bg-primary/90 disabled:opacity-50 flex items-center justify-center gap-2 transition-all"
                    >
                        {isLoading ? (
                            <Sparkles className="size-4 animate-spin" />
                        ) : (
                            <Sparkles className="size-4" />
                        )}
                        {isLoading ? "Analyzing Vibe..." : "Find Group Movies"}
                    </button>

                    {error && (
                        <p className="text-sm text-destructive text-center">{error}</p>
                    )}
                </div>
            </div>

            {/* Results Grid */}
            {recommendations.length > 0 && (
                <div className="space-y-4">
                    <h3 className="text-lg font-semibold">Group Recommendations</h3>
                    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-4">
                        {recommendations.map((item) => {
                            if (!item.movie) return null;
                            return (
                                <MovieCard
                                    key={item.movie.tmdb_id}
                                    id={item.movie.tmdb_id}
                                    title={item.movie.title}
                                    posterPath={item.movie.poster_path}
                                    year={item.movie.year}
                                    runtime={item.movie.runtime}
                                    rating={item.movie.vote_average}
                                    genres={item.movie.genres}
                                    overview={item.movie.overview}
                                    providers={item.providers}
                                    contributors={item.contributors}
                                    badgeType="rating"
                                    href={`https://letterboxd.com/tmdb/${item.movie.tmdb_id}`}
                                    vectorbox_score={item.movie.vectorbox_score}
                                    imdb_rating={item.movie.imdb_rating}
                                    metacritic_rating={item.movie.metacritic_rating}

                                    title_es={item.movie.title_es}
                                    onInspect={() => {}} // Placeholder
                                />
                            );
                        })}
                    </div>
                </div>
            )}
        </div>
    );
}
