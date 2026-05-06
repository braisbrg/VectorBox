"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@clerk/nextjs";
import { motion, AnimatePresence } from "framer-motion";
import Image from "next/image";
import { ThumbsUp, ThumbsDown, Minus, Undo2, SkipForward, Star, Sparkles, Search } from "lucide-react";
import { getTMDBImageUrl } from "@/lib/api";

interface OnboardingMovie {
    tmdb_id: number;
    title: string;
    year?: number;
    poster_path?: string;
    overview?: string;
    genres?: string[];
    vote_average?: number;
    vote_count?: number;
    runtime?: number;
    original_language?: string;
    vectorbox_score?: number;
}

type Signal = "positive" | "neutral" | "negative";

// Button config in spec wireframe order (left → right): NOT FOR ME, IT WAS OK, LOVED IT.
// Keyboard 1/2/3 map left → right so the digit on the badge always matches the key.
const SIGNAL_BUTTONS: {
    signal: Signal;
    key: "1" | "2" | "3";
    label: string;
    icon: typeof ThumbsUp;
    color: string;
}[] = [
    { signal: "negative", key: "1", label: "NOT FOR ME", icon: ThumbsDown, color: "text-red-400" },
    { signal: "neutral",  key: "2", label: "IT WAS OK",  icon: Minus,      color: "text-zinc-400" },
    { signal: "positive", key: "3", label: "LOVED IT",   icon: ThumbsUp,   color: "text-primary" },
];

const MOVIES_KEY = "vb_onboarding_movies";
const PROGRESS_KEY = "vb_onboarding_progress";
const RATINGS_KEY = "vb_guest_ratings";

export default function OnboardingCarouselPage() {
    const router = useRouter();
    const { isSignedIn } = useAuth();

    const [movies, setMovies] = useState<OnboardingMovie[]>([]);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [ratings, setRatings] = useState<Record<number, Signal>>({});
    const [ratedCount, setRatedCount] = useState(0);
    const [loading, setLoading] = useState(true);
    const [showPeek, setShowPeek] = useState(false);
    const [showRegistration, setShowRegistration] = useState(false);
    const [direction, setDirection] = useState(1);
    const [searchOpen, setSearchOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState("");
    const [searchResults, setSearchResults] = useState<OnboardingMovie[]>([]);
    const [searchLoading, setSearchLoading] = useState(false);
    const [isLoadingMore, setIsLoadingMore] = useState(false);
    const [page, setPage] = useState(1);
    const isMounted = useRef(true);

    useEffect(() => {
        return () => { isMounted.current = false; };
    }, []);

    // Mount: prefer cached movies+progress over a fresh fetch so a guest who
    // refreshes mid-carousel keeps their place. Pole randomness would otherwise
    // re-roll the first 5 films and force the saved-progress check to discard.
    useEffect(() => {
        const hydrate = async () => {
            try {
                const savedMoviesRaw = localStorage.getItem(MOVIES_KEY);
                const savedProgressRaw = localStorage.getItem(PROGRESS_KEY);
                const savedRatingsRaw = localStorage.getItem(RATINGS_KEY);

                if (savedMoviesRaw && savedProgressRaw) {
                    try {
                        const savedMovies: OnboardingMovie[] = JSON.parse(savedMoviesRaw);
                        const savedProgress = JSON.parse(savedProgressRaw);
                        if (Array.isArray(savedMovies) && savedMovies.length > 0) {
                            if (!isMounted.current) return;
                            setMovies(savedMovies);
                            setCurrentIndex(savedProgress.currentIndex || 0);
                            setRatedCount(savedProgress.ratedCount || 0);
                            if (savedRatingsRaw) {
                                try { setRatings(JSON.parse(savedRatingsRaw)); } catch { /* ignore */ }
                            }
                            setLoading(false);
                            return;
                        }
                    } catch { /* corrupt — fall through to fetch */ }
                }

                // No usable cache — fetch and persist
                const savedTags = localStorage.getItem("vb_guest_tags");
                const avoided = savedTags ? JSON.parse(savedTags).avoided || [] : [];
                const params = new URLSearchParams(avoided.length > 0 ? { avoided_tags: avoided.join(",") } : {});
                
                const API_URL = process.env.NEXT_PUBLIC_API_URL || "";
                const res = await fetch(`${API_URL}/api/onboarding/movies?${params.toString()}`);
                if (!res.ok) throw new Error("Failed to fetch movies");
                const data: OnboardingMovie[] = await res.json();
                if (!isMounted.current) return;
                setMovies(data);
                localStorage.setItem(MOVIES_KEY, JSON.stringify(data));
            } catch (e) {
                console.error("Failed to load onboarding movies:", e);
            } finally {
                if (isMounted.current) setLoading(false);
            }
        };
        hydrate();
    }, []);

    // Persist helper
    const persist = useCallback(
        (newRatings: Record<number, Signal>, newIndex: number, newCount: number) => {
            localStorage.setItem(RATINGS_KEY, JSON.stringify(newRatings));
            localStorage.setItem(
                PROGRESS_KEY,
                JSON.stringify({
                    currentIndex: newIndex,
                    ratedCount: newCount,
                    movieIds: movies.map((m) => m.tmdb_id),
                })
            );
        },
        [movies]
    );

    // Rate handler
    const handleRate = useCallback(
        (signal: Signal) => {
            if (currentIndex >= movies.length) return;
            const movie = movies[currentIndex];
            const newRatings = { ...ratings, [movie.tmdb_id]: signal };
            const newCount = ratedCount + 1;
            const newIndex = currentIndex + 1;
            setDirection(1);
            setRatings(newRatings);
            setRatedCount(newCount);
            setCurrentIndex(newIndex);
            persist(newRatings, newIndex, newCount);

            if (newCount === 10) setShowPeek(true);
            if (newCount >= 15 && !showRegistration) setShowRegistration(true);
        },
        [currentIndex, movies, ratings, ratedCount, persist, showRegistration]
    );

    // Skip
    const handleSkip = useCallback(() => {
        if (currentIndex >= movies.length) return;
        setDirection(1);
        const newIndex = currentIndex + 1;
        setCurrentIndex(newIndex);
        persist(ratings, newIndex, ratedCount);
    }, [currentIndex, movies.length, ratings, ratedCount, persist]);

    // Undo
    const handleUndo = useCallback(() => {
        if (currentIndex <= 0) return;
        const prevIndex = currentIndex - 1;
        const prevMovie = movies[prevIndex];
        const newRatings = { ...ratings };
        const wasRated = prevMovie.tmdb_id in newRatings;
        delete newRatings[prevMovie.tmdb_id];
        const newCount = wasRated ? ratedCount - 1 : ratedCount;
        setDirection(-1);
        setCurrentIndex(prevIndex);
        setRatings(newRatings);
        setRatedCount(newCount);
        persist(newRatings, prevIndex, newCount);
    }, [currentIndex, movies, ratings, ratedCount, persist]);

    const loadMoreMovies = useCallback(async () => {
        setIsLoadingMore(true);
        const nextPage = page + 1;
        const shownIds = movies.map(m => m.tmdb_id).join(",");
        const savedTags = localStorage.getItem("vb_guest_tags");
        const avoided = savedTags ? JSON.parse(savedTags).avoided || [] : [];
        
        const params = new URLSearchParams({
            page: nextPage.toString(),
            exclude_ids: shownIds,
            ...(avoided.length > 0 && { avoided_tags: avoided.join(",") }),
        });
        
        try {
            const API_URL = process.env.NEXT_PUBLIC_API_URL || "";
            const response = await fetch(`${API_URL}/api/onboarding/movies?${params.toString()}`);
            if (!response.ok) throw new Error("Failed to fetch more movies");
            const newMovies = await response.json();
            
            setMovies(prev => {
                const combined = [...prev, ...newMovies];
                localStorage.setItem(MOVIES_KEY, JSON.stringify(combined));
                return combined;
            });
            setPage(nextPage);
        } catch (e) {
            console.error("Failed to load more movies:", e);
        } finally {
            setIsLoadingMore(false);
        }
    }, [movies, page]);

    useEffect(() => {
        // When 3 movies from the end, load more
        if (movies.length > 0 && currentIndex >= movies.length - 3 && !isLoadingMore) {
            loadMoreMovies();
        }
    }, [currentIndex, movies.length, isLoadingMore, loadMoreMovies]);

    // Search modal helpers
    const openSearch = useCallback(() => {
        setSearchOpen(true);
    }, []);
    const closeSearch = useCallback(() => {
        setSearchOpen(false);
        setSearchQuery("");
        setSearchResults([]);
    }, []);

    const handleSearchRate = useCallback(
        (movie: OnboardingMovie, signal: Signal) => {
            // Save rating only — don't add to carousel pool or advance the deck.
            // The rating is persisted and will be migrated on signup.
            const newRatings = { ...ratings, [movie.tmdb_id]: signal };
            const newCount = Object.keys(newRatings).length;
            setRatings(newRatings);
            setRatedCount(newCount);
            persist(newRatings, currentIndex, newCount);
            if (newCount === 10) setShowPeek(true);
            if (newCount >= 15 && !showRegistration) setShowRegistration(true);
            closeSearch();
        },
        [ratings, currentIndex, showRegistration, persist, closeSearch]
    );

    // Debounced search fetch
    useEffect(() => {
        if (!searchQuery || searchQuery.trim().length < 2) {
            setSearchResults([]);
            return;
        }
        let cancelled = false;
        setSearchLoading(true);
        const t = setTimeout(async () => {
            try {
                const API_URL = process.env.NEXT_PUBLIC_API_URL || "";
                const res = await fetch(
                    `${API_URL}/api/onboarding/search?q=${encodeURIComponent(searchQuery)}`
                );
                if (!res.ok) throw new Error("search failed");
                const data: OnboardingMovie[] = await res.json();
                if (!cancelled) setSearchResults(data);
            } catch (e) {
                if (!cancelled) setSearchResults([]);
                console.error("Search failed:", e);
            } finally {
                if (!cancelled) setSearchLoading(false);
            }
        }, 300);
        return () => {
            cancelled = true;
            clearTimeout(t);
        };
    }, [searchQuery]);

    // Keyboard handler. Single mount via refs so re-renders don't re-bind.
    // Wireframe order: 1 = NOT FOR ME, 2 = IT WAS OK, 3 = LOVED IT.
    const handlersRef = useRef({ handleRate, handleSkip, handleUndo, openSearch, closeSearch, searchOpen });
    useEffect(() => {
        handlersRef.current = { handleRate, handleSkip, handleUndo, openSearch, closeSearch, searchOpen };
    });
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            const target = e.target;
            const inField = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
            const h = handlersRef.current;
            // Modal owns the keyboard while open: only Escape passes through.
            if (h.searchOpen) {
                if (e.key === "Escape") {
                    e.preventDefault();
                    h.closeSearch();
                }
                return;
            }
            if (inField) return;
            switch (e.key) {
                case "1": h.handleRate("negative"); break;
                case "2": h.handleRate("neutral"); break;
                case "3": h.handleRate("positive"); break;
                case " ": e.preventDefault(); h.handleSkip(); break;
                case "ArrowLeft": h.handleUndo(); break;
                case "/":
                    e.preventDefault();
                    h.openSearch();
                    break;
            }
        };
        window.addEventListener("keydown", handler);
        return () => window.removeEventListener("keydown", handler);
    }, []);

    const handleSaveProfile = () => {
        router.push("/login?migrate=true");
    };

    const currentMovie = currentIndex < movies.length ? movies[currentIndex] : null;
    const progress = movies.length > 0 ? Math.min((ratedCount / 15) * 100, 100) : 0;

    if (loading) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-background">
                <div className="text-center space-y-4">
                    <div className="w-8 h-8 border-2 border-primary border-t-transparent animate-spin mx-auto" />
                    <p className="font-mono text-xs text-zinc-600 uppercase tracking-widest">Loading films...</p>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-background text-foreground relative overflow-hidden">
            <div className="absolute inset-0 bg-[url('/grid-pattern.svg')] opacity-5 pointer-events-none" />

            {/* Header */}
            <header className="relative z-20 border-b border-border/50 px-4 py-3">
                <div className="max-w-4xl mx-auto flex items-center justify-between">
                    <h1 className="text-lg font-black tracking-tighter font-mono uppercase">
                        VECTOR<span className="text-primary">BOX</span>
                    </h1>
                    <div className="flex items-center gap-3">
                        <span className="text-[10px] font-mono text-zinc-600 uppercase">
                            {ratedCount}/15 rated
                        </span>
                        {ratedCount >= 15 && (
                            <button
                                onClick={handleSaveProfile}
                                className="px-3 py-1.5 bg-primary text-black font-bold font-mono uppercase tracking-wider text-[10px] hover:bg-primary/90 transition-colors"
                            >
                                SAVE PROFILE
                            </button>
                        )}
                    </div>
                </div>
                {/* Progress bar */}
                <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-zinc-900">
                    <motion.div
                        className="h-full bg-primary"
                        initial={{ width: 0 }}
                        animate={{ width: `${progress}%` }}
                        transition={{ duration: 0.3 }}
                    />
                </div>
            </header>

            {/* Main content */}
            <main className="relative z-10 max-w-4xl mx-auto px-4 py-8">
                {currentMovie ? (
                    <div className="flex flex-col lg:flex-row gap-8 items-start">
                        {/* Poster */}
                        <div className="w-full lg:w-[300px] shrink-0">
                            <AnimatePresence mode="wait" custom={direction}>
                                <motion.div
                                    key={currentMovie.tmdb_id}
                                    custom={direction}
                                    initial={{ opacity: 0, x: direction * 60 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    exit={{ opacity: 0, x: direction * -60 }}
                                    transition={{ duration: 0.25 }}
                                    className="relative aspect-[2/3] w-full max-w-[300px] mx-auto lg:mx-0 border border-border/30 overflow-hidden"
                                >
                                    {currentMovie.poster_path ? (
                                        <Image
                                            src={getTMDBImageUrl(currentMovie.poster_path, "w500")}
                                            alt={currentMovie.title}
                                            fill
                                            className="object-cover"
                                            priority
                                        />
                                    ) : (
                                        <div className="w-full h-full bg-zinc-900 flex items-center justify-center">
                                            <span className="font-mono text-zinc-700 text-xs">NO POSTER</span>
                                        </div>
                                    )}
                                </motion.div>
                            </AnimatePresence>
                        </div>

                        {/* Info + Actions */}
                        <div className="flex-1 space-y-6">
                            <AnimatePresence mode="wait">
                                <motion.div
                                    key={currentMovie.tmdb_id}
                                    initial={{ opacity: 0, y: 10 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    exit={{ opacity: 0, y: -10 }}
                                    transition={{ duration: 0.2 }}
                                    className="space-y-4"
                                >
                                    <div>
                                        <h2 className="text-2xl md:text-3xl font-black tracking-tighter font-mono uppercase leading-tight">
                                            {currentMovie.title}
                                        </h2>
                                        <div className="flex items-center gap-3 mt-2 text-xs font-mono text-zinc-500">
                                            {currentMovie.year && <span>{currentMovie.year}</span>}
                                            {currentMovie.runtime && <span>{currentMovie.runtime} min</span>}
                                            {currentMovie.vote_average && (
                                                <span className="flex items-center gap-1">
                                                    <Star className="w-3 h-3 text-yellow-500" />
                                                    {currentMovie.vote_average.toFixed(1)}
                                                </span>
                                            )}
                                            {currentMovie.vectorbox_score && (
                                                <span className="text-primary">VB: {Math.round(currentMovie.vectorbox_score)}</span>
                                            )}
                                        </div>
                                    </div>

                                    {currentMovie.genres && currentMovie.genres.length > 0 && (
                                        <div className="flex flex-wrap gap-1.5">
                                            {currentMovie.genres.map((g) => (
                                                <span
                                                    key={g}
                                                    className="px-2 py-0.5 border border-zinc-800 text-[10px] font-mono text-zinc-500 uppercase"
                                                >
                                                    {g}
                                                </span>
                                            ))}
                                        </div>
                                    )}

                                    {currentMovie.overview && (
                                        <p className="text-sm text-zinc-400 leading-relaxed line-clamp-4">
                                            {currentMovie.overview}
                                        </p>
                                    )}
                                </motion.div>
                            </AnimatePresence>

                            {/* Rating buttons */}
                            <div className="space-y-3">
                                <div className="grid grid-cols-3 gap-2">
                                    {SIGNAL_BUTTONS.map(({ signal, key, label, icon: Icon, color }) => (
                                        <button
                                            key={signal}
                                            onClick={() => handleRate(signal)}
                                            className={`flex flex-col items-center gap-1.5 py-3 px-2 border border-zinc-800 hover:border-zinc-600 transition-all font-mono text-xs uppercase ${color} hover:bg-zinc-900/50`}
                                        >
                                            <Icon className="w-5 h-5" />
                                            <span className="text-[10px] flex items-center gap-1.5">
                                                <span className="font-mono opacity-60">{key}</span>
                                                {label}
                                            </span>
                                        </button>
                                    ))}
                                </div>

                                {/* Bottom controls bar */}
                                <div className="flex items-center justify-between gap-2 mt-2">
                                    {/* Skip — left aligned, dashed border to indicate it doesn't count */}
                                    <button
                                        onClick={handleSkip}
                                        className="border border-dashed border-zinc-600 text-zinc-500 px-4 py-2 
                                                   font-mono text-xs hover:border-zinc-400 hover:text-zinc-400 
                                                   transition-colors flex items-center gap-2"
                                    >
                                        <span className="text-[10px] opacity-60">SPACE</span>
                                        HAVEN'T SEEN IT
                                    </button>
                                    
                                    <div className="flex gap-2">
                                        {/* Undo */}
                                        <button
                                            onClick={handleUndo}
                                            disabled={currentIndex <= 0}
                                            className="border border-border text-zinc-500 px-3 py-2 font-mono text-xs
                                                       hover:border-zinc-400 hover:text-zinc-400 transition-colors
                                                       disabled:opacity-30 disabled:cursor-not-allowed
                                                       flex items-center gap-1"
                                        >
                                            <span className="text-[10px] opacity-60">←</span> UNDO
                                        </button>
                                        
                                        {/* Search */}
                                        <button
                                            onClick={openSearch}
                                            className="border border-border text-zinc-500 px-3 py-2 font-mono text-xs
                                                       hover:border-zinc-400 hover:text-zinc-400 transition-colors
                                                       flex items-center gap-1"
                                        >
                                            <span className="text-[10px] opacity-60">/</span> SEARCH
                                        </button>
                                    </div>
                                </div>
                                
                                {isLoadingMore && currentIndex >= movies.length - 1 && (
                                    <div className="font-mono text-xs text-zinc-500 animate-pulse text-center mt-4">
                                        [ LOADING MORE FILMS... ]
                                    </div>
                                )}

                                {/* Keyboard hints */}
                                <div className="hidden md:flex items-center justify-center gap-4 text-[9px] font-mono text-zinc-700 uppercase">
                                    <span>[1] Not For Me</span>
                                    <span>[2] It Was Ok</span>
                                    <span>[3] Loved It</span>
                                    <span>[Space] Skip</span>
                                    <span>[←] Undo</span>
                                    <span>[/] Search</span>
                                </div>

                            </div>
                        </div>
                    </div>
                ) : (
                    /* The "all films rated" state should only show if we truly ran out
                       of movies AND the user has rated them all (edge case, very rare) */
                    <div className="text-center py-20 space-y-6">
                        {ratedCount < 15 ? (
                            <div className="font-mono text-sm text-zinc-500 animate-pulse">
                                Loading more films...
                            </div>
                        ) : (
                            <>
                                <Sparkles className="w-12 h-12 text-primary mx-auto" />
                                <h2 className="text-2xl font-black font-mono uppercase tracking-tighter">
                                    ALL FILMS <span className="text-primary">RATED</span>
                                </h2>
                                <p className="text-zinc-500 font-mono text-sm">
                                    Your taste profile is ready. Save your profile to get personalized recommendations.
                                </p>
                                <button
                                    onClick={handleSaveProfile}
                                    className="px-8 py-3 bg-primary text-black font-bold font-mono uppercase tracking-wider text-sm hover:bg-primary/90 transition-colors glow-primary-hover"
                                >
                                    SAVE PROFILE
                                </button>
                            </>
                        )}
                    </div>
                )}

                {/* Peek banner at 10 ratings */}
                <AnimatePresence>
                    {showPeek && ratedCount >= 10 && ratedCount < 15 && (
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -10 }}
                            className="mt-8 border border-primary/30 bg-primary/5 p-4"
                        >
                            <div className="flex items-center justify-between">
                                <div className="space-y-1">
                                    <p className="text-xs font-mono text-primary uppercase tracking-wider font-bold">
                                        Nice taste — keep going!
                                    </p>
                                    <p className="text-[10px] font-mono text-zinc-500">
                                        Rate 5 more films to unlock your full profile.
                                    </p>
                                </div>
                                <button
                                    onClick={() => setShowPeek(false)}
                                    className="text-[10px] font-mono text-zinc-600 hover:text-zinc-400 transition-colors uppercase"
                                >
                                    [ DISMISS ]
                                </button>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>

                {/* Registration prompt at 15 ratings */}
                <AnimatePresence>
                    {showRegistration && ratedCount >= 15 && currentMovie && (
                        <motion.div
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -10 }}
                            className="mt-8 border border-primary/50 bg-card p-6 space-y-4"
                        >
                            <div className="space-y-2">
                                <h3 className="text-lg font-black font-mono uppercase tracking-tighter">
                                    PROFILE <span className="text-primary">READY</span>
                                </h3>
                                <p className="text-xs font-mono text-zinc-500">
                                    You&apos;ve rated {ratedCount} films. Save your profile to get personalized AI recommendations.
                                </p>
                            </div>
                            <div className="flex items-center gap-3 flex-wrap">
                                <button
                                    onClick={handleSaveProfile}
                                    className="px-6 py-2.5 bg-primary text-black font-bold font-mono uppercase tracking-wider text-xs hover:bg-primary/90 transition-colors glow-primary-hover"
                                >
                                    SAVE PROFILE
                                </button>
                                <button
                                    onClick={() => router.push("/explore?guest=true")}
                                    className="px-4 py-2.5 border border-border text-zinc-400 font-mono uppercase tracking-wider text-xs hover:border-zinc-500 hover:text-zinc-300 transition-colors"
                                >
                                    [ SKIP FOR NOW ]
                                </button>
                                <button
                                    onClick={() => setShowRegistration(false)}
                                    className="text-[10px] font-mono text-zinc-600 hover:text-zinc-400 transition-colors uppercase"
                                >
                                    [ KEEP EXPLORING ]
                                </button>
                            </div>
                            <p className="text-[10px] font-mono text-zinc-600">
                                Your ratings are saved in this browser. They&apos;ll be lost if you clear your cache.
                            </p>
                        </motion.div>
                    )}
                </AnimatePresence>

                {/* Constellation viz — decorative MVP */}
                {ratedCount > 0 && (
                    <div className="mt-12 border border-border/30 p-4">
                        <p className="text-[10px] font-mono text-zinc-700 uppercase tracking-widest mb-3">
                            // YOUR TASTE MAP //
                        </p>
                        {/* Decorative only — real placement requires Qdrant 384d → 2D PCA, deferred */}
                        <div className="relative h-32 overflow-hidden">
                            {Object.entries(ratings).map(([tmdbId, signal]) => {
                                const id = parseInt(tmdbId);
                                const movie = movies.find((m) => m.tmdb_id === id);
                                if (!movie) return null;
                                const x = (id % 97) / 97 * 90 + 5;
                                const y = (id % 53) / 53 * 80 + 10;
                                const color =
                                    signal === "positive" ? "bg-primary" :
                                    signal === "negative" ? "bg-red-500" : "bg-zinc-600";
                                return (
                                    <div
                                        key={tmdbId}
                                        className={`absolute w-2 h-2 ${color} opacity-70`}
                                        style={{ left: `${x}%`, top: `${y}%` }}
                                        title={movie.title}
                                    />
                                );
                            })}
                        </div>
                    </div>
                )}
            </main>

            {/* Search modal */}
            <AnimatePresence>
                {searchOpen && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.15 }}
                        className="fixed inset-0 bg-background/90 z-50 flex items-start justify-center pt-20 px-4"
                        onClick={closeSearch}
                    >
                        <div
                            className="w-full max-w-lg border border-border bg-background"
                            onClick={(e) => e.stopPropagation()}
                        >
                            <div className="flex items-center border-b border-border px-4 py-3">
                                <span className="text-zinc-500 font-mono text-xs mr-3">SEARCH</span>
                                <input
                                    autoFocus
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    placeholder="Type a film title..."
                                    className="flex-1 bg-transparent font-mono text-sm text-foreground placeholder:text-zinc-600 outline-none"
                                />
                                <button
                                    onClick={closeSearch}
                                    className="text-zinc-500 hover:text-foreground font-mono text-xs ml-3"
                                >
                                    ESC
                                </button>
                            </div>

                            <div className="max-h-96 overflow-y-auto">
                                {searchLoading && (
                                    <div className="p-4 font-mono text-xs text-zinc-500 animate-pulse">
                                        SEARCHING...
                                    </div>
                                )}
                                {!searchLoading && searchResults.map((movie) => (
                                    <div
                                        key={movie.tmdb_id}
                                        className="border-b border-border last:border-0 p-3 flex gap-3"
                                    >
                                        {movie.poster_path && (
                                            // eslint-disable-next-line @next/next/no-img-element
                                            <img
                                                src={getTMDBImageUrl(movie.poster_path, "w92")}
                                                alt={movie.title}
                                                className="w-10 h-14 object-cover flex-shrink-0 grayscale"
                                            />
                                        )}
                                        <div className="flex-1 min-w-0">
                                            <div className="font-mono text-xs text-foreground truncate">
                                                {movie.title}
                                            </div>
                                            <div className="font-mono text-[10px] text-zinc-500">
                                                {movie.year}
                                                {movie.genres && movie.genres.length > 0 && (
                                                    <> · {movie.genres.slice(0, 2).join("/")}</>
                                                )}
                                            </div>
                                        </div>
                                        <div className="flex gap-1 flex-shrink-0 self-center">
                                            {([
                                                { signal: "negative" as Signal, label: "✕", cls: "hover:border-red-500 hover:text-red-500" },
                                                { signal: "neutral" as Signal,  label: "~", cls: "hover:border-zinc-400 hover:text-zinc-400" },
                                                { signal: "positive" as Signal, label: "♥", cls: "hover:border-primary hover:text-primary" },
                                            ]).map(({ signal, label, cls }) => {
                                                const active = ratings[movie.tmdb_id] === signal;
                                                return (
                                                    <button
                                                        key={signal}
                                                        onClick={() => handleSearchRate(movie, signal)}
                                                        className={`border w-7 h-7 font-mono text-xs flex items-center justify-center transition-colors ${
                                                            active
                                                                ? "border-primary text-primary"
                                                                : `border-border text-zinc-600 ${cls}`
                                                        }`}
                                                    >
                                                        {label}
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    </div>
                                ))}
                                {!searchLoading && searchQuery.trim().length >= 2 && searchResults.length === 0 && (
                                    <div className="p-4 font-mono text-xs text-zinc-600">
                                        NO RESULTS FOR &quot;{searchQuery.toUpperCase()}&quot;
                                    </div>
                                )}
                                {!searchLoading && searchQuery.trim().length < 2 && (
                                    <div className="p-4 font-mono text-[10px] text-zinc-700 uppercase tracking-wider">
                                        Type at least 2 characters
                                    </div>
                                )}
                            </div>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
