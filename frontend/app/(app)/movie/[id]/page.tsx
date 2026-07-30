"use client";

// Movie dossier (handoff V2 surface): backdrop hero + poster/meta + actions +
// synopsis + why-this D2 panel + full-width more-like-this bottom row (posters
// navigate to each film's own dossier).

import { use, useRef, useState } from "react";
import Image from "next/image";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X, Loader2, ChevronLeft, ChevronRight } from "lucide-react";
import {
    getMovieDetail,
    getWhy,
    getSimilarMovies,
    getTMDBImageUrl,
    getLetterboxdUrl,
    setWatchlist,
    markWatched,
    rejectMovie,
    SimilarMovie,
} from "@/lib/api";
import { useLanguage } from "@/components/language-provider";
import { useContextualBack } from "@/lib/use-back";
import { SubScreenHeader } from "@/components/shell/sub-screen-header";
import { scheduleFeedInvalidation } from "@/lib/feed-invalidation";
import Link from "next/link";
import { TridentBar, ContributionCards, AnchorList, NeighborStrip, ClusterCard, BreakdownCta } from "@/components/why-breakdown";
import { cn } from "@/lib/utils";

const fmtDur = (m?: number) => (m ? `${Math.floor(m / 60)}H${String(m % 60).padStart(2, "0")}` : "—");

export default function MovieDossierPage({ params }: { params: Promise<{ id: string }> }) {
    const { id } = use(params);
    const tmdbId = Number(id);
    const goBack = useContextualBack();
    const { language, t } = useLanguage();
    const queryClient = useQueryClient();

    const { data: movie, isLoading } = useQuery({
        queryKey: ["movie", tmdbId],
        queryFn: () => getMovieDetail(tmdbId),
        staleTime: 10 * 60 * 1000,
    });
    const { data: why } = useQuery({
        queryKey: ["why", tmdbId],
        queryFn: () => getWhy(tmdbId),
        staleTime: 10 * 60 * 1000,
        retry: 1,
    });
    const { data: similar } = useQuery({
        queryKey: ["similar", tmdbId],
        queryFn: () => getSimilarMovies(tmdbId, 12),
        staleTime: 10 * 60 * 1000,
    });

    const [onWatchlist, setOnWatchlist] = useState(false);
    const [action, setAction] = useState<"watchlist" | "watched" | "rejected" | null>(null);
    // more-like-this row scroller (same arrow pattern as MovieCarousel)
    const similarRef = useRef<HTMLDivElement>(null);
    const scrollSimilar = (dir: "left" | "right") => {
        const el = similarRef.current;
        if (el) el.scrollBy({ left: dir === "left" ? -el.offsetWidth / 2 : el.offsetWidth / 2, behavior: "smooth" });
    };

    if (isLoading) {
        return (
            <div className="flex items-center justify-center py-32 font-mono text-xs uppercase tracking-widest text-fg-3">
                LOADING_DOSSIER…
            </div>
        );
    }
    if (!movie) {
        return (
            <div className="py-32 text-center font-mono text-xs uppercase tracking-widest text-fg-3">
                FILM_NOT_FOUND ·{" "}
                <button onClick={goBack} className="text-primary hover:underline">
                    [ back ]
                </button>
            </div>
        );
    }

    const title = language === "es" && movie.title_es ? movie.title_es : movie.title;
    const overview = language === "es" && movie.overview_es ? movie.overview_es : movie.overview;
    const q = movie.vectorbox_score;

    const act = async (kind: "watchlist" | "watched" | "rejected") => {
        if (action) return;
        setAction(kind);
        try {
            if (kind === "watchlist") {
                await setWatchlist(tmdbId, !onWatchlist);
                setOnWatchlist((v) => !v);
            } else if (kind === "watched") {
                await markWatched(tmdbId);
                scheduleFeedInvalidation(queryClient);
            } else {
                await rejectMovie(tmdbId);
                scheduleFeedInvalidation(queryClient);
            }
        } catch (e) {
            console.error(`${kind} failed`, e);
        } finally {
            setAction(null);
        }
    };

    return (
        <div className="space-y-8 pb-8 pt-4">
            {/* back + eyebrow — full-width row on mobile (sub-screen), compact button ≥lg */}
            <SubScreenHeader crumb="crumbs.dossier" fallback="/feed" />
            <div className="hidden items-center justify-between lg:flex">
                <button
                    onClick={goBack}
                    className="border border-border-2 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-fg-2 transition-colors hover:border-primary hover:text-primary"
                >
                    {t("ui.back")}
                </button>
                <span className="eyebrow">{t("dossier.eyebrow")}</span>
            </div>

            {/* backdrop hero */}
            <div className="relative -mx-4 h-48 overflow-hidden border-y border-border-2 md:mx-0 md:h-64 md:border-x">
                {movie.backdrop_path ? (
                    <Image
                        src={getTMDBImageUrl(movie.backdrop_path, "w1280")}
                        alt=""
                        fill
                        sizes="100vw"
                        className="object-cover opacity-50"
                        priority
                    />
                ) : (
                    <div className="poster-art size-full" />
                )}
                <div className="absolute inset-0 bg-gradient-to-t from-bg via-bg/40 to-transparent" />
                <div className="absolute bottom-4 left-4 right-4 flex items-end justify-between gap-4">
                    <div className="min-w-0">
                        <h1 className="truncate font-display text-3xl uppercase leading-none tracking-tight text-fg md:text-5xl">{title}</h1>
                        <div className="mt-2 font-mono text-[11px] text-fg-2">
                            {movie.directors?.[0] && <span className="text-fg">{movie.directors[0]}</span>}
                            {movie.directors?.[0] && " · "}
                            {movie.year} · {fmtDur(movie.runtime)}
                            {movie.genres.length > 0 && <span className="text-fg-3"> · {movie.genres.slice(0, 3).join(" / ").toLowerCase()}</span>}
                        </div>
                    </div>
                    {q != null && (
                        <div className="shrink-0 text-right">
                            <div className="font-display text-4xl font-bold leading-none tracking-[-0.04em] text-primary md:text-5xl">
                                Q{Math.round(q)}
                            </div>
                        </div>
                    )}
                </div>
            </div>

            <div className="grid grid-cols-1 gap-8 lg:grid-cols-[240px_1fr]">
                {/* left: poster + scores + actions */}
                <div className="space-y-4">
                    <div className="poster-art relative mx-auto aspect-[2/3] w-48 border border-border-2 lg:w-full">
                        {movie.poster_url && (
                            <Image src={getTMDBImageUrl(movie.poster_url, "w500")} alt={title} fill sizes="240px" className="object-cover" />
                        )}
                    </div>

                    {/* score strip — LBXD cell removed: Movie.letterboxd_rating was never
                        written (always "—"); the real scraped ★ lives on the popular row. */}
                    <div className="grid grid-cols-2 divide-x divide-border border border-border-2 bg-bg-2 text-center">
                        {[
                            { l: "IMDb", v: movie.imdb_rating != null ? `${movie.imdb_rating}` : "—" },
                            { l: "META", v: movie.metacritic_rating != null ? `${movie.metacritic_rating}` : "—" },
                        ].map((s) => (
                            <div key={s.l} className="py-2">
                                <div className="font-display text-sm text-fg">{s.v}</div>
                                <div className="font-mono text-[8px] uppercase tracking-[0.15em] text-fg-3">{s.l}</div>
                            </div>
                        ))}
                    </div>

                    <div className="space-y-2">
                        <button
                            onClick={() => act("watchlist")}
                            disabled={action !== null}
                            className={cn(
                                "flex min-h-[42px] w-full items-center justify-center gap-2 border px-3 py-2 font-mono text-[11px] font-bold uppercase tracking-[0.05em] transition-colors disabled:opacity-50",
                                onWatchlist
                                    ? "border-primary text-primary"
                                    : "border-primary bg-primary text-primary-ink hover:bg-transparent hover:text-primary"
                            )}
                        >
                            {action === "watchlist" ? <Loader2 size={12} className="animate-spin" /> : onWatchlist ? <Check size={12} /> : null}
                            {onWatchlist ? "on watchlist" : "+ watchlist"}
                        </button>
                        <a
                            href={getLetterboxdUrl(tmdbId)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex min-h-[42px] w-full items-center justify-center border border-border-2 px-3 py-2 font-mono text-[11px] uppercase tracking-[0.05em] text-fg transition-colors hover:border-primary hover:text-primary"
                        >
                            letterboxd ↗
                        </a>
                        <div className="grid grid-cols-2 gap-2">
                            <button
                                onClick={() => act("rejected")}
                                disabled={action !== null}
                                className="flex min-h-[36px] items-center justify-center gap-1 border border-dashed border-border-2 px-2 py-1.5 font-mono text-[10px] uppercase tracking-[0.05em] text-fg-2 transition-colors hover:border-danger hover:text-danger disabled:opacity-50"
                            >
                                {action === "rejected" ? <Loader2 size={10} className="animate-spin" /> : <X size={10} />} {t("dossier.pass")}
                            </button>
                            <button
                                onClick={() => act("watched")}
                                disabled={action !== null}
                                className="flex min-h-[36px] items-center justify-center gap-1 border border-dashed border-border-2 px-2 py-1.5 font-mono text-[10px] uppercase tracking-[0.05em] text-fg-2 transition-colors hover:border-primary hover:text-primary disabled:opacity-50"
                            >
                                {action === "watched" ? <Loader2 size={10} className="animate-spin" /> : <Check size={10} />} {t("dossier.seen")}
                            </button>
                        </div>
                    </div>

                    {/* availability (prototype dossier "availability · {CC}") */}
                    <div className="border border-border-2 bg-bg-2 p-3">
                        <div className="eyebrow mb-2">{t("dossier.availability")} · es</div>
                        {movie.streaming_providers && movie.streaming_providers.length > 0 ? (
                            <div className="flex flex-wrap gap-1.5">
                                {movie.streaming_providers.map((p) => (
                                    <span key={p} className="border border-primary bg-primary/10 px-2 py-0.5 font-mono text-[10px] lowercase text-primary">
                                        {p}
                                    </span>
                                ))}
                            </div>
                        ) : (
                            <p className="font-mono text-[10px] text-fg-3">{t("dossier.not_streaming")}</p>
                        )}
                        {/* TMDB API ToS: JustWatch credit wherever provider data renders */}
                        <p className="mt-2 font-mono text-[9px] text-fg-3/70">{t("insp.justwatch")}</p>
                    </div>

                    {/* cast */}
                    {movie.cast && movie.cast.length > 0 && (
                        <div className="border border-border-2 bg-bg-2 p-3">
                            <div className="eyebrow mb-2">{t("dossier.cast")}</div>
                            <div className="space-y-1 font-mono text-[11px] text-fg-2">
                                {movie.cast.slice(0, 3).map((c) => (
                                    <div key={c}>{c}</div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                {/* right: synopsis + why D2 */}
                <div className="space-y-6">
                    {movie.tagline && <p className="font-mono text-sm italic text-primary">“{movie.tagline}”</p>}
                    {overview && (
                        <div>
                            <div className="eyebrow mb-2">{t("dossier.synopsis")}</div>
                            {/* 3xl: prose stays readable, but uses the space the rail freed up */}
                            <p className="max-w-3xl font-mono text-[12px] leading-relaxed text-fg-2">{overview}</p>
                        </div>
                    )}

                    {/* WHY-THIS · DENSITY 2 — detail panel (full column width — no rail on /movie) */}
                    {why && (
                        <div className="border border-border-2 bg-bg-2 p-5">
                            <div className="mb-3.5 flex items-baseline justify-between">
                                <div className="font-display text-lg uppercase tracking-[-0.01em] text-fg">{t("dossier.why_title")}</div>
                                {why.rank != null && why.rank_pool != null && (
                                    <div className="font-mono text-[10px] uppercase tracking-[0.1em] text-fg-3">
                                        {t("dossier.rank")} {why.rank} {t("dossier.of")} {why.rank_pool}
                                    </div>
                                )}
                            </div>
                            <div className="mb-4">
                                <TridentBar trident={why.trident} height={12} />
                            </div>
                            <div className="mb-4">
                                <ContributionCards why={why} />
                            </div>
                            <div className="mb-4">
                                <div className="eyebrow mb-2">{t("dossier.matched_on")}</div>
                                <AnchorList anchors={why.anchors} />
                            </div>
                            <div className="mb-4">
                                <div className="eyebrow mb-2">{t("dossier.nearest_embed")}</div>
                                {/* Shared strip (same component as the rail mini-row) — posters
                                    navigate to each film's own dossier. */}
                                <NeighborStrip neighbors={why.neighbors} />
                            </div>
                            {why.cluster && <ClusterCard cluster={why.cluster} />}
                            <BreakdownCta tmdbId={tmdbId} className="mt-4" />
                        </div>
                    )}

                </div>
            </div>

            {/* MORE LIKE THIS — full-width bottom row, anchored on THIS film (user 2026-07-10).
                Same /similar/{id} data the page already fetches; posters navigate to their
                own dossiers. Feed-row sizing so it reads like a proper section. */}
            {similar && similar.length > 0 && (
                <div className="border-t border-border-2 pt-6">
                    <div className="mb-3 flex items-center justify-between">
                        <div className="eyebrow">{t("dossier.more_like_this")}</div>
                        <div className="flex items-center gap-3">
                            <span className="font-mono text-[10px] text-fg-3">{title.toLowerCase()} · {similar.length}</span>
                            {/* arrows — desktop only (mobile swipes natively); MovieCarousel pattern */}
                            <div className="hidden gap-2 md:flex">
                                <button
                                    onClick={() => scrollSimilar("left")}
                                    className="flex size-8 items-center justify-center border border-border-2 text-fg-3 transition-colors hover:border-primary hover:text-primary"
                                    aria-label={t("aria.scroll_left")}
                                >
                                    <ChevronLeft className="size-4" />
                                </button>
                                <button
                                    onClick={() => scrollSimilar("right")}
                                    className="flex size-8 items-center justify-center border border-border-2 text-fg-3 transition-colors hover:border-primary hover:text-primary"
                                    aria-label={t("aria.scroll_right")}
                                >
                                    <ChevronRight className="size-4" />
                                </button>
                            </div>
                        </div>
                    </div>
                    <div ref={similarRef} className="flex gap-2.5 overflow-x-auto pb-2 scrollbar-hide md:gap-3">
                        {similar.map((s: SimilarMovie) => (
                            <Link
                                key={s.movie_id}
                                href={`/movie/${s.movie_id}`}
                                className="group w-[118px] shrink-0 text-left md:w-[150px]"
                            >
                                <div className="poster-art relative aspect-[2/3] w-full border border-border-2 transition-colors group-hover:border-primary">
                                    {s.poster_path && (
                                        <Image src={getTMDBImageUrl(s.poster_path, "w342")} alt={s.title} fill sizes="(max-width: 768px) 118px, 150px" className="object-cover" />
                                    )}
                                    {s.vectorbox_score != null && (
                                        <span className="absolute right-0 top-0 bg-primary px-1.5 py-0.5 font-display text-[9px] font-bold text-primary-ink">
                                            Q{Math.round(s.vectorbox_score)}
                                        </span>
                                    )}
                                    <span className="absolute bottom-0 left-0 bg-black/85 px-1.5 py-0.5 font-display text-[9px] text-primary">
                                        {s.similarity_score}%
                                    </span>
                                </div>
                                <div className="mt-1 line-clamp-2 font-mono text-[10px] leading-tight text-fg transition-colors group-hover:text-primary">{s.title}</div>
                                <div className="font-mono text-[9px] text-fg-3">{s.year}</div>
                            </Link>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
