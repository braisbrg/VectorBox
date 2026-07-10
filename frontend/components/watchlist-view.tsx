"use client";

// Watchlist — handoff screens-v3/watchlist.jsx: hero stat strip (saved · total
// runtime · upcoming) + provider segs + sort row + 5-col data-strip grid
// (provider in the card HUD instead of ★). Real data via getWatchlist.
// Leaving-soon cell omitted: no provider-expiry data in the backend yet.

import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { m } from "framer-motion";
import { Loader2, SlidersHorizontal, X } from "lucide-react";
import { getLetterboxdUrl, getWatchlist } from "@/lib/api";
import { getProvidersForCountry } from "@/lib/constants";
import { SubScreenHeader } from "@/components/shell/sub-screen-header";
import { useLanguage } from "@/components/language-provider";
import { MovieCard } from "@/components/ui/movie-card";
import { FilterFab } from "@/components/shell/filter-fab";
import { BottomSheet } from "@/components/shell/bottom-sheet";
import { cn } from "@/lib/utils";

interface WatchlistViewProps {
    userId: number;
    username: string;
    countryCode?: string;
    streamingProviders?: number[];
    onInspect?: (movie: import("@/lib/api").FeedItem, sectionId?: string) => void;
}

const WATCHLIST_FILTERS_KEY = "watchlist_filters";

interface WatchlistFilters {
    runtimeMax?: number;
    runtimeMin?: number;
    yearMin?: number;
    yearMax?: number;
    genre?: string;
    sortBy: "date_added" | "title" | "rating";
    streaming: number[];
    minRating?: number;
}

const getPersistedFilters = (): Partial<WatchlistFilters> | null => {
    try {
        const saved = localStorage.getItem(WATCHLIST_FILTERS_KEY);
        if (saved) return JSON.parse(saved);
    } catch {}
    return null;
};

const EMPTY_PROVIDERS: number[] = [];

const SORTS: { k: WatchlistFilters["sortBy"]; key: string }[] = [
    { k: "date_added", key: "wl.sort_added" },
    { k: "rating", key: "wl.sort_quality" },
    { k: "title", key: "wl.sort_title" },
];

// Shared filter controls — rendered in the desktop bar AND the mobile sheet.
// Top-level components (not nested) so inputs don't remount per keystroke.

function ProviderPills({
    countryCode,
    streaming,
    onUpdate,
}: {
    countryCode: string;
    streaming: number[];
    onUpdate: (updates: Partial<WatchlistFilters>) => void;
}) {
    const { t } = useLanguage();
    return (
        <div className="flex max-w-full overflow-x-auto border border-border-2 scrollbar-hide">
            <button
                onClick={() => onUpdate({ streaming: [] })}
                className={cn(
                    "shrink-0 border-r border-border-2 px-3 py-1.5 font-mono text-[11px] transition-colors",
                    streaming.length === 0 ? "bg-primary font-bold text-primary-ink" : "text-fg-2 hover:text-fg"
                )}
            >
                {t("wl.all")}
            </button>
            {getProvidersForCountry(countryCode).map((p) => {
                const on = streaming.includes(p.id);
                return (
                    <button
                        key={p.id}
                        onClick={() =>
                            onUpdate({
                                streaming: on ? streaming.filter((id) => id !== p.id) : [...streaming, p.id],
                            })
                        }
                        className={cn(
                            "shrink-0 border-r border-border-2 px-3 py-1.5 font-mono text-[11px] lowercase transition-colors last:border-r-0",
                            on ? "bg-primary font-bold text-primary-ink" : "text-fg-2 hover:text-fg"
                        )}
                    >
                        {p.name}
                    </button>
                );
            })}
        </div>
    );
}

function SortRow({ sortBy, onUpdate }: { sortBy: WatchlistFilters["sortBy"]; onUpdate: (u: Partial<WatchlistFilters>) => void }) {
    const { t } = useLanguage();
    return (
        <div className="flex items-center gap-1 font-mono text-[11px] text-fg-3">
            <span>{t("wl.sort")}</span>
            {SORTS.map((s) => (
                <button
                    key={s.k}
                    onClick={() => onUpdate({ sortBy: s.k })}
                    className={cn(
                        "px-1.5 py-1 transition-colors",
                        sortBy === s.k ? "text-primary underline underline-offset-4" : "text-fg-2 hover:text-fg"
                    )}
                >
                    {t(s.key)}
                </button>
            ))}
        </div>
    );
}

function ExtraFilters({
    filters,
    onUpdate,
    onClearAll,
    idPrefix,
}: {
    filters: WatchlistFilters;
    onUpdate: (u: Partial<WatchlistFilters>) => void;
    onClearAll: () => void;
    idPrefix: string;
}) {
    const { t } = useLanguage();
    return (
        <>
            <div className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-4">
                <div>
                    <label htmlFor={`${idPrefix}-runtime`} className="eyebrow mb-1.5 flex justify-between">
                        <span>{t("wl.max_runtime")}</span>
                        <span className="text-fg-2">{filters.runtimeMax ? `${filters.runtimeMax}m` : t("wl.any")}</span>
                    </label>
                    <input
                        id={`${idPrefix}-runtime`}
                        type="range"
                        min="60"
                        max="240"
                        step="10"
                        className="w-full"
                        style={{ accentColor: "var(--primary)" }}
                        value={filters.runtimeMax || 240}
                        onChange={(e) => onUpdate({ runtimeMax: parseInt(e.target.value) })}
                    />
                </div>
                <div>
                    <label htmlFor={`${idPrefix}-year-from`} className="eyebrow mb-1.5 block">{t("wl.year_from")}</label>
                    <input
                        id={`${idPrefix}-year-from`}
                        type="number"
                        placeholder="1980"
                        className="w-full border border-border-2 bg-bg-3 p-2 font-mono text-[11px] focus:border-primary focus:outline-none"
                        value={filters.yearMin || ""}
                        onChange={(e) => onUpdate({ yearMin: e.target.value ? parseInt(e.target.value) : undefined })}
                    />
                </div>
                <div>
                    <label htmlFor={`${idPrefix}-year-to`} className="eyebrow mb-1.5 block">{t("wl.year_to")}</label>
                    <input
                        id={`${idPrefix}-year-to`}
                        type="number"
                        placeholder="2024"
                        className="w-full border border-border-2 bg-bg-3 p-2 font-mono text-[11px] focus:border-primary focus:outline-none"
                        value={filters.yearMax || ""}
                        onChange={(e) => onUpdate({ yearMax: e.target.value ? parseInt(e.target.value) : undefined })}
                    />
                </div>
                <div>
                    <label htmlFor={`${idPrefix}-min-q`} className="eyebrow mb-1.5 flex justify-between">
                        <span>{t("wl.min_q")}</span>
                        <span className="text-fg-2">{filters.minRating ? `${filters.minRating}` : t("wl.any")}</span>
                    </label>
                    <input
                        id={`${idPrefix}-min-q`}
                        type="range"
                        min="0"
                        max="100"
                        step="5"
                        className="w-full"
                        style={{ accentColor: "var(--primary)" }}
                        value={filters.minRating || 0}
                        onChange={(e) => onUpdate({ minRating: parseFloat(e.target.value) || undefined })}
                    />
                </div>
            </div>
            <button
                onClick={onClearAll}
                className="flex w-full items-center justify-center gap-1 border-t border-border-2 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-fg-3 transition-colors hover:text-danger"
            >
                <X className="size-3" /> {t("wl.clear_all")}
            </button>
        </>
    );
}

export function WatchlistView({ userId, username, countryCode = "ES", streamingProviders = EMPTY_PROVIDERS, onInspect }: WatchlistViewProps) {
    const { t } = useLanguage();
    const [showFilters, setShowFilters] = useState(false);
    const [sheetOpen, setSheetOpen] = useState(false);
    const [page, setPage] = useState(1);
    const LIMIT = 20;
    const [filters, setFilters] = useState<WatchlistFilters>(() => {
        const base: WatchlistFilters = {
            sortBy: "date_added",
            streaming: streamingProviders,
            ...getPersistedFilters(),
        };
        base.streaming = streamingProviders; // streaming always from prop, never from localStorage
        return base;
    });

    const updateFilters = (updates: Partial<WatchlistFilters>) => {
        setFilters((prev) => {
            const next = { ...prev, ...updates };
            const { streaming, ...toPersist } = next;
            localStorage.setItem(WATCHLIST_FILTERS_KEY, JSON.stringify(toPersist));
            return next;
        });
        setPage(1);
    };

    const clearAllFilters = () => {
        localStorage.removeItem(WATCHLIST_FILTERS_KEY);
        setFilters({ sortBy: "date_added", streaming: [] });
        setPage(1);
    };

    const [debouncedFilters, setDebouncedFilters] = useState(filters);
    useEffect(() => {
        const timer = setTimeout(() => setDebouncedFilters(filters), 500);
        return () => clearTimeout(timer);
    }, [filters]);

    const { data, isLoading, error } = useQuery({
        queryKey: ["watchlist", userId, debouncedFilters, countryCode, page],
        queryFn: () =>
            getWatchlist(page, LIMIT, countryCode, {
                sort_by: debouncedFilters.sortBy,
                runtime_min: debouncedFilters.runtimeMin,
                runtime_max: debouncedFilters.runtimeMax,
                year_min: debouncedFilters.yearMin,
                year_max: debouncedFilters.yearMax,
                genres: debouncedFilters.genre,
                min_rating: debouncedFilters.minRating,
                streaming_providers:
                    debouncedFilters.streaming.length > 0 ? debouncedFilters.streaming.join(",") : undefined,
            }),
    });

    const stats = data?.stats;
    const totalMin = stats?.total_runtime_min ?? 0;
    const extraFilterCount = [filters.runtimeMax, filters.yearMin, filters.yearMax, filters.minRating].filter(Boolean).length;

    if (isLoading && !data) {
        return (
            <div className="flex items-center justify-center py-20">
                <Loader2 className="size-10 animate-spin text-primary" />
            </div>
        );
    }
    if (error) {
        return (
            <div className="mt-6 border border-danger/40 bg-bg-2 p-6 font-mono text-sm text-danger">
                Failed to load watchlist. Please try again.
            </div>
        );
    }

    return (
        <div className="space-y-5 pt-6">
            {/* Mobile back-row — /watch is a sub-screen reached from /you (handoff) */}
            <SubScreenHeader crumb="crumbs.watchlist" fallback="/you" />
            <h1 className="font-display text-2xl uppercase tracking-[-0.02em] text-fg">watchlist</h1>

            {/* HERO STAT STRIP — tappable summary on mobile opens the filter sheet (handoff) */}
            <div
                className="grid grid-cols-3 gap-4 border border-border-2 bg-bg-2 p-4"
                role="button"
                tabIndex={0}
                onClick={() => {
                    if (typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches) setSheetOpen(true);
                }}
            >
                <div>
                    <div className="eyebrow mb-1">{t("wl.films_saved")}</div>
                    <div className="font-display text-3xl leading-none text-primary">{stats?.total_films ?? data?.total ?? 0}</div>
                </div>
                <div>
                    <div className="eyebrow mb-1">{t("wl.total_runtime")}</div>
                    <div className="font-display text-[26px] leading-none text-fg">
                        {Math.floor(totalMin / 60)}
                        <span className="text-lg text-fg-3">h</span>
                        {totalMin % 60}
                        <span className="text-lg text-fg-3">m</span>
                    </div>
                    <div className="mt-1 font-mono text-[10px] text-fg-3">~{Math.max(1, Math.ceil(totalMin / 60 / 2))} {t("wl.weekends")}</div>
                </div>
                <div>
                    <div className="eyebrow mb-1">upcoming</div>
                    <div className="font-display text-[26px] leading-none text-accent-purple">{stats?.upcoming ?? 0}</div>
                    <div className="mt-1 font-mono text-[10px] text-fg-3">{t("wl.release_pending")}</div>
                </div>
            </div>

            {/* FILTER BAR — provider segs · sort · filters toggle (desktop; mobile uses the FAB sheet) */}
            <div className="hidden flex-wrap items-center gap-3 lg:flex">
                <ProviderPills countryCode={countryCode} streaming={filters.streaming} onUpdate={updateFilters} />

                <div className="flex-1" />

                <SortRow sortBy={filters.sortBy} onUpdate={updateFilters} />

                <button
                    onClick={() => setShowFilters((v) => !v)}
                    className={cn(
                        "flex items-center gap-1.5 border px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-[0.1em] transition-colors",
                        showFilters || extraFilterCount > 0
                            ? "border-primary text-primary"
                            : "border-border-2 text-fg-3 hover:border-fg-3 hover:text-fg-2"
                    )}
                >
                    <SlidersHorizontal className="size-3" /> {t("wl.filters")}{extraFilterCount > 0 ? ` · ${extraFilterCount}` : ""}
                </button>
            </div>

            {/* extra filters accordion (desktop) */}
            {showFilters && (
                <m.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    className="hidden overflow-hidden border border-border-2 bg-bg-2 lg:block"
                >
                    <ExtraFilters filters={filters} onUpdate={updateFilters} onClearAll={clearAllFilters} idPrefix="wl" />
                </m.div>
            )}

            {/* Mobile: filters FAB + bottom sheet (handoff — no tab bar on /watch, FAB sits low) */}
            <FilterFab
                count={filters.streaming.length + extraFilterCount}
                onClick={() => setSheetOpen(true)}
            />
            <BottomSheet open={sheetOpen} onClose={() => setSheetOpen(false)} ariaLabel="Watchlist filters">
                <div className="flex items-center justify-between border-b border-border-2 px-5 pb-3">
                    <span className="text-xs font-bold uppercase tracking-widest text-primary">FILTERS</span>
                </div>
                <div className="space-y-4 overflow-y-auto px-5 py-4 pb-[calc(env(safe-area-inset-bottom)+24px)]">
                    <ProviderPills countryCode={countryCode} streaming={filters.streaming} onUpdate={updateFilters} />
                    <SortRow sortBy={filters.sortBy} onUpdate={updateFilters} />
                    <div className="border border-border-2 bg-bg-3/40">
                        <ExtraFilters filters={filters} onUpdate={updateFilters} onClearAll={clearAllFilters} idPrefix="wlm" />
                    </div>
                </div>
            </BottomSheet>

            {/* GRID */}
            {!data || data.items.length === 0 ? (
                <div className="border border-dashed border-border-2 bg-bg-2 p-10 text-center">
                    <p className="font-display text-lg uppercase text-fg-3">{t("wl.empty_title")}</p>
                    <p className="mt-2 font-mono text-[11px] text-fg-3">
                        {extraFilterCount > 0 || filters.streaming.length > 0
                            ? t("wl.empty_filtered")
                            : t("wl.empty_note")}
                    </p>
                </div>
            ) : (
                <div className="space-y-6">
                    <div className={cn("grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5", isLoading && "opacity-60")}>
                        {data.items.map((item) => (
                            <MovieCard
                                key={item.id}
                                id={item.id}
                                title={item.title}
                                posterPath={item.poster_url}
                                year={item.year}
                                runtime={item.runtime}
                                href={getLetterboxdUrl(item.id)}
                                overview={item.overview}
                                vectorbox_score={item.vectorbox_score}
                                contributors={item.contributors}
                                hudRight={(item.streaming_providers?.[0] || "—").toLowerCase()}
                                onInspect={() => onInspect?.(item, "watchlist")}
                            />
                        ))}
                    </div>

                    {data.total > LIMIT && (
                        <div className="flex items-center justify-center gap-4 border-t border-border-2 pt-4">
                            <button
                                onClick={() => setPage((p) => Math.max(1, p - 1))}
                                disabled={page === 1}
                                className="border border-border-2 px-4 py-2 font-mono text-[11px] uppercase tracking-[0.05em] text-fg-2 transition-colors hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
                            >
                                ← prev
                            </button>
                            <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-fg-3">
                                page {page} / {Math.ceil(data.total / LIMIT)}
                            </span>
                            <button
                                onClick={() => setPage((p) => p + 1)}
                                disabled={page >= Math.ceil(data.total / LIMIT)}
                                className="border border-border-2 px-4 py-2 font-mono text-[11px] uppercase tracking-[0.05em] text-fg-2 transition-colors hover:border-primary hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
                            >
                                next →
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
