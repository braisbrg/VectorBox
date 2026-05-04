"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Sparkles, Star } from "lucide-react";
import { getTMDBImageUrl } from "@/lib/api";

type Signal = "positive" | "neutral" | "negative";

interface GuestFeedMovie {
    tmdb_id: number;
    title: string;
    year?: number;
    poster_path?: string;
    overview?: string;
    genres?: string[];
    vectorbox_score?: number;
    vote_average?: number;
    runtime?: number;
    directors?: string[];
}

const RATINGS_KEY = "vb_guest_ratings";

function readRatings(): Record<number, Signal> {
    if (typeof window === "undefined") return {};
    try {
        const raw = window.localStorage.getItem(RATINGS_KEY);
        return raw ? (JSON.parse(raw) as Record<number, Signal>) : {};
    } catch {
        return {};
    }
}

function writeRatings(ratings: Record<number, Signal>) {
    window.localStorage.setItem(RATINGS_KEY, JSON.stringify(ratings));
}

export default function ExplorePage() {
    const searchParams = useSearchParams();
    const isGuest = searchParams.get("guest") === "true";

    const [ratings, setRatings] = useState<Record<number, Signal>>({});
    const [hydrated, setHydrated] = useState(false);

    useEffect(() => {
        setRatings(readRatings());
        setHydrated(true);
    }, []);

    const ratingsKey = useMemo(() => JSON.stringify(ratings), [ratings]);

    const { data: movies = [], isLoading } = useQuery<GuestFeedMovie[]>({
        queryKey: ["guest-feed", ratingsKey],
        queryFn: async () => {
            const API_URL = process.env.NEXT_PUBLIC_API_URL || "";
            const res = await fetch(`${API_URL}/api/public/guest-feed`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: ratingsKey,
            });
            if (!res.ok) throw new Error("Failed to load guest feed");
            return (await res.json()) as GuestFeedMovie[];
        },
        enabled: hydrated,
        staleTime: 30_000,
    });

    const positiveCount = useMemo(
        () => Object.values(ratings).filter((s) => s === "positive").length,
        [ratings]
    );
    const usingPersonalized = positiveCount >= 3;

    const handleRate = (tmdbId: number, signal: Signal) => {
        setRatings((prev) => {
            const next = { ...prev };
            if (next[tmdbId] === signal) delete next[tmdbId];
            else next[tmdbId] = signal;
            writeRatings(next);
            return next;
        });
    };

    return (
        <div className="min-h-screen bg-background text-foreground">
            {/* Top bar */}
            <header className="border-b border-border/50 px-4 py-3">
                <div className="max-w-6xl mx-auto flex items-center justify-between">
                    <Link href="/explore" className="text-lg font-black tracking-tighter font-mono uppercase">
                        VECTOR<span className="text-primary">BOX</span>
                    </Link>
                    <div className="flex gap-2">
                        <Link
                            href="/login"
                            className="border border-border text-zinc-400 px-3 py-1.5 font-mono text-xs uppercase hover:border-zinc-500 hover:text-zinc-300 transition-colors"
                        >
                            [ LOG IN ]
                        </Link>
                        <Link
                            href="/login"
                            className="border border-primary text-primary px-3 py-1.5 font-mono text-xs uppercase hover:bg-primary hover:text-background transition-colors"
                        >
                            [ SIGN UP ]
                        </Link>
                    </div>
                </div>
            </header>

            {/* Guest banner */}
            {isGuest && (
                <div className="border-b border-border/50 bg-zinc-900/30 px-4 py-3">
                    <div className="max-w-6xl mx-auto flex items-center justify-between gap-4 flex-wrap">
                        <div className="font-mono text-xs text-zinc-400">
                            <span className="text-primary mr-2">[ {usingPersonalized ? "BASED ON YOUR RATINGS" : "POPULAR FILMS"} ]</span>
                            {usingPersonalized
                                ? "Sign up to unlock your full personalized feed."
                                : "Rate at least 3 films to unlock personalized recommendations."}
                        </div>
                        <Link
                            href="/login?migrate=true"
                            className="border border-primary text-primary px-3 py-1.5 font-mono text-xs uppercase hover:bg-primary hover:text-background transition-colors"
                        >
                            [ SAVE PROFILE ]
                        </Link>
                    </div>
                </div>
            )}

            <main className="max-w-6xl mx-auto px-4 py-8">
                {!hydrated || isLoading ? (
                    <div className="flex items-center justify-center py-20">
                        <div className="text-center space-y-4">
                            <div className="w-8 h-8 border-2 border-primary border-t-transparent animate-spin mx-auto" />
                            <p className="font-mono text-xs text-zinc-600 uppercase tracking-widest">Loading films...</p>
                        </div>
                    </div>
                ) : movies.length === 0 ? (
                    <div className="text-center py-20 space-y-4">
                        <Sparkles className="w-12 h-12 text-primary mx-auto opacity-60" />
                        <p className="font-mono text-sm text-zinc-500">No films available right now.</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
                        {movies.map((movie, i) => (
                            <motion.div
                                key={movie.tmdb_id}
                                initial={{ opacity: 0, y: 8 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: Math.min(i * 0.03, 0.3) }}
                                className="space-y-2"
                            >
                                <div className="relative aspect-[2/3] border border-border/30 overflow-hidden bg-zinc-900">
                                    {movie.poster_path ? (
                                        <Image
                                            src={getTMDBImageUrl(movie.poster_path, "w342")}
                                            alt={movie.title}
                                            fill
                                            className="object-cover"
                                            sizes="(max-width: 640px) 50vw, (max-width: 1024px) 25vw, 20vw"
                                        />
                                    ) : (
                                        <div className="w-full h-full flex items-center justify-center font-mono text-zinc-700 text-xs">
                                            NO POSTER
                                        </div>
                                    )}
                                    {movie.vectorbox_score && (
                                        <div className="absolute top-1 right-1 px-1.5 py-0.5 bg-background/80 border border-primary/50 font-mono text-[10px] text-primary">
                                            VB {Math.round(movie.vectorbox_score)}
                                        </div>
                                    )}
                                </div>
                                <div className="space-y-1">
                                    <p className="font-mono text-xs text-foreground truncate" title={movie.title}>
                                        {movie.title}
                                    </p>
                                    <div className="flex items-center justify-between text-[10px] font-mono text-zinc-500">
                                        <span>{movie.year}</span>
                                        {movie.vote_average && (
                                            <span className="flex items-center gap-1">
                                                <Star className="w-3 h-3 text-yellow-500" />
                                                {movie.vote_average.toFixed(1)}
                                            </span>
                                        )}
                                    </div>
                                    <div className="flex gap-1">
                                        {([
                                            { signal: "negative" as Signal, label: "✕", cls: "hover:border-red-500 hover:text-red-500" },
                                            { signal: "neutral" as Signal,  label: "~", cls: "hover:border-zinc-400 hover:text-zinc-400" },
                                            { signal: "positive" as Signal, label: "♥", cls: "hover:border-primary hover:text-primary" },
                                        ]).map(({ signal, label, cls }) => {
                                            const active = ratings[movie.tmdb_id] === signal;
                                            return (
                                                <button
                                                    key={signal}
                                                    onClick={() => handleRate(movie.tmdb_id, signal)}
                                                    className={`flex-1 border h-7 font-mono text-xs flex items-center justify-center transition-colors ${
                                                        active
                                                            ? "border-primary text-primary"
                                                            : `border-border text-zinc-600 ${cls}`
                                                    }`}
                                                    aria-label={`Rate ${movie.title} ${signal}`}
                                                >
                                                    {label}
                                                </button>
                                            );
                                        })}
                                    </div>
                                </div>
                            </motion.div>
                        ))}
                    </div>
                )}
            </main>
        </div>
    );
}
