"use client";

// Guest rating carousel — handoff `gonb` layout (two-column: rate card + aside
// "your taste, taking shape"). ALL logic/endpoints are unchanged from the
// pre-reskin version: init-session, status, movies paging, rate, search,
// keyboard 1/2/3/space/←//, 15-unlock destinations.

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Wordmark } from "@/components/ui/wordmark";
import { useRouter } from "next/navigation";
import { useAuth } from "@clerk/nextjs";
import { m, AnimatePresence } from "framer-motion";
import Image from "next/image";
import Link from "next/link";
import { Star, X } from "lucide-react";
import { getTMDBImageUrl, api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useLanguage } from "@/components/language-provider";

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

type Signal = "favorite" | "positive" | "neutral" | "negative";

// Button config in spec wireframe order (left → right): NOT FOR ME, IT WAS OK, LOVED IT.
// Keyboard 1/2/3 map left → right so the digit on the badge always matches the key.
// Rate buttons — 4-signal scale: left-aligned, bordered number box, 3px colored
// left edge (red/gray/lime/lime+★), hover-lit. "loved" is the favorite (5★, ★).
const SIGNAL_BUTTONS: {
    signal: Signal;
    key: "1" | "2" | "3" | "4";
    /** Mobile glyph — no keyboard at <md, so the key square shows this instead. */
    icon: string;
    label: string;
    sub: string;
    border: string;
    hover: string;
    fav?: boolean;
}[] = [
    { signal: "negative", key: "1", icon: "✕", label: "gonb.not_for_me", sub: "gonb.not_for_me_sub", border: "border-l-danger", hover: "hover:border-danger hover:bg-danger/[0.08]" },
    { signal: "neutral", key: "2", icon: "~", label: "gonb.ok", sub: "gonb.ok_sub", border: "border-l-fg-2", hover: "hover:border-fg-2 hover:bg-fg/[0.04]" },
    { signal: "positive", key: "3", icon: "★", label: "gonb.liked", sub: "gonb.liked_sub", border: "border-l-[#5cdb95]", hover: "hover:border-[#5cdb95] hover:bg-[#5cdb95]/[0.08]" },
    { signal: "favorite", key: "4", icon: "♥", label: "gonb.loved", sub: "gonb.loved_sub", border: "border-l-primary", hover: "hover:border-primary hover:bg-primary/[0.08]", fav: true },
];

// Progressive "signal so far" reveal thresholds (handoff aside).
const SIGNAL_REVEALS = [3, 5, 7, 10, 13];

export default function OnboardingCarouselPage() {
    const { t } = useLanguage();
    const router = useRouter();
    const { isSignedIn } = useAuth();

    const [movies, setMovies] = useState<OnboardingMovie[]>([]);
    const [currentIndex, setCurrentIndex] = useState(0);
    const [ratings, setRatings] = useState<Record<number, Signal>>({});
    const [ratedCount, setRatedCount] = useState(0);
    const [loading, setLoading] = useState(true);
    // Guests land on a start-selection (explore the feed / rate films) before the
    // carousel; picking "rate" sets vb_onb_rate and routes via the avoid-tags form.
    // null = undetermined (brief), avoids a carousel flash before the check.
    const [showSelection, setShowSelection] = useState<boolean | null>(null);
    useEffect(() => {
        if (isSignedIn === undefined) return;
        setShowSelection(!isSignedIn && localStorage.getItem("vb_onb_rate") !== "1");
    }, [isSignedIn]);
    const [showPeek, setShowPeek] = useState(false);
    const [showRegistration, setShowRegistration] = useState(false);
    const [direction, setDirection] = useState(1);
    const [searchOpen, setSearchOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState("");
    const [searchResults, setSearchResults] = useState<OnboardingMovie[]>([]);
    const [searchLoading, setSearchLoading] = useState(false);
    const [isLoadingMore, setIsLoadingMore] = useState(false);
    const [page, setPage] = useState(1);
    const [sessionReady, setSessionReady] = useState(false);
    const isMounted = useRef(true);

    useEffect(() => {
        return () => { isMounted.current = false; };
    }, []);

    // Mount: init anonymous session + fetch movies from API
    useEffect(() => {
        const hydrate = async () => {
            try {
                // Init or resume anonymous session (sets httponly cookie) for
                // GUESTS. For Clerk-authed users the backend now short-circuits
                // and returns their real identity WITHOUT creating an anon row
                // or cookie — so we always re-query /status below for the
                // authoritative rating count (works for both anon and authed).
                await api.post("/api/onboarding/init-session");
                if (!isMounted.current) return;

                // Authoritative rating count regardless of auth state. Without
                // this, an authed user coming from "Rate more films" sees a
                // ratedCount of 0 and the VIEW FEED button stays hidden until
                // they vote 15 NEW times.
                try {
                    const { data: statusData } = await api.get("/api/onboarding/status");
                    if (isMounted.current) setRatedCount(statusData.ratings_count || 0);
                } catch {
                    // Non-fatal — leave at 0; user can still rate.
                }
                setSessionReady(true);

                // Fetch carousel movies
                const { data } = await api.get("/api/onboarding/movies");
                if (!isMounted.current) return;
                setMovies(data as OnboardingMovie[]);
            } catch (e) {
                console.error("Failed to load onboarding movies:", e);
            } finally {
                if (isMounted.current) setLoading(false);
            }
        };
        hydrate();
    }, []);

    // Save a single rating to DB via API
    const saveRating = useCallback(async (tmdbId: number, signal: Signal) => {
        try {
            await api.post("/api/onboarding/rate", { tmdb_id: tmdbId, signal });
        } catch (e) {
            console.error("Failed to save rating:", e);
        }
    }, []);

    // Rate handler - saves to DB via API
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
            saveRating(movie.tmdb_id, signal);

            if (newCount === 10) setShowPeek(true);
            if (newCount >= 15 && !showRegistration) setShowRegistration(true);
        },
        [currentIndex, movies, ratings, ratedCount, saveRating, showRegistration]
    );

    // Skip - no API call needed, just advance the carousel
    const handleSkip = useCallback(() => {
        setDirection(1);
        // Functional update so the callback can be safely fired in quick
        // succession (keyboard "Space" auto-repeat) without stale closures.
        setCurrentIndex((prev) => (prev >= movies.length ? prev : prev + 1));
    }, [movies.length]);

    // Undo - client-side only (rating already sent; re-rating will overwrite on next swipe)
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
    }, [currentIndex, movies, ratings, ratedCount]);

    const loadMoreMovies = useCallback(async () => {
        setIsLoadingMore(true);
        const nextPage = page + 1;
        const shownIds = movies.map(m => m.tmdb_id).join(",");
        const savedTags = localStorage.getItem("vb_guest_tags");
        const avoided = savedTags ? JSON.parse(savedTags).avoided || [] : [];

        try {
            // Use the `api` client (axios) so AuthBridge attaches the Clerk
            // JWT — plain `fetch()` bypassed it and the request hung waiting
            // for a response the dev proxy never resolved.
            const { data: newMovies } = await api.get<OnboardingMovie[]>(
                "/api/onboarding/movies",
                {
                    params: {
                        page: nextPage,
                        exclude_ids: shownIds,
                        ...(avoided.length > 0 ? { avoided_tags: avoided.join(",") } : {}),
                    },
                },
            );
            setMovies(prev => [...prev, ...newMovies]);
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
            // Save rating to DB - don't add to carousel pool or advance the deck.
            const newRatings = { ...ratings, [movie.tmdb_id]: signal };
            const newCount = Object.keys(newRatings).length;
            setRatings(newRatings);
            setRatedCount(newCount);
            saveRating(movie.tmdb_id, signal);
            if (newCount === 10) setShowPeek(true);
            if (newCount >= 15 && !showRegistration) setShowRegistration(true);
            closeSearch();
        },
        [ratings, currentIndex, showRegistration, saveRating, closeSearch]
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
                const { data } = await api.get<OnboardingMovie[]>(
                    "/api/onboarding/search",
                    { params: { q: searchQuery } },
                );
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
    const handlersRef = useRef({ handleRate, handleSkip, handleUndo, openSearch, closeSearch, searchOpen, showSelection });
    useEffect(() => {
        handlersRef.current = { handleRate, handleSkip, handleUndo, openSearch, closeSearch, searchOpen, showSelection };
    });
    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            const target = e.target;
            const inField = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
            const h = handlersRef.current;
            // On the start-selection screen the carousel isn't shown — don't let a
            // stray 1/2/3 rate a background film.
            if (h.showSelection) return;
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
                case "4": h.handleRate("favorite"); break;
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

    // "Signal so far" — top genres across rated films, revealed progressively
    // at 3/5/7/10/13 ratings (handoff aside).
    const signalChips = useMemo(() => {
        const counts: Record<string, number> = {};
        for (const [idStr, signal] of Object.entries(ratings)) {
            if (signal === "negative") continue;
            const mv = movies.find((mm) => mm.tmdb_id === Number(idStr));
            for (const g of mv?.genres ?? []) counts[g] = (counts[g] || 0) + 1;
        }
        const revealed = SIGNAL_REVEALS.filter((t) => ratedCount >= t).length;
        return Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .slice(0, revealed)
            .map(([g]) => g.toLowerCase());
    }, [ratings, movies, ratedCount]);

    if (loading || showSelection === null) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-bg">
                <div className="space-y-4 text-center">
                    <div className="mx-auto size-8 animate-spin border-2 border-primary border-t-transparent" />
                    <p className="font-mono text-xs uppercase tracking-widest text-fg-3">Loading films…</p>
                </div>
            </div>
        );
    }

    // Guest start-selection — explore the feed, or rate films (→ avoid-tags → carousel).
    if (showSelection) {
        return (
            <div className="flex min-h-screen flex-col items-center justify-center bg-bg p-4 text-fg">
                <div className="w-full max-w-lg space-y-6">
                    <div>
                        <p className="eyebrow mb-2 text-primary">{t("gonb.sel_eyebrow")}</p>
                        <h1 className="font-display text-3xl uppercase tracking-[-0.02em] md:text-4xl">{t("gonb.sel_title")}</h1>
                    </div>
                    <button
                        onClick={() => router.push("/explore")}
                        className="group block w-full border border-border-2 bg-bg-2 p-5 text-left transition-colors hover:border-primary"
                    >
                        <div className="flex items-center justify-between">
                            <span className="font-display text-lg uppercase tracking-tight text-fg">{t("gonb.sel_feed")}</span>
                            <span className="font-display text-lg text-fg-3 transition-colors group-hover:text-primary">→</span>
                        </div>
                        <p className="mt-1.5 font-mono text-[11px] leading-relaxed text-fg-3">{t("gonb.sel_feed_sub")}</p>
                    </button>
                    <button
                        onClick={() => { localStorage.setItem("vb_onb_rate", "1"); router.push("/onboarding/tags"); }}
                        className="group block w-full border-2 border-primary bg-bg-2 p-5 text-left shadow-acid transition-transform hover:-translate-x-px hover:-translate-y-px"
                    >
                        <div className="flex items-center justify-between">
                            <span className="font-display text-lg uppercase tracking-tight text-primary">{t("gonb.sel_rate")}</span>
                            <span className="font-display text-lg text-primary">→</span>
                        </div>
                        <p className="mt-1.5 font-mono text-[11px] leading-relaxed text-fg-2">{t("gonb.sel_rate_sub")}</p>
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="relative min-h-screen bg-bg text-fg">
            {/* Header */}
            <header className="relative z-20 border-b border-border-2 px-4 py-3">
                <div className="mx-auto flex max-w-7xl items-center justify-between">
                    <div className="flex items-baseline gap-3">
                        {/* wordmark → landing (escape hatch without browser-back) */}
                        <h1 className="font-display text-lg uppercase tracking-tight">
                            <Link href="/">
                                <Wordmark />
                            </Link>
                        </h1>
                        {/* Signed-in users ("rate more films") get NO cold-start chrome —
                            no "cold-start" tag, no x/15 counter, no unlock/fork; just the
                            rating card + a back-to-feed affordance. */}
                        <span className="hidden font-mono text-[9px] uppercase tracking-[0.18em] text-fg-3 sm:inline">
                            {isSignedIn ? t("gonb.session") : `${t("gonb.guest_session")} · ${t("gonb.coldstart")}`}
                        </span>
                    </div>
                    <div className="flex items-center gap-3">
                        {isSignedIn ? (
                            <>
                                {/* La otra vía de arranque: el ZIP/RSS de Letterboxd. Sin esto
                                    un registrado que aterriza aquí no tiene forma de llegar
                                    al wizard sin pasar por ajustes. */}
                                <Link
                                    href="/import"
                                    className="border border-border-2 px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-fg-2 transition-colors hover:border-primary hover:text-primary"
                                >
                                    {t("gonb.import_lb")}
                                </Link>
                                <button
                                    onClick={() => router.push("/?onboarding_complete=true")}
                                    className="border border-border-2 px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-fg-2 transition-colors hover:border-primary hover:text-primary"
                                >
                                    {t("gonb.back_to_feed")}
                                </button>
                            </>
                        ) : (
                            <>
                                <span className={cn(
                                    "font-mono text-[10px] uppercase text-fg-3",
                                    // At 15 the two CTA buttons appear — the counter overflows 390px, hide it there.
                                    ratedCount >= 15 && "hidden sm:inline"
                                )}>
                                    {t("gonb.film")} {currentIndex + 1} · {t("gonb.rated")} <span className="text-primary">{Math.min(ratedCount, 15)}</span> / 15
                                </span>
                                {ratedCount >= 15 && (
                                    <>
                                        {/* Guest came from /explore via "Rate more films" — bounce back
                                            there with cache invalidation so the new ratings show up. */}
                                        <button
                                            onClick={() => router.push("/explore?onboarding_complete=true")}
                                            className="border border-primary px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-wider text-primary transition-colors hover:bg-primary hover:text-primary-ink"
                                        >
                                            {t("gonb.view_explore")}
                                        </button>
                                        <button
                                            onClick={handleSaveProfile}
                                            className="bg-primary px-3 py-1.5 font-display text-[10px] font-bold uppercase tracking-wider text-primary-ink"
                                        >
                                            {t("gonb.save_profile")}
                                        </button>
                                    </>
                                )}
                            </>
                        )}
                    </div>
                </div>
            </header>

            {/* Main content — gonb two-column */}
            <main className="relative z-10 mx-auto grid max-w-7xl grid-cols-1 gap-8 px-4 py-4 md:py-8 lg:grid-cols-[1fr_340px]">
                <div>
                    {/* Mobile taste strip — the aside collapses into this at <lg:
                        unlock bar (guests) + the strongest signal chips so far. */}
                    <div className="mb-4 space-y-2.5 lg:hidden">
                        {!isSignedIn && (
                            <div>
                                <div className="mb-1 flex items-baseline justify-between font-display text-[9px] uppercase tracking-[0.15em] text-fg-3">
                                    <span>{t("gonb.start")}</span>
                                    <span className="text-primary">{t("gonb.unlock_15")}</span>
                                </div>
                                <div className="h-1.5 border border-border-2 bg-bg-3">
                                    <div className="h-full bg-primary transition-[width] duration-300" style={{ width: `${progress}%` }} />
                                </div>
                            </div>
                        )}
                        {signalChips.length > 0 && (
                            <div className="flex flex-wrap gap-1.5">
                                {signalChips.slice(0, 4).map((g) => (
                                    <span key={g} className="chip accent">{g}</span>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* inline search primer (gonb-primer) — opens the search modal */}
                    <button
                        onClick={openSearch}
                        className="mb-3.5 flex w-full items-center gap-3 border border-border-2 bg-bg-2 px-4 py-2.5 font-mono text-xs text-fg-3 transition-colors hover:border-primary md:mb-5 md:py-3"
                    >
                        <span className="text-primary">⌕</span>
                        <span className="flex-1 truncate text-left">{t("gonb.primer")}</span>
                        <kbd className="border border-border-2 bg-bg-3 px-1.5 py-0.5 font-display text-[9px] text-fg-2">/</kbd>
                    </button>
                    {currentMovie ? (
                        <>
                        {/* bordered card: poster + info (gonb-card). Mobile is a compact
                            horizontal row (small poster · info) so the whole rating loop
                            fits one viewport without scrolling. */}
                        <div className="flex flex-row gap-3.5 border border-border-2 bg-bg-2 p-3.5 md:gap-6 md:p-5">
                            {/* Poster */}
                            <div className="w-[104px] shrink-0 md:w-[180px]">
                                <AnimatePresence mode="wait" custom={direction}>
                                    <m.div
                                        key={currentMovie.tmdb_id}
                                        custom={direction}
                                        initial={{ opacity: 0, x: direction * 60 }}
                                        animate={{ opacity: 1, x: 0 }}
                                        exit={{ opacity: 0, x: direction * -60 }}
                                        transition={{ duration: 0.25 }}
                                        className="poster-art relative aspect-[2/3] w-full overflow-hidden border border-border-2"
                                    >
                                        {currentMovie.poster_path ? (
                                            <Image
                                                src={getTMDBImageUrl(currentMovie.poster_path, "w500")}
                                                alt={currentMovie.title}
                                                fill
                                                sizes="(max-width: 768px) 104px, 180px"
                                                className="object-cover"
                                                priority
                                            />
                                        ) : (
                                            <div className="flex size-full items-center justify-center font-display text-3xl tracking-[0.1em] text-fg/5">
                                                VBX
                                            </div>
                                        )}
                                        {currentMovie.vectorbox_score ? (
                                            <span className="absolute right-0 top-0 bg-primary px-[7px] py-[3px] font-display text-[11px] font-bold leading-none text-primary-ink">
                                                Q{Math.round(currentMovie.vectorbox_score)}
                                            </span>
                                        ) : null}
                                    </m.div>
                                </AnimatePresence>
                            </div>

                            {/* Info + Actions */}
                            <div className="min-w-0 flex-1">
                                <AnimatePresence mode="wait">
                                    <m.div
                                        key={currentMovie.tmdb_id}
                                        initial={{ opacity: 0, y: 10 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={{ opacity: 0, y: -10 }}
                                        transition={{ duration: 0.2 }}
                                        className="space-y-2 md:space-y-3.5"
                                    >
                                        <div>
                                            <h2 className="font-display text-lg uppercase leading-tight tracking-[-0.01em] text-fg md:text-3xl">
                                                {currentMovie.title}
                                            </h2>
                                            <div className="mt-1.5 flex items-center gap-3 font-mono text-xs text-fg-3 md:mt-2">
                                                {currentMovie.year && <span>{currentMovie.year}</span>}
                                                {currentMovie.runtime ? (
                                                    <span>{Math.floor(currentMovie.runtime / 60)}H{String(currentMovie.runtime % 60).padStart(2, "0")}</span>
                                                ) : null}
                                                {currentMovie.vote_average && (
                                                    <span className="flex items-center gap-1 text-fg-2">
                                                        <Star className="size-3 fill-current" />
                                                        {currentMovie.vote_average.toFixed(1)}
                                                    </span>
                                                )}
                                            </div>
                                        </div>

                                        {currentMovie.genres && currentMovie.genres.length > 0 && (
                                            <div className="flex flex-wrap gap-1.5">
                                                {currentMovie.genres.map((g) => (
                                                    <span key={g} className="chip">{g}</span>
                                                ))}
                                            </div>
                                        )}

                                        {currentMovie.overview && (
                                            <p className="line-clamp-2 font-mono text-xs leading-relaxed text-fg-2 md:line-clamp-4 md:text-sm">
                                                {currentMovie.overview}
                                            </p>
                                        )}
                                    </m.div>
                                </AnimatePresence>
                            </div>
                        </div>

                        {/* Rating + skip + keyboard — full-width below the card (gonb) */}
                        <div className="mt-4 space-y-3">
                                    <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
                                        {SIGNAL_BUTTONS.map(({ signal, key, icon, label, sub, border, hover, fav }) => (
                                            <button
                                                key={signal}
                                                onClick={() => handleRate(signal)}
                                                className={cn(
                                                    // Mobile: compact inline icon+label row (no sub) so all 4 buttons
                                                    // + the card fit one viewport; desktop keeps the tall gonb layout.
                                                    "flex flex-row items-center gap-2 border border-border-2 border-l-[3px] px-3 py-2.5 text-left font-mono transition-colors md:flex-col md:items-start md:gap-2.5 md:px-4 md:py-5",
                                                    border,
                                                    hover,
                                                    fav && "bg-primary/[0.05]"
                                                )}
                                            >
                                                {/* No keyboard at <md — show the signal glyph instead of the key number. */}
                                                <span className="flex size-6 shrink-0 items-center justify-center border border-border-2 font-display text-base leading-none text-fg-3 md:size-8 md:text-2xl">
                                                    <span className="md:hidden">{icon}</span>
                                                    <span className="hidden md:inline">{key}</span>
                                                </span>
                                                <span className="block text-xs leading-none text-fg md:text-sm">
                                                    {t(label)}{fav && <span className="text-primary"> ★</span>}
                                                </span>
                                                <span className="hidden text-[10px] text-fg-3 md:block">{t(sub)}</span>
                                            </button>
                                        ))}
                                    </div>

                                    {/* Skip / undo / search row */}
                                    <div className="mt-2 flex items-center justify-between gap-2">
                                        <button
                                            onClick={handleSkip}
                                            className="flex items-center gap-2 border border-dashed border-border-2 px-4 py-2 font-mono text-xs text-fg-3 transition-colors hover:border-fg-3 hover:text-fg-2"
                                        >
                                            {t("gonb.havent_seen")}
                                            <span className="text-[9px] uppercase opacity-60">{t("gonb.doesnt_count")}</span>
                                        </button>

                                        <div className="flex gap-2">
                                            <button
                                                onClick={handleUndo}
                                                disabled={currentIndex <= 0}
                                                className="flex items-center gap-1 border border-border-2 px-3 py-2 font-mono text-xs text-fg-3 transition-colors hover:border-fg-3 hover:text-fg-2 disabled:cursor-not-allowed disabled:opacity-30"
                                            >
                                                {t("gonb.undo")}
                                            </button>
                                            <button
                                                onClick={openSearch}
                                                className="flex items-center gap-1 border border-border-2 px-3 py-2 font-mono text-xs text-fg-3 transition-colors hover:border-fg-3 hover:text-fg-2"
                                            >
                                                <span className="text-[10px] opacity-60">/</span> {t("gonb.search")}
                                            </button>
                                        </div>
                                    </div>

                                    {isLoadingMore && currentIndex >= movies.length - 1 && (
                                        <div className="mt-4 animate-pulse text-center font-mono text-xs text-fg-3">
                                            {t("gonb.loading_more")}
                                        </div>
                                    )}

                                    {/* Keyboard legend */}
                                    <div className="hidden items-center justify-center gap-4 font-mono text-[9px] uppercase text-fg-3 md:flex">
                                        <span>1 · {t("gonb.not_for_me")}</span>
                                        <span>2 · {t("gonb.ok")}</span>
                                        <span>3 · {t("gonb.liked")}</span>
                                        <span>4 · {t("gonb.loved")}</span>
                                        <span>{t("gonb.kbd_space_skip")}</span>
                                        <span>{t("gonb.kbd_undo")}</span>
                                        <span>{t("gonb.kbd_search")}</span>
                                    </div>
                                </div>
                        </>
                    ) : (
                        /* The "all films rated" state should only show if we truly ran out
                           of movies AND the user has rated them all (edge case, very rare) */
                        <div className="space-y-6 py-20 text-center">
                            {ratedCount < 15 ? (
                                <div className="animate-pulse font-mono text-sm text-fg-3">{t("gonb.loading_more")}</div>
                            ) : (
                                <>
                                    <p className="font-display text-4xl text-primary">✓</p>
                                    <h2 className="font-display text-2xl uppercase tracking-tight">
                                        {t("gonb.all_rated_1")} <span className="text-primary">{t("gonb.all_rated_2")}</span>
                                    </h2>
                                    <p className="font-mono text-sm text-fg-3">
                                        {t("gonb.profile_ready_note")}
                                    </p>
                                    <button
                                        onClick={handleSaveProfile}
                                        className="bg-primary px-8 py-3 font-display text-sm font-bold uppercase tracking-wider text-primary-ink"
                                    >
                                        {t("gonb.save_profile")}
                                    </button>
                                </>
                            )}
                        </div>
                    )}

                    {/* Peek banner at 10 ratings — cold-start only */}
                    <AnimatePresence>
                        {!isSignedIn && showPeek && ratedCount >= 10 && ratedCount < 15 && (
                            <m.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -10 }}
                                className="mt-8 border border-primary/40 bg-bg-2 p-4"
                            >
                                <div className="flex items-center justify-between">
                                    <div className="space-y-1">
                                        <p className="font-display text-xs uppercase tracking-wider text-primary">
                                            {t("gonb.peek_title")}
                                        </p>
                                        <p className="font-mono text-[10px] text-fg-3">
                                            {t("gonb.peek_note")}
                                        </p>
                                    </div>
                                    <button
                                        onClick={() => setShowPeek(false)}
                                        className="font-mono text-[10px] uppercase text-fg-3 transition-colors hover:text-fg-2"
                                    >
                                        {t("gonb.dismiss")}
                                    </button>
                                </div>
                            </m.div>
                        )}
                    </AnimatePresence>

                    {/* 15-unlock choice fork — guests only (authed users already have an account) */}
                    <AnimatePresence>
                        {!isSignedIn && showRegistration && ratedCount >= 15 && currentMovie && (
                            <m.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -10 }}
                                className="relative mt-8 border-2 border-primary bg-bg-2 p-5 shadow-acid-primary"
                            >
                                <button
                                    onClick={() => setShowRegistration(false)}
                                    aria-label="Dismiss"
                                    className="absolute right-3 top-3 p-1 text-fg-3 transition-colors hover:text-fg"
                                >
                                    <X className="size-4" />
                                </button>
                                <p className="eyebrow mb-1 text-primary">{t("gonb.fork_eyebrow")}</p>
                                <h3 className="font-display text-xl uppercase tracking-tight text-fg">
                                    {t("gonb.fork_title")}
                                </h3>
                                <p className="mt-1 font-mono text-[11px] text-fg-3">
                                    {ratedCount} {t("gonb.films_rated_choose")}
                                </p>
                                <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
                                    <button
                                        onClick={() => router.push("/register")}
                                        className="bg-primary px-4 py-2.5 text-left font-display text-[11px] font-bold uppercase tracking-[0.06em] text-primary-ink"
                                    >
                                        {t("gonb.fork_create")}
                                    </button>
                                    <button
                                        onClick={handleSaveProfile}
                                        className="border border-fg px-4 py-2.5 text-left font-display text-[11px] font-bold uppercase tracking-[0.06em] text-fg transition-colors hover:border-primary hover:text-primary"
                                    >
                                        {t("gonb.fork_signin")}
                                    </button>
                                    <button
                                        onClick={() => router.push("/explore?onboarding_complete=true")}
                                        className="border border-border-2 px-4 py-2.5 text-left font-mono text-[11px] uppercase tracking-[0.06em] text-fg-2 transition-colors hover:border-fg-3 hover:text-fg"
                                    >
                                        {t("gonb.fork_guest")}
                                    </button>
                                    <button
                                        onClick={() => setShowRegistration(false)}
                                        className="border border-dashed border-border-2 px-4 py-2.5 text-left font-mono text-[11px] uppercase tracking-[0.06em] text-fg-3 transition-colors hover:text-fg-2"
                                    >
                                        {t("gonb.fork_keep")}
                                    </button>
                                </div>
                                <p className="mt-3 font-mono text-[10px] text-fg-3">
                                    {t("gonb.fork_note")}
                                </p>
                            </m.div>
                        )}
                    </AnimatePresence>
                </div>

                {/* ASIDE — your taste, taking shape. Desktop-only: on mobile it collapsed
                    below the fold, so it becomes the slim strip above the card instead. */}
                <aside className="hidden space-y-4 lg:block lg:pt-1">
                    <div className="eyebrow text-primary">{t("gonb.aside_title")}</div>

                    {/* taste-map constellation (decorative — real placement needs vectors) */}
                    <div className="border border-border-2 bg-bg-2 p-3">
                        <div className="relative h-36 overflow-hidden">
                            {Object.entries(ratings).map(([tmdbId, signal]) => {
                                const id = parseInt(tmdbId);
                                const movie = movies.find((mm) => mm.tmdb_id === id);
                                if (!movie) return null;
                                const x = ((id % 97) / 97) * 90 + 5;
                                const y = ((id % 53) / 53) * 80 + 10;
                                return (
                                    <div
                                        key={tmdbId}
                                        className={cn(
                                            "absolute size-2",
                                            (signal === "positive" || signal === "favorite") && "bg-primary",
                                            signal === "negative" && "border border-danger",
                                            signal === "neutral" && "bg-fg-3"
                                        )}
                                        style={{ left: `${x}%`, top: `${y}%` }}
                                        title={movie.title}
                                    />
                                );
                            })}
                            {ratedCount === 0 && (
                                <div className="flex h-full items-center justify-center font-mono text-[10px] uppercase tracking-widest text-fg-3">
                                    {t("gonb.rate_to_place")}
                                </div>
                            )}
                        </div>
                        <div className="mt-2 flex gap-3 border-t border-border pt-2 font-mono text-[9px] uppercase tracking-[0.08em] text-fg-3">
                            <span className="flex items-center gap-1"><span className="size-1.5 bg-primary" /> {t("gonb.legend_loved")}</span>
                            <span className="flex items-center gap-1"><span className="size-1.5 bg-fg-3" /> {t("gonb.legend_ok")}</span>
                            <span className="flex items-center gap-1"><span className="size-1.5 border border-danger" /> {t("gonb.not_for_me")}</span>
                        </div>
                    </div>

                    {/* signal so far — progressive genre chips */}
                    <div className="border border-border-2 bg-bg-2 p-3">
                        <div className="eyebrow mb-2">{t("gonb.signal_so_far")}</div>
                        {signalChips.length > 0 ? (
                            <div className="flex flex-wrap gap-1.5">
                                {signalChips.map((g) => (
                                    <span key={g} className="chip accent">{g}</span>
                                ))}
                            </div>
                        ) : (
                            <p className="font-mono text-[10px] text-fg-3">
                                {t("gonb.reveal_pre")} {Math.max(0, SIGNAL_REVEALS[0] - ratedCount)} {t("gonb.reveal_post")}
                            </p>
                        )}
                    </div>

                    {/* unlock progress — cold-start only (hidden for signed-in "rate more") */}
                    {!isSignedIn && (
                        <div className="border border-border-2 bg-bg-2 p-3">
                            <div className="mb-1.5 flex items-baseline justify-between font-display text-[9px] uppercase tracking-[0.15em] text-fg-3">
                                <span>{t("gonb.start")}</span>
                                <span className="text-primary">{t("gonb.unlock_15")}</span>
                            </div>
                            <div className="h-1.5 border border-border-2 bg-bg-3">
                                <m.div
                                    className="h-full bg-primary"
                                    initial={{ width: 0 }}
                                    animate={{ width: `${progress}%` }}
                                    transition={{ duration: 0.3 }}
                                />
                            </div>
                            <p className="mt-2 font-mono text-[9px] uppercase tracking-[0.08em] text-fg-3">
                                {t("gonb.skips_note")}
                            </p>
                        </div>
                    )}
                </aside>
            </main>

            {/* Search modal */}
            <AnimatePresence>
                {searchOpen && (
                    <m.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.15 }}
                        className="fixed inset-0 z-50 flex items-start justify-center bg-bg/90 px-4 pt-20"
                        onClick={closeSearch}
                        onKeyDown={(e) => { if (e.key === 'Escape' || e.key === 'Enter') closeSearch(); }}
                        tabIndex={0}
                        role="button"
                    >
                        <div
                            className="w-full max-w-lg border-2 border-primary bg-bg"
                            onClick={(e) => e.stopPropagation()}
                            onKeyDown={(e) => e.stopPropagation()}
                            role="dialog"
                            aria-modal="true"
                        >
                            <div className="flex items-center border-b border-border-2 px-4 py-3">
                                <span className="mr-3 font-display text-xs text-primary">⌕</span>
                                <input
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    placeholder={t("gonb.search_ph")}
                                    className="flex-1 bg-transparent font-mono text-sm text-fg outline-none placeholder:text-fg-3"
                                />
                                <button
                                    onClick={closeSearch}
                                    className="ml-3 border border-border-2 bg-bg-3 px-1.5 py-0.5 font-display text-[9px] text-fg-2"
                                >
                                    esc
                                </button>
                            </div>

                            <div className="max-h-96 overflow-y-auto">
                                {searchLoading && (
                                    <div className="animate-pulse p-4 font-mono text-xs text-fg-3">{t("gonb.searching")}</div>
                                )}
                                {!searchLoading && searchResults.map((movie) => (
                                    <div
                                        key={movie.tmdb_id}
                                        className="flex gap-3 border-b border-border p-3 last:border-0"
                                    >
                                        {movie.poster_path && (
                                            // eslint-disable-next-line @next/next/no-img-element
                                            <img
                                                src={getTMDBImageUrl(movie.poster_path, "w92")}
                                                alt={movie.title}
                                                className="h-14 w-10 flex-shrink-0 border border-border-2 object-cover"
                                            />
                                        )}
                                        <div className="min-w-0 flex-1">
                                            <div className="truncate font-mono text-xs text-fg">{movie.title}</div>
                                            <div className="font-mono text-[10px] text-fg-3">
                                                {movie.year}
                                                {movie.genres && movie.genres.length > 0 && (
                                                    <> · {movie.genres.slice(0, 2).join("/")}</>
                                                )}
                                            </div>
                                        </div>
                                        <div className="flex flex-shrink-0 gap-1 self-center">
                                            {([
                                                { signal: "negative" as Signal, label: "✕", cls: "hover:border-danger hover:text-danger" },
                                                { signal: "neutral" as Signal, label: "~", cls: "hover:border-fg-3 hover:text-fg-2" },
                                                { signal: "positive" as Signal, label: "★", cls: "hover:border-[#5cdb95] hover:text-[#5cdb95]" },
                                                { signal: "favorite" as Signal, label: "♥", cls: "hover:border-primary hover:text-primary" },
                                            ]).map(({ signal, label, cls }) => {
                                                const active = ratings[movie.tmdb_id] === signal;
                                                return (
                                                    <button
                                                        key={signal}
                                                        onClick={() => handleSearchRate(movie, signal)}
                                                        className={cn(
                                                            "flex size-7 items-center justify-center border font-mono text-xs transition-colors",
                                                            active ? "border-primary text-primary" : `border-border-2 text-fg-3 ${cls}`
                                                        )}
                                                    >
                                                        {label}
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    </div>
                                ))}
                                {!searchLoading && searchQuery.trim().length >= 2 && searchResults.length === 0 && (
                                    <div className="p-4 font-mono text-xs text-fg-3">
                                        {t("gonb.no_results_for")} &quot;{searchQuery.toUpperCase()}&quot;
                                    </div>
                                )}
                                {!searchLoading && searchQuery.trim().length < 2 && (
                                    <div className="p-4 font-mono text-[10px] uppercase tracking-wider text-fg-3">
                                        {t("gonb.min_chars")}
                                    </div>
                                )}
                            </div>
                        </div>
                    </m.div>
                )}
            </AnimatePresence>
        </div>
    );
}
