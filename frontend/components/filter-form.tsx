"use client";

// SYS_CONSOLE filter form (handoff provider-rail) — shared by the desktop
// right rail and the mobile filter bottom sheet so the two can't drift.
// Slider state lives HERE; country/providers are lifted to the shell.

import { useState, useCallback, useEffect } from "react";
import { COUNTRIES, GENRES, getProvidersForCountry } from "@/lib/constants";
import { FilterSearchParams } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";
import { cn } from "@/lib/utils";

export interface FilterFormProps {
    countryCode: string;
    onCountryChange: (code: string) => void;
    streamingProviders: number[];
    onToggleProvider: (id: number) => void;
    onClearFilters: () => void;
    onFilterSearch?: (params: FilterSearchParams) => void;
    /** Count of active filter-search results (shows the "N films match" line). */
    filteredCount?: number | null;
    /** Submit button label — mobile sheet overrides with "apply". */
    submitLabel?: string;
    /** Feed source: the whole catalogue or only the user's watchlist. */
    scope?: "global" | "watchlist";
    onScopeChange?: (scope: "global" | "watchlist") => void;
}

// Rail range slider with a live value label (handoff provider-rail sliders).
function RailSlider({
    label,
    min,
    max,
    step,
    value,
    onChange,
    display,
}: {
    label: string;
    min: number;
    max: number;
    step: number;
    value: number;
    onChange: (v: number) => void;
    display: string;
}) {
    return (
        <div>
            <div className="mb-1 flex items-center justify-between text-[9px] uppercase tracking-wide text-fg-3">
                <span>{label}</span>
                <span className="font-display text-fg">{display}</span>
            </div>
            <input
                type="range"
                min={min}
                max={max}
                step={step}
                value={value}
                onChange={(e) => onChange(Number(e.target.value))}
                className="h-1.5 w-full cursor-pointer appearance-none bg-bg-3"
                style={{ accentColor: "var(--primary)" }}
                aria-label={label}
            />
        </div>
    );
}

// Slider "no filter" extremes — a value at the extreme means "unset".
const YEAR_MIN_FLOOR = 1920;
const YEAR_MAX_CEIL = 2026;
const RUNTIME_CEIL = 240;
// Persisted slider filters (opt-in via the "remember filters" toggle).
const SAVE_KEY = "vb_rail_filters";

export function FilterForm({
    countryCode,
    onCountryChange,
    streamingProviders,
    onToggleProvider,
    onClearFilters,
    onFilterSearch,
    filteredCount,
    submitLabel = "> EXECUTE_QUERY",
    scope = "global",
    onScopeChange,
}: FilterFormProps) {
    const { t } = useLanguage();
    const [yearMin, setYearMin] = useState(YEAR_MIN_FLOOR);
    const [yearMax, setYearMax] = useState(YEAR_MAX_CEIL);
    const [maxRuntime, setMaxRuntime] = useState(RUNTIME_CEIL);
    const [minScore, setMinScore] = useState(0);
    const [genres, setGenres] = useState<string[]>([]);
    const [genresOpen, setGenresOpen] = useState(false);
    const [remember, setRemember] = useState(false);

    // Restore saved slider filters on mount (presence of the key = remember on).
    useEffect(() => {
        const raw = localStorage.getItem(SAVE_KEY);
        if (!raw) return;
        try {
            const f = JSON.parse(raw);
            setRemember(true);
            if (typeof f.yearMin === "number") setYearMin(f.yearMin);
            if (typeof f.yearMax === "number") setYearMax(f.yearMax);
            if (typeof f.maxRuntime === "number") setMaxRuntime(f.maxRuntime);
            if (typeof f.minScore === "number") setMinScore(f.minScore);
            if (Array.isArray(f.genres)) {
                const gs = f.genres.filter((g: unknown) => typeof g === "string");
                setGenres(gs);
                if (gs.length) setGenresOpen(true); // surface the restored selection
            }
        } catch { /* corrupt — ignore */ }
    }, []);

    // Persist while remember is on.
    useEffect(() => {
        if (remember) localStorage.setItem(SAVE_KEY, JSON.stringify({ yearMin, yearMax, maxRuntime, minScore, genres }));
    }, [remember, yearMin, yearMax, maxRuntime, minScore, genres]);

    const toggleRemember = () => setRemember((r) => {
        if (r) localStorage.removeItem(SAVE_KEY);
        return !r;
    });

    const handleFilterSearch = useCallback(() => {
        onFilterSearch?.({
            yearMin: yearMin > YEAR_MIN_FLOOR ? yearMin : null,
            yearMax: yearMax < YEAR_MAX_CEIL ? yearMax : null,
            maxRuntime: maxRuntime < RUNTIME_CEIL ? maxRuntime : null,
            minScore: minScore > 0 ? minScore : null,
            genres: genres.length ? genres : undefined,
            watchlist: scope === "watchlist",
        });
    }, [yearMin, yearMax, maxRuntime, minScore, genres, scope, onFilterSearch]);

    const toggleGenre = (g: string) =>
        setGenres((prev) => (prev.includes(g) ? prev.filter((x) => x !== g) : [...prev, g]));

    const activeProvidersCount = streamingProviders.length;
    const anyActive =
        activeProvidersCount > 0 ||
        yearMin > YEAR_MIN_FLOOR ||
        yearMax < YEAR_MAX_CEIL ||
        maxRuntime < RUNTIME_CEIL ||
        minScore > 0 ||
        genres.length > 0;

    // RESET clears EVERYTHING — the local sliders (which the shell can't see) plus
    // providers/country and the active filtered view (via onClearFilters in the shell).
    const resetAll = () => {
        setYearMin(YEAR_MIN_FLOOR);
        setYearMax(YEAR_MAX_CEIL);
        setMaxRuntime(RUNTIME_CEIL);
        setMinScore(0);
        setGenres([]);
        localStorage.removeItem(SAVE_KEY);
        setRemember(false);
        onClearFilters();
    };

    return (
        <div className="space-y-8">
            {/* SOURCE — de dónde salen las recomendaciones. Va el primero porque no es
                un filtro más: cambia el universo del que eligen todas las filas, y los
                de abajo se aplican DENTRO de lo que elijas aquí. */}
            {onScopeChange && (
                <div className="space-y-4">
                    <span className="block border-b border-border pb-2 text-[10px] uppercase tracking-widest text-fg-3">
                        {">"} SOURCE
                    </span>
                    <div className="flex gap-1">
                        {(["global", "watchlist"] as const).map((s) => (
                            <button
                                key={s}
                                onClick={() => onScopeChange(s)}
                                className={cn(
                                    "flex-1 border px-2.5 py-1.5 text-[10px] uppercase tracking-wide transition-colors",
                                    scope === s
                                        ? "border-primary bg-primary font-bold text-primary-ink"
                                        : "border-border-2 text-fg-3 hover:border-fg-3"
                                )}
                            >
                                {t(`filters.scope_${s}`)}
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* Providers + region (handoff provider-rail) */}
            <div className="space-y-4">
                <div className="flex items-center justify-between border-b border-border pb-2">
                    <span className="text-[10px] uppercase tracking-widest text-fg-3">{">"} PROVIDERS · {countryCode}</span>
                    {anyActive && (
                        <button onClick={resetAll} className="text-[10px] text-primary hover:underline">
                            [RESET]
                        </button>
                    )}
                </div>

                {/* region segmented buttons */}
                <div className="flex flex-wrap gap-1">
                    {COUNTRIES.map((c) => (
                        <button
                            key={c.code}
                            onClick={() => onCountryChange(c.code)}
                            className={cn(
                                "border px-2.5 py-1 text-[10px] uppercase tracking-wide transition-colors",
                                countryCode === c.code
                                    ? "border-primary bg-primary font-bold text-primary-ink"
                                    : "border-border-2 text-fg-3 hover:border-fg-3"
                            )}
                        >
                            {c.code}
                        </button>
                    ))}
                </div>

                {/* provider name rows (handoff provider-rail · checkbox square + name) */}
                <div className="flex flex-col gap-1">
                    {getProvidersForCountry(countryCode).map((p) => {
                        const isActive = streamingProviders.includes(p.id);
                        return (
                            <button
                                key={p.id}
                                onClick={() => onToggleProvider(p.id)}
                                className={cn(
                                    "flex items-center gap-2.5 border px-2.5 py-1.5 text-left transition-colors",
                                    isActive ? "border-primary bg-primary/5" : "border-border-2 hover:border-fg-3"
                                )}
                            >
                                <span
                                    className={cn(
                                        "flex size-4 shrink-0 items-center justify-center border font-display text-[10px] leading-none",
                                        isActive ? "border-primary bg-primary text-primary-ink" : "border-border-2 text-transparent"
                                    )}
                                >
                                    ✓
                                </span>
                                <span className={cn("flex-1 text-[11px] lowercase", isActive ? "text-fg" : "text-fg-2")}>{p.name}</span>
                            </button>
                        );
                    })}
                </div>
            </div>

            {/* Query filters — sliders */}
            <div className="space-y-4">
                <span className="block border-b border-border pb-2 text-[10px] uppercase tracking-widest text-fg-3">
                    {">"} QUERY_FILTERS
                </span>
                <RailSlider label={t("filters.year_min")} min={YEAR_MIN_FLOOR} max={YEAR_MAX_CEIL} step={1} value={yearMin} onChange={setYearMin} display={yearMin > YEAR_MIN_FLOOR ? String(yearMin) : t("filters.any")} />
                <RailSlider label={t("filters.year_max")} min={YEAR_MIN_FLOOR} max={YEAR_MAX_CEIL} step={1} value={yearMax} onChange={setYearMax} display={yearMax < YEAR_MAX_CEIL ? String(yearMax) : t("filters.any")} />
                <RailSlider label={t("filters.runtime_max")} min={60} max={RUNTIME_CEIL} step={5} value={maxRuntime} onChange={setMaxRuntime} display={maxRuntime < RUNTIME_CEIL ? `${maxRuntime}m` : t("filters.any")} />
                <RailSlider label={t("filters.quality_min")} min={0} max={100} step={5} value={minScore} onChange={setMinScore} display={minScore > 0 ? `Q${minScore}` : t("filters.any")} />

                {/* F10 genre chips — OR semantics, collapsed behind a disclosure (18 chips
                    would dominate the rail otherwise). Header weight matches PROVIDERS /
                    QUERY_FILTERS so it can't be missed. */}
                <div>
                    <button
                        onClick={() => setGenresOpen((o) => !o)}
                        className="flex w-full items-center justify-between border border-border-2 px-2.5 py-2 text-[10px] uppercase tracking-widest text-fg-3 transition-colors hover:border-primary hover:text-fg"
                    >
                        <span>{">"} {t("filters.genres")} {genresOpen ? "▾" : "▸"}</span>
                        <span className={cn("font-display", genres.length > 0 ? "text-primary" : "text-fg-3")}>
                            {genres.length > 0 ? genres.length : t("filters.any")}
                        </span>
                    </button>
                    {genresOpen && (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                            {GENRES.map((g) => {
                                const on = genres.includes(g);
                                return (
                                    <button
                                        key={g}
                                        onClick={() => toggleGenre(g)}
                                        className={cn(
                                            "border px-1.5 py-0.5 text-[9px] lowercase transition-colors",
                                            on ? "border-primary bg-primary/10 text-primary" : "border-border-2 text-fg-3 hover:border-fg-3"
                                        )}
                                    >
                                        {g}
                                    </button>
                                );
                            })}
                        </div>
                    )}
                </div>

                <button
                    onClick={handleFilterSearch}
                    className="w-full border border-primary py-2 text-[10px] font-bold uppercase tracking-widest text-primary transition-colors hover:bg-primary hover:text-primary-ink"
                >
                    {submitLabel}
                </button>
                <button
                    onClick={toggleRemember}
                    className="flex w-full items-center gap-2 text-[9px] uppercase tracking-wide text-fg-3 transition-colors hover:text-fg-2"
                >
                    <span className={cn("flex size-3.5 items-center justify-center border", remember ? "border-primary bg-primary text-primary-ink" : "border-border-2 text-transparent")}>
                        {remember ? "✓" : ""}
                    </span>
                    {t("filters.save_filters")}
                </button>
                {filteredCount != null && (
                    <div className="flex items-center justify-between border border-border-2 bg-bg-2 px-3 py-2 text-[10px]">
                        <span className="text-fg-3">RESULT</span>
                        <span>
                            <span className="font-bold text-primary">{filteredCount}</span> {t("filters.films_match")}
                        </span>
                    </div>
                )}
            </div>
        </div>
    );
}
