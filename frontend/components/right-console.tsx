"use client";

import { X, Check, Loader2, Plus } from "lucide-react";
import { m, AnimatePresence } from "framer-motion";
import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { FeedItem, getTMDBImageUrl, FilterSearchParams } from "@/lib/api";
import { WhyThisFilm } from "@/components/why-this-film";
import { FilterForm } from "@/components/filter-form";
import { MoodChips } from "@/components/mood-chips";
import { useLanguage } from "@/components/language-provider";
import { cn } from "@/lib/utils";
import { APP_VERSION } from "@/lib/version";

interface RightConsoleProps {
    selectedMovie: FeedItem | null;
    selectedSectionId?: string;
    onCloseInspector: () => void;
    scope: "watchlist" | "global";
    onScopeChange: (scope: "watchlist" | "global") => void;
    countryCode: string;
    onCountryChange: (code: string) => void;
    streamingProviders: number[];
    onToggleProvider: (id: number) => void;
    onClearFilters: () => void;
    onFilterSearch?: (params: FilterSearchParams) => void;
    onMarkWatched?: (tmdbId: number) => void;
    onWatchlist?: (tmdbId: number) => void;
    onReject?: (tmdbId: number) => void;
    inspectorActionLoading?: "watched" | "rejected" | "watchlist" | null;
    /** Count of active filter-search results (shows the "N films match" rail line). */
    filteredCount?: number | null;
}

export function RightConsole({
    selectedMovie,
    selectedSectionId,
    onCloseInspector,
    countryCode,
    onCountryChange,
    streamingProviders,
    onToggleProvider,
    onClearFilters,
    onFilterSearch,
    onMarkWatched,
    onWatchlist,
    onReject,
    inspectorActionLoading,
    filteredCount,
    scope,
    onScopeChange,
}: RightConsoleProps) {
    const inspectedMovie = selectedMovie;
    const selectedContributors = selectedMovie?.contributors;
    const { language, t } = useLanguage();
    const displayTitle =
        language === "es" && inspectedMovie?.title_es ? inspectedMovie.title_es : inspectedMovie?.title;
    const displayOverview =
        language === "es" && inspectedMovie?.overview_es ? inspectedMovie.overview_es : inspectedMovie?.overview;

    const [scoreExpanded, setScoreExpanded] = useState(false);
    // Sinopsis plegada por defecto. Una de TMDB pasa de 600 caracteres y en un
    // rail de 320px empuja "disponible en" y las acciones fuera de la pantalla:
    // había que bajar muchísimo para llegar a lo accionable. Se pliega por encima
    // de este umbral, no siempre — plegar tres líneas es ruido.
    const [overviewExpanded, setOverviewExpanded] = useState(false);
    const overviewIsLong = (displayOverview?.length ?? 0) > 240;

    return (
        <aside className="fixed right-0 top-0 z-40 hidden h-screen w-80 flex-col overflow-hidden border-l border-border-2 bg-bg font-mono text-xs lg:flex">
            <AnimatePresence mode="wait">
                {!selectedMovie ? (
                    <m.div
                        key="global-controls"
                        initial={{ opacity: 0, x: 20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 20 }}
                        className="flex h-full flex-col"
                    >
                        <div className="flex items-center justify-between border-b border-border-2 bg-bg-2 p-4">
                            <span className="font-bold uppercase tracking-widest text-primary">SYS_CONSOLE</span>
                            <div className="size-2 animate-pulse bg-primary" />
                        </div>

                        <div className="flex-1 overflow-y-auto p-4 scrollbar-hide">
                            <MoodChips />
                            <FilterForm
                                countryCode={countryCode}
                                onCountryChange={onCountryChange}
                                streamingProviders={streamingProviders}
                                onToggleProvider={onToggleProvider}
                                onClearFilters={onClearFilters}
                                onFilterSearch={onFilterSearch}
                                filteredCount={filteredCount}
                                scope={scope}
                                onScopeChange={onScopeChange}
                            />
                        </div>

                        <div className="border-t border-border-2 bg-bg-2 p-4">
                            <div className="flex items-center justify-between text-[9px] text-fg-3">
                                <span>STATUS: ONLINE</span>
                                <span>V{APP_VERSION}_PROD</span>
                            </div>
                        </div>
                    </m.div>
                ) : (
                    <m.div
                        key="movie-inspector"
                        initial={{ opacity: 0, x: 20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 20 }}
                        className="flex h-full flex-col bg-bg-2"
                    >
                        <div className="flex items-center justify-between border-b border-border-2 p-4">
                            <div className="flex items-center gap-2">
                                <span className="trident-mark">
                                    <b />
                                    <b />
                                    <b />
                                </span>
                                <span className="font-bold uppercase tracking-widest text-primary">DATA_INSPECTOR</span>
                            </div>
                            <button
                                onClick={onCloseInspector}
                                className="border border-transparent p-1 transition-colors hover:border-primary hover:text-primary"
                                aria-label="Close inspector"
                            >
                                <X size={16} />
                            </button>
                        </div>

                        <div className="flex-1 space-y-6 overflow-y-auto p-6 scrollbar-hide">
                            {inspectedMovie ? (
                                <>
                                    <div className="poster-art relative mx-auto aspect-[2/3] w-48 border border-border-2">
                                        {inspectedMovie.poster_url ? (
                                            <Image
                                                src={getTMDBImageUrl(inspectedMovie.poster_url, "w342")}
                                                alt={inspectedMovie.title ?? "Movie poster"}
                                                fill
                                                sizes="192px"
                                                className="object-cover"
                                            />
                                        ) : (
                                            <div className="flex h-full items-center justify-center text-fg-3">NO_DATA</div>
                                        )}
                                    </div>

                                    <div className="space-y-2">
                                        <h2 className="font-display text-lg uppercase leading-tight tracking-tight text-fg">
                                            {displayTitle}
                                        </h2>
                                        <div className="flex gap-4 text-[10px] font-bold text-fg-3">
                                            <span>{inspectedMovie.year || "????"}</span>
                                            <span>{inspectedMovie.runtime ? `${inspectedMovie.runtime} MIN` : "?? MIN"}</span>
                                        </div>
                                    </div>

                                    {/* VB Score */}
                                    <div
                                        className="cursor-pointer select-none border border-border-2 bg-bg-3 p-4"
                                        onClick={() => setScoreExpanded((v) => !v)}
                                        onKeyDown={(e) => { if (e.key === "Enter") setScoreExpanded((v) => !v); }}
                                        role="button"
                                        tabIndex={0}
                                    >
                                        <div className="mb-1 flex items-center justify-between">
                                            <span className="text-[10px] font-bold uppercase italic text-fg-3">VECTORBOX_SCORE</span>
                                            <div className="flex items-center gap-2">
                                                {/* No fallback to match_score: that is a
                                                    SIMILARITY, and this box is labelled
                                                    VECTORBOX_SCORE. movie-card.tsx already
                                                    says so; these two inspectors did the
                                                    opposite. No VBS → show nothing. */}
                                                <span className="font-display text-xl text-primary">
                                                    {inspectedMovie.vectorbox_score ?? "—"}
                                                </span>
                                                <span className="text-[9px] text-fg-3">{scoreExpanded ? "▲" : "▼"}</span>
                                            </div>
                                        </div>
                                        <div className="h-1 w-full overflow-hidden bg-border">
                                            <div
                                                className="h-full bg-primary"
                                                style={{ width: `${inspectedMovie.vectorbox_score ?? 0}%` }}
                                            />
                                        </div>
                                        {scoreExpanded && (inspectedMovie.imdb_rating || inspectedMovie.metacritic_rating || inspectedMovie.rating) && (
                                            <div className="mt-3 space-y-1 border-t border-border pt-2">
                                                {inspectedMovie.imdb_rating != null && (
                                                    <div className="flex justify-between text-[10px]">
                                                        <span className="text-fg-3">IMDb</span>
                                                        <span className="text-fg-2">{inspectedMovie.imdb_rating}/10</span>
                                                    </div>
                                                )}
                                                {inspectedMovie.metacritic_rating != null && (
                                                    <div className="flex justify-between text-[10px]">
                                                        <span className="text-fg-3">Metacritic</span>
                                                        <span className="text-fg-2">{inspectedMovie.metacritic_rating}/100</span>
                                                    </div>
                                                )}
                                                {inspectedMovie.rating != null && (
                                                    <div className="flex justify-between text-[10px]">
                                                        <span className="text-fg-3">TMDB</span>
                                                        <span className="text-fg-2">{inspectedMovie.rating.toFixed(1)}/10</span>
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                    </div>

                                    {/* Synopsis */}
                                    <div className="space-y-2">
                                        <span className="block border-b border-border pb-2 text-[10px] uppercase tracking-widest text-fg-3">
                                            {">"} DATA_SYNOPSIS
                                        </span>
                                        <p
                                            className={cn(
                                                "text-[11px] normal-case leading-relaxed text-fg-2",
                                                overviewIsLong && !overviewExpanded && "line-clamp-4"
                                            )}
                                        >
                                            {displayOverview || "NO OVERVIEW DATA AVAILABLE IN LOCAL_CACHE."}
                                        </p>
                                        {overviewIsLong && (
                                            <button
                                                onClick={() => setOverviewExpanded((v) => !v)}
                                                className="text-[10px] uppercase tracking-widest text-fg-3 transition-colors hover:text-primary"
                                            >
                                                {overviewExpanded ? t("insp.less") : t("insp.more")}
                                            </button>
                                        )}
                                    </div>

                                    {/* Available on (handoff renderRailInspector) */}
                                    <div className="space-y-2">
                                        <span className="block border-b border-border pb-2 text-[10px] uppercase tracking-widest text-fg-3">
                                            {">"} {t("insp.available_on")} · {countryCode}
                                        </span>
                                        {inspectedMovie.streaming_providers && inspectedMovie.streaming_providers.length > 0 ? (
                                            <div className="flex flex-wrap gap-1.5">
                                                {inspectedMovie.streaming_providers.map((p) => (
                                                    <span key={p} className="border border-primary bg-primary/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-primary">
                                                        {p}
                                                    </span>
                                                ))}
                                            </div>
                                        ) : (
                                            <p className="text-[10px] uppercase tracking-wide text-fg-3">not streaming in {countryCode}</p>
                                        )}
                                        {/* TMDB API ToS: JustWatch credit wherever provider data renders */}
                                        <p className="text-[9px] normal-case text-fg-3/70">{t("insp.justwatch")}</p>
                                    </div>

                                    {/* Why this film — density 1 */}
                                    <div className="mt-3 border-t border-border pt-3">
                                        <WhyThisFilm tmdbId={inspectedMovie.id} contributors={selectedContributors} sectionId={selectedSectionId} />
                                    </div>

                                    <Link
                                        href={`/movie/${inspectedMovie.id}`}
                                        className="block w-full border border-border-2 py-2 text-center font-mono text-[10px] uppercase tracking-[0.1em] text-fg-2 transition-colors hover:border-primary hover:text-primary"
                                    >
                                        {t("ql.full_page")}
                                    </Link>

                                    {/* Actions */}
                                    <div className="space-y-2 pt-4">
                                        {/* La watchlist faltaba aquí y es la acción
                                            más probable al inspeccionar algo que no
                                            has visto: decidir verla luego. */}
                                        {onWatchlist && (
                                            <button
                                                onClick={() => onWatchlist(inspectedMovie.id)}
                                                disabled={inspectorActionLoading !== null}
                                                className="flex w-full items-center justify-center gap-2 border border-border-2 py-3 text-fg-2 transition-colors hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
                                            >
                                                {inspectorActionLoading === "watchlist" ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
                                                <span className="text-[10px] font-bold uppercase tracking-widest">{">"} {t("insp.watchlist")}</span>
                                            </button>
                                        )}
                                        <button
                                            onClick={() => onMarkWatched?.(inspectedMovie.id)}
                                            disabled={inspectorActionLoading !== null}
                                            className="flex w-full items-center justify-center gap-2 border border-border-2 py-3 text-fg-2 transition-colors hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-50"
                                        >
                                            {inspectorActionLoading === "watched" ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                                            <span className="text-[10px] font-bold uppercase tracking-widest">{">"} {t("insp.mark_watched")}</span>
                                        </button>
                                        <button
                                            onClick={() => onReject?.(inspectedMovie.id)}
                                            disabled={inspectorActionLoading !== null}
                                            className="flex w-full items-center justify-center gap-2 border border-border-2 py-3 text-fg-3 transition-colors hover:border-danger hover:text-danger disabled:cursor-not-allowed disabled:opacity-50"
                                        >
                                            {inspectorActionLoading === "rejected" ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
                                            <span className="text-[10px] font-bold uppercase tracking-widest">{">"} {t("insp.not_interested")}</span>
                                        </button>
                                    </div>
                                </>
                            ) : (
                                <div className="py-20 text-center uppercase italic tracking-widest text-fg-3">
                                    ERROR: FAILED_TO_FETCH_METADATA
                                </div>
                            )}
                        </div>
                    </m.div>
                )}
            </AnimatePresence>
        </aside>
    );
}
