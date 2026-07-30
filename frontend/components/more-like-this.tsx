"use client";

// Similars / More Like This — handoff screens-v3/more-like-this.jsx.
// Top: 5-anchor slot strip + blended-centroid bar. Left: search + poster grid.
// Right: live recommendations (rank · poster · why · Q + d) → quick-look (mlt).

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Search, Loader2 } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import Image from "next/image";
import { getTMDBImageUrl, api } from "@/lib/api";
import { QuickLook, QuickLookFilm } from "@/components/quick-look";
import { useLanguage } from "@/components/language-provider";
import { cn } from "@/lib/utils";

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

interface SimilarRec {
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
    const { language, t } = useLanguage();
    // The landing's title field hands its text over here rather than dropping it
    // — you typed it once, so you should not type it twice.
    const handoff = useSearchParams().get("q") ?? "";
    const [searchQuery, setSearchQuery] = useState(handoff);
    const [searchResults, setSearchResults] = useState<SearchedMovie[]>([]);
    const [seeds, setSeeds] = useState<SearchedMovie[]>([]);
    const [recs, setRecs] = useState<SimilarRec[]>([]);
    const [quickLook, setQuickLook] = useState<QuickLookFilm | null>(null);

    const searchMutation = useMutation({
        mutationFn: async (query: string) => {
            const res = await api.get(`/api/search/autocomplete?q=${encodeURIComponent(query)}`);
            return res.data as SearchedMovie[];
        },
        onSuccess: (data) => {
            const seen = new Set(seeds.map((s) => s.tmdb_id));
            setSearchResults(data.filter((m) => !seen.has(m.tmdb_id)));
        },
    });

    const similarMutation = useMutation({
        mutationFn: async (tmdbIds: number[]) => {
            const res = await api.post("/api/recommendations/similar/multi", { tmdb_ids: tmdbIds, limit: 12 });
            return res.data;
        },
        onSuccess: (data) => {
            const map = new Map<number, SimilarRec>();
            (data.recommendations || []).forEach((r: SimilarRec) => {
                if (!map.has(r.movie_id)) map.set(r.movie_id, r);
            });
            setRecs(Array.from(map.values()));
        },
    });

    // Live: re-run the blend whenever the anchor set changes (debounced).
    useEffect(() => {
        if (seeds.length === 0) {
            setRecs([]);
            return;
        }
        const t = setTimeout(() => similarMutation.mutate(seeds.map((s) => s.tmdb_id)), 400);
        return () => clearTimeout(t);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [seeds]);

    // Run the handed-over text once on arrival. Prefilling the box without
    // searching would still make the visitor press enter on words they already
    // typed, which is the handoff failing quietly.
    useEffect(() => {
        if (handoff.trim()) searchMutation.mutate(handoff);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const handleSearch = (e: React.FormEvent) => {
        e.preventDefault();
        if (searchQuery.trim()) searchMutation.mutate(searchQuery);
    };
    const addSeed = (movie: SearchedMovie) => {
        if (seeds.length >= MAX_SEEDS || seeds.some((s) => s.tmdb_id === movie.tmdb_id)) return;
        setSeeds((prev) => [...prev, movie]);
        // Mobile (handoff mlt): picking an anchor clears the search so the
        // results collapse and the live recommendations are immediately
        // visible. Desktop keeps the results for multi-add from one search.
        if (typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches) {
            setSearchQuery("");
            setSearchResults([]);
        }
    };
    const removeSeed = (tmdbId: number) => setSeeds((prev) => prev.filter((s) => s.tmdb_id !== tmdbId));

    const blends = seeds.length >= 2;
    const slotsLeft = MAX_SEEDS - seeds.length;

    return (
        <div className="space-y-6 pt-6">
            <div>
                <h1 className="font-display text-2xl uppercase tracking-[-0.02em] text-fg">
                    {t("mlt2.anchors")} · {seeds.length}/{MAX_SEEDS}
                </h1>
                <p className="tiny mt-1">
                    {t("mlt2.hint")}{" "}
                    {slotsLeft === 0 ? t("mlt2.slots_full") : `${slotsLeft} ${t("mlt2.slots_open")}`}
                </p>
            </div>

            {/* anchor slot strip + blend bar */}
            <div className="flex flex-wrap gap-2.5 border border-border-2 bg-bg-2 p-3.5">
                {Array.from({ length: MAX_SEEDS }).map((_, slot) => {
                    const film = seeds[slot];
                    if (!film) {
                        return (
                            <div
                                key={slot}
                                className="flex h-[120px] w-20 items-center justify-center border-2 border-dashed border-border-2 font-display text-2xl text-fg-3"
                            >
                                {slot + 1}
                            </div>
                        );
                    }
                    return (
                        <button key={slot} onClick={() => removeSeed(film.tmdb_id)} className="relative text-left" title={`Remove ${film.title}`}>
                            <div className="poster-art relative h-[120px] w-20 border border-border-2">
                                {film.poster_path && (
                                    <Image src={getTMDBImageUrl(film.poster_path, "w154")} alt={film.title} fill sizes="80px" className="object-cover" />
                                )}
                                <span className="absolute left-0 top-0 bg-primary px-[5px] py-[2px] font-display text-[10px] font-bold text-primary-ink">
                                    {slot + 1}
                                </span>
                                <span className="absolute right-0 top-0 bg-black/85 px-[5px] py-[2px] font-display text-[10px] text-fg">×</span>
                            </div>
                            <div className="mt-1 w-20 truncate font-mono text-[10px] leading-tight text-fg-2">{film.title}</div>
                        </button>
                    );
                })}
                <div className="flex min-w-[180px] flex-1 flex-col justify-between px-2 py-1">
                    <div>
                        <div className={cn("font-display text-[10px] uppercase tracking-[0.15em]", blends ? "text-primary" : "text-fg-3")}>
                            {blends ? t("mlt2.blend") : t("mlt2.blend_hint")}
                        </div>
                        {blends && (
                            <>
                                <div className="mt-2 flex h-1.5 gap-px border border-border-2">
                                    {seeds.map((s) => (
                                        <div key={s.tmdb_id} className="flex-1" style={{ background: `hsl(${(s.tmdb_id * 47) % 360}, 60%, 55%)` }} />
                                    ))}
                                </div>
                                <div className="mt-1 font-mono text-[10px] text-fg-3">
                                    {t("mlt2.blend_meta")} {seeds.length} {t("mlt2.blend_meta2")}
                                </div>
                            </>
                        )}
                    </div>
                    <button
                        onClick={() => setSeeds([])}
                        disabled={seeds.length === 0}
                        className="self-end border border-border-2 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-fg-2 transition-colors hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:text-fg-3"
                    >
                        {t("mlt2.clear_all")}
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_1.4fr]">
                {/* LEFT — search + add grid */}
                <div>
                    <div className="eyebrow mb-2">{t("mlt2.add_films")}</div>
                    <form onSubmit={handleSearch} className="mb-2.5 flex items-center gap-2 border border-border-2 bg-bg-2 px-3 py-2">
                        <span className="text-primary">⌕</span>
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder={t("mlt2.search_ph")}
                            className="w-full bg-transparent font-mono text-[11px] text-fg placeholder:text-fg-3 focus:outline-none"
                        />
                        <button
                            type="submit"
                            disabled={searchMutation.isPending || !searchQuery.trim()}
                            aria-label="Search"
                            className="text-fg-3 transition-colors hover:text-primary disabled:opacity-40"
                        >
                            {searchMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
                        </button>
                    </form>

                    {searchResults.length > 0 ? (
                        <>
                            {/* Mobile (handoff mlt): compact "+ add" rows — short list, recs stay close */}
                            <div className="flex flex-col border border-border-2 bg-bg-2 lg:hidden">
                                {searchResults.slice(0, 6).map((movie, i) => {
                                    const isPicked = seeds.some((s) => s.tmdb_id === movie.tmdb_id);
                                    const disabled = !isPicked && seeds.length >= MAX_SEEDS;
                                    return (
                                        <button
                                            key={movie.tmdb_id}
                                            disabled={disabled}
                                            onClick={() => (isPicked ? removeSeed(movie.tmdb_id) : addSeed(movie))}
                                            className={cn(
                                                "flex items-center gap-3 px-3 py-2 text-left transition-colors",
                                                i > 0 && "border-t border-dashed border-border-2",
                                                disabled && "cursor-not-allowed opacity-40"
                                            )}
                                        >
                                            <div className="poster-art relative h-[54px] w-9 shrink-0 border border-border-2">
                                                {movie.poster_path && (
                                                    <Image src={getTMDBImageUrl(movie.poster_path, "w154")} alt={movie.title} fill sizes="36px" className="object-cover" />
                                                )}
                                            </div>
                                            <div className="min-w-0 flex-1">
                                                <div className={cn("truncate font-mono text-xs", isPicked ? "text-primary" : "text-fg")}>{movie.title}</div>
                                                <div className="font-mono text-[10px] text-fg-3">{movie.year}</div>
                                            </div>
                                            <span className="font-display text-lg text-primary">{isPicked ? "✓" : "+"}</span>
                                        </button>
                                    );
                                })}
                            </div>
                            {/* Desktop: poster grid (two-column layout, results don't block the recs) */}
                            <div className="hidden grid-cols-3 gap-2.5 sm:grid-cols-4 lg:grid">
                                {searchResults.slice(0, 8).map((movie) => {
                                    const isPicked = seeds.some((s) => s.tmdb_id === movie.tmdb_id);
                                    const disabled = !isPicked && seeds.length >= MAX_SEEDS;
                                    return (
                                        <button
                                            key={movie.tmdb_id}
                                            disabled={disabled}
                                            onClick={() => (isPicked ? removeSeed(movie.tmdb_id) : addSeed(movie))}
                                            className={cn("relative text-left", disabled && "cursor-not-allowed opacity-40")}
                                        >
                                            <div className="poster-art relative aspect-[2/3] w-full border border-border-2">
                                                {movie.poster_path && (
                                                    <Image src={getTMDBImageUrl(movie.poster_path, "w342")} alt={movie.title} fill sizes="120px" className="object-cover" />
                                                )}
                                                {isPicked && (
                                                    <div className="absolute inset-0 flex items-start justify-end border-[3px] border-primary bg-primary/10 p-1.5">
                                                        <span className="bg-primary px-1.5 py-0.5 font-display text-[11px] font-bold text-primary-ink">✓</span>
                                                    </div>
                                                )}
                                            </div>
                                            <div className={cn("mt-1 truncate font-mono text-[10px] leading-tight", isPicked ? "text-primary" : "text-fg-2")}>
                                                {movie.title}
                                            </div>
                                            <div className="font-mono text-[9px] text-fg-3">{movie.year}</div>
                                        </button>
                                    );
                                })}
                            </div>
                        </>
                    ) : (
                        <div className="border border-dashed border-border-2 bg-bg-2 p-8 text-center font-mono text-[11px] text-fg-3">
                            {t("mlt2.empty")}
                        </div>
                    )}
                </div>

                {/* RIGHT — live recommendations */}
                <div>
                    <div className="mb-2 flex items-baseline justify-between">
                        <span className="font-display text-[11px] uppercase tracking-[0.18em] text-primary">{t("mlt2.recs")}</span>
                        <span className="font-mono text-[9px] uppercase text-fg-3">
                            {seeds.length === 0 ? t("mlt2.add_one") : `${t("mlt2.from_anchors")} ${seeds.length} ${t("mlt2.anchor_word")}`}
                        </span>
                    </div>
                    {seeds.length === 0 ? (
                        <div className="border border-dashed border-border-2 bg-bg-2 p-10 text-center font-mono text-xs text-fg-3">
                            {t("mlt2.pick_one")}
                        </div>
                    ) : similarMutation.isPending && recs.length === 0 ? (
                        <div className="flex items-center justify-center border border-border-2 bg-bg-2 py-16">
                            <Loader2 className="size-6 animate-spin text-primary" />
                        </div>
                    ) : (
                        <div className={cn("flex flex-col border border-border-2 bg-bg-2", similarMutation.isPending && "opacity-60")}>
                            {recs.map((f, i) => (
                                <button
                                    key={f.movie_id}
                                    onClick={() =>
                                        setQuickLook({
                                            tmdb_id: f.movie_id,
                                            title: f.title,
                                            year: f.year,
                                            overview: f.overview,
                                            poster_url: f.poster_path,
                                            q: f.vectorbox_score,
                                            contextLine: `${f.similarity_score}% match to your ${seeds.length}-anchor blend`,
                                        })
                                    }
                                    className={cn(
                                        "flex items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-bg-3",
                                        i > 0 && "border-t border-dashed border-border-2"
                                    )}
                                >
                                    <span className="w-5 font-display text-[10px] text-fg-3">{String(i + 1).padStart(2, "0")}</span>
                                    <div className="poster-art relative h-[60px] w-10 shrink-0 border border-border-2">
                                        {f.poster_path && (
                                            <Image src={getTMDBImageUrl(f.poster_path, "w154")} alt={f.title} fill sizes="40px" className="object-cover" />
                                        )}
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <div className="truncate font-mono text-xs text-fg">
                                            {language === "es" && f.title_es ? f.title_es : f.title}{" "}
                                            <span className="text-fg-3">· {f.year}</span>
                                        </div>
                                        <div className="mt-px font-mono text-[10px] text-fg-3">{f.similarity_score}% {t("mlt2.match_blend")}</div>
                                    </div>
                                    <div className="text-right">
                                        <div className="font-display text-[13px] font-bold text-primary">
                                            {f.vectorbox_score != null ? `Q${Math.round(f.vectorbox_score)}` : "—"}
                                        </div>
                                        <div className="font-mono text-[9px] text-fg-3">d {((100 - f.similarity_score) / 100).toFixed(2)}</div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            <QuickLook film={quickLook} context="mlt" onClose={() => setQuickLook(null)} />
        </div>
    );
}
