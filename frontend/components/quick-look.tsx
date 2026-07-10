"use client";

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { m, AnimatePresence } from "framer-motion";
import { X, Check } from "lucide-react";
import { AxiosError } from "axios";
import { getTMDBImageUrl, getLetterboxdUrl, getMovieDetail, setWatchlist } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";
import { cn } from "@/lib/utils";

export interface QuickLookFilm {
    tmdb_id: number;
    title: string;
    year?: number;
    runtime?: number;
    overview?: string;
    poster_url?: string | null;
    q?: number | null;
    /** Context-specific line, e.g. "92% similar to your blend" (mlt) or "predicted 4.2★ for @ana" (grp). */
    contextLine?: string;
}

interface QuickLookProps {
    film: QuickLookFilm | null;
    /** Which surface opened it — mirrors the handoff's quick-look contexts. */
    context: "mlt" | "grp" | "magic" | "space";
    onClose: () => void;
}

const CONTEXT_LABEL: Record<QuickLookProps["context"], string> = {
    mlt: "similars",
    grp: "group rec",
    magic: "magic box",
    space: "vector space",
};

/**
 * Quick-look — the shared V3 surface (handoff: modal on desktop, bottom-sheet
 * on mobile). Lightweight film summary + actions; "open full page →" goes to
 * the dossier.
 */
export function QuickLook({ film, context, onClose }: QuickLookProps) {
    const { language, t } = useLanguage();
    const [onWatchlist, setOnWatchlist] = useState(false);
    const [saving, setSaving] = useState(false);
    const [needsAuth, setNeedsAuth] = useState(false);

    // Lazy detail fetch on open — enriches the body (director/genres/ratings/tagline).
    const { data: detail } = useQuery({
        queryKey: ["movie", film?.tmdb_id],
        queryFn: () => getMovieDetail(film!.tmdb_id),
        enabled: !!film,
        staleTime: 10 * 60 * 1000,
    });

    const addWatchlist = async () => {
        if (!film || saving) return;
        setSaving(true);
        try {
            await setWatchlist(film.tmdb_id, !onWatchlist);
            setOnWatchlist((v) => !v);
        } catch (e) {
            // Guest (no session) → 401. Prompt sign-up instead of failing silently.
            if ((e as AxiosError).response?.status === 401) setNeedsAuth(true);
            else console.error("watchlist toggle failed", e);
        } finally {
            setSaving(false);
        }
    };

    const director = detail?.directors?.[0];
    const genres = detail?.genres ?? [];
    // Localized metadata via the detail fetch (callers pass EN by default)
    const displayTitle = language === "es" && detail?.title_es ? detail.title_es : film?.title;
    const displayOverview =
        language === "es" && detail?.overview_es ? detail.overview_es : (film?.overview ?? detail?.overview);

    return (
        <AnimatePresence>
            {film && (
                <>
                    <m.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={onClose}
                        className="fixed inset-0 z-[60] bg-black/70"
                    />
                    <m.div
                        initial={{ opacity: 0, y: 24 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 24 }}
                        transition={{ duration: 0.18 }}
                        role="dialog"
                        aria-modal="true"
                        className={cn(
                            "fixed z-[60] border border-border-2 bg-bg-2 font-mono shadow-acid",
                            // mobile: bottom sheet · desktop: centered modal
                            "inset-x-0 bottom-0 max-h-[85vh] overflow-y-auto",
                            "lg:inset-auto lg:left-1/2 lg:top-1/2 lg:w-[560px] lg:max-w-[92vw] lg:-translate-x-1/2 lg:-translate-y-1/2"
                        )}
                    >
                        <div className="flex items-center justify-between border-b border-border-2 px-4 py-3">
                            <span className="font-display text-[10px] uppercase tracking-[0.2em] text-fg-3">
                                quick-look · <span className="text-primary">{CONTEXT_LABEL[context]}</span>
                            </span>
                            <button onClick={onClose} aria-label="Close" className="p-1 text-fg-3 transition-colors hover:text-primary">
                                <X size={16} />
                            </button>
                        </div>

                        <div className="flex gap-4 p-4">
                            <div className="poster-art relative aspect-[2/3] w-28 shrink-0 overflow-hidden border border-border-2 sm:w-32">
                                {film.poster_url && (
                                    <Image src={getTMDBImageUrl(film.poster_url, "w342")} alt={film.title} fill sizes="128px" className="object-cover" />
                                )}
                                {film.q != null && (
                                    <span className="absolute right-0 top-0 bg-primary px-[7px] py-[3px] font-display text-[11px] font-bold leading-none text-primary-ink">
                                        Q{Math.round(film.q)}
                                    </span>
                                )}
                            </div>
                            <div className="min-w-0 flex-1">
                                <h2 className="font-display text-xl uppercase leading-tight tracking-tight text-fg">{displayTitle}</h2>
                                <div className="mt-1 font-mono text-[10px] text-fg-3">
                                    {film.year || "????"}
                                    {film.runtime ? ` · ${Math.floor(film.runtime / 60)}H${String(film.runtime % 60).padStart(2, "0")}` : ""}
                                </div>
                                {(director || detail?.imdb_rating != null) && (
                                    <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 font-mono text-[10px] text-fg-2">
                                        {director && <span>dir. {director}</span>}
                                        {detail?.imdb_rating != null && <span className="text-fg-3">IMDb {detail.imdb_rating}</span>}
                                        {detail?.metacritic_rating != null && <span className="text-fg-3">Meta {detail.metacritic_rating}</span>}
                                    </div>
                                )}
                                {genres.length > 0 && (
                                    <div className="mt-2 flex flex-wrap gap-1">
                                        {genres.slice(0, 4).map((g) => (
                                            <span key={g} className="border border-border-2 px-1.5 py-0.5 text-[9px] uppercase tracking-wide text-fg-3">
                                                {g}
                                            </span>
                                        ))}
                                    </div>
                                )}
                                {detail?.streaming_providers && detail.streaming_providers.length > 0 && (
                                    <div className="mt-2 flex flex-wrap items-center gap-1">
                                        <span className="font-mono text-[9px] uppercase tracking-widest text-fg-3">{t("ql.on")}</span>
                                        {detail.streaming_providers.slice(0, 4).map((p) => (
                                            <span key={p} className="border border-primary bg-primary/10 px-1.5 py-0.5 font-mono text-[9px] lowercase text-primary">
                                                {p}
                                            </span>
                                        ))}
                                    </div>
                                )}
                                {film.contextLine && (
                                    <div className="mt-2 border border-dashed border-border-2 bg-bg-3 px-2 py-1.5 font-mono text-[10px] text-primary">
                                        {film.contextLine}
                                    </div>
                                )}
                                {displayOverview && (
                                    <p className="mt-3 line-clamp-4 font-mono text-[11px] leading-relaxed text-fg-2">{displayOverview}</p>
                                )}
                                {detail?.tagline && (
                                    <p className="mt-2 font-mono text-[10px] italic text-primary">“{detail.tagline}”</p>
                                )}
                            </div>
                        </div>

                        <div className="grid grid-cols-2 gap-2 border-t border-border-2 p-4">
                            {needsAuth ? (
                                <Link
                                    href="/register"
                                    onClick={onClose}
                                    className="flex min-h-[40px] items-center justify-center border border-primary bg-primary px-3 py-2 font-mono text-[11px] font-bold uppercase tracking-[0.05em] text-primary-ink"
                                >
                                    {t("ql.sign_up")}
                                </Link>
                            ) : (
                                <button
                                    onClick={addWatchlist}
                                    disabled={saving}
                                    className={cn(
                                        "flex min-h-[40px] items-center justify-center gap-2 border px-3 py-2 font-mono text-[11px] font-bold uppercase tracking-[0.05em] transition-colors disabled:opacity-50",
                                        onWatchlist
                                            ? "border-primary bg-transparent text-primary"
                                            : "border-primary bg-primary text-primary-ink hover:bg-transparent hover:text-primary"
                                    )}
                                >
                                    {onWatchlist ? (
                                        <>
                                            <Check size={12} /> {t("ql.on_watchlist")}
                                        </>
                                    ) : (
                                        t("ql.add_watchlist")
                                    )}
                                </button>
                            )}
                            <a
                                href={getLetterboxdUrl(film.tmdb_id)}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex min-h-[40px] items-center justify-center border border-border-2 px-3 py-2 font-mono text-[11px] uppercase tracking-[0.05em] text-fg transition-colors hover:border-primary hover:text-primary"
                            >
                                letterboxd ↗
                            </a>
                            <Link
                                href={`/movie/${film.tmdb_id}`}
                                onClick={onClose}
                                className="col-span-2 flex min-h-[40px] items-center justify-center border border-border-2 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-fg-2 transition-colors hover:border-primary hover:text-primary"
                            >
                                {t("ql.full_page")}
                            </Link>
                        </div>
                    </m.div>
                </>
            )}
        </AnimatePresence>
    );
}
