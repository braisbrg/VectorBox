"use client";

import { X, Globe, Tv, RotateCcw, Info, CheckCircle2, Lock } from "lucide-react";
import type { Contributor } from "@/types/feed";
import { motion, AnimatePresence } from "framer-motion";
import Image from "next/image";
import { useState, useCallback } from "react";
import { useLanguage } from "@/components/language-provider";
import { COUNTRIES, getProvidersForCountry } from "@/lib/constants";
import { FeedItem, getTMDBImageUrl, FilterSearchParams } from "@/lib/api";

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
}

export function RightConsole({
    selectedMovie,
    selectedSectionId,
    onCloseInspector,
    scope,
    onScopeChange,
    countryCode,
    onCountryChange,
    streamingProviders,
    onToggleProvider,
    onClearFilters,
    onFilterSearch,
}: RightConsoleProps) {
    const { t } = useLanguage();
    const inspectedMovie = selectedMovie;
    const selectedContributors = selectedMovie?.contributors;

    const getSectionReason = (sectionId?: string) => {
        if (!sectionId) return "MATRIX GENERAL ALGORITHM";
        if (sectionId.startsWith("because_you_watched")) return "Item-to-Item Semantic Similarity from Anchors";
        if (sectionId === "niche_picks") return "Matches User Cluster Taste Profile";
        if (sectionId === "picked_for_you") return "Hybrid RRF Signal Fusion Mechanism";
        if (sectionId === "available_now") return "Streaming Availability Cross-reference";
        if (sectionId === "hidden_gems") return "Algorithmically Detected Discovery Metrics";
        if (sectionId.startsWith("watchlist")) return "User Curated Dataset";
        if (sectionId === "auteur") return "Auteur/Director Affinity Calculation";
        if (sectionId === "cult_actor") return "Actor Representation Calculation";
        if (sectionId.includes("wildcard") || sectionId.includes("random")) return "Anti-Routine Parameter Matrix Injection";
        return "STANDARD VECTORBOX MATCH";
    };

    const [yearMin, setYearMin] = useState("");
    const [yearMax, setYearMax] = useState("");
    const [maxRuntime, setMaxRuntime] = useState("");
    const [minScore, setMinScore] = useState("");

    const handleFilterSearch = useCallback(() => {
        onFilterSearch?.({
            yearMin: yearMin ? parseInt(yearMin) : null,
            yearMax: yearMax ? parseInt(yearMax) : null,
            maxRuntime: maxRuntime ? parseInt(maxRuntime) : null,
            minScore: minScore ? parseFloat(minScore) : null,
        });
    }, [yearMin, yearMax, maxRuntime, minScore, onFilterSearch]);

    const activeProvidersCount = streamingProviders.length;

    return (
        <aside className="hidden lg:flex fixed right-0 top-0 w-80 h-screen bg-[#050505] border-l border-zinc-800 flex-col z-40 overflow-hidden font-mono text-xs">
            <AnimatePresence mode="wait">
                {!selectedMovie ? (
                    <motion.div
                        key="global-controls"
                        initial={{ opacity: 0, x: 20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 20 }}
                        className="flex flex-col h-full"
                    >
                        {/* Header */}
                        <div className="p-4 border-b border-zinc-800 bg-black flex items-center justify-between">
                            <span className="text-primary font-bold tracking-widest uppercase">SYS_CONSOLE_V2</span>
                            <div className="w-2 h-2 bg-primary animate-pulse" />
                        </div>

                        <div className="flex-1 overflow-y-auto p-4 space-y-8 custom-scrollbar">
                            {/* Scope Toggle */}
                            <div className="space-y-4">
                                <span className="text-zinc-500 uppercase tracking-widest text-[10px] block border-b border-zinc-800 pb-2">
                                    {">"} ACCESS_SCOPE
                                </span>
                                <div className="grid grid-cols-2 gap-2">
                                    <button
                                        onClick={() => onScopeChange("global")}
                                        className={`py-2 px-3 border transition-colors ${scope === "global" ? "bg-primary text-black border-primary font-bold" : "bg-transparent text-zinc-500 border-zinc-800 hover:border-zinc-500"}`}
                                    >
                                        GLOBAL
                                    </button>
                                    <button
                                        onClick={() => onScopeChange("watchlist")}
                                        className={`py-2 px-3 border transition-colors ${scope === "watchlist" ? "bg-primary text-black border-primary font-bold" : "bg-transparent text-zinc-500 border-zinc-800 hover:border-zinc-500"}`}
                                    >
                                        WATCHLIST
                                    </button>
                                </div>
                            </div>

                            {/* Streaming Providers */}
                            <div className="space-y-4">
                                <div className="flex items-center justify-between border-b border-zinc-800 pb-2">
                                    <span className="text-zinc-500 uppercase tracking-widest text-[10px]">
                                        {">"} SIGNAL_FILTERS
                                    </span>
                                    {activeProvidersCount > 0 && (
                                        <button onClick={onClearFilters} className="text-[10px] text-primary hover:underline">
                                            [RESET]
                                        </button>
                                    )}
                                </div>

                                <div className="relative mb-4">
                                    <select
                                        value={countryCode}
                                        onChange={(e) => onCountryChange(e.target.value)}
                                        className="w-full bg-zinc-900/50 border border-zinc-800 p-2 pl-8 appearance-none focus:outline-none focus:border-primary text-[10px]"
                                    >
                                        {COUNTRIES.map(c => <option key={c.code} value={c.code}>{c.name}</option>)}
                                    </select>
                                    <Globe className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-zinc-500" />
                                </div>

                                <div className="grid grid-cols-4 gap-2">
                                    {getProvidersForCountry(countryCode).map(p => {
                                        const isActive = streamingProviders.includes(p.id);
                                        return (
                                            <button
                                                key={p.id}
                                                onClick={() => onToggleProvider(p.id)}
                                                className={`relative aspect-square border transition-all ${isActive ? "border-primary opacity-100" : "border-zinc-800 opacity-40 hover:opacity-100 hover:border-zinc-500"}`}
                                                title={p.name}
                                            >
                                                <Image
                                                    src={`https://image.tmdb.org/t/p/w92${p.logo_path}`}
                                                    alt={p.name}
                                                    fill
                                                    sizes="40px"
                                                    className="object-cover"
                                                />
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>

                            {/* Query Filters */}
                            <div className="space-y-4">
                                <span className="text-zinc-500 uppercase tracking-widest text-[10px] block border-b border-zinc-800 pb-2">
                                    {">"} QUERY_FILTERS
                                </span>
                                <div className="grid grid-cols-2 gap-2">
                                    <div>
                                        <label className="text-[9px] text-zinc-600 uppercase block mb-1">YEAR_MIN</label>
                                        <input
                                            type="number"
                                            placeholder="1970"
                                            value={yearMin}
                                            onChange={(e) => setYearMin(e.target.value)}
                                            className="w-full bg-zinc-900/50 border border-zinc-800 p-2 focus:outline-none focus:border-primary text-[10px]"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-[9px] text-zinc-600 uppercase block mb-1">YEAR_MAX</label>
                                        <input
                                            type="number"
                                            placeholder="2025"
                                            value={yearMax}
                                            onChange={(e) => setYearMax(e.target.value)}
                                            className="w-full bg-zinc-900/50 border border-zinc-800 p-2 focus:outline-none focus:border-primary text-[10px]"
                                        />
                                    </div>
                                </div>
                                <div className="grid grid-cols-2 gap-2">
                                    <div>
                                        <label className="text-[9px] text-zinc-600 uppercase block mb-1">MAX_RUNTIME</label>
                                        <input
                                            type="number"
                                            placeholder="120"
                                            value={maxRuntime}
                                            onChange={(e) => setMaxRuntime(e.target.value)}
                                            className="w-full bg-zinc-900/50 border border-zinc-800 p-2 focus:outline-none focus:border-primary text-[10px]"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-[9px] text-zinc-600 uppercase block mb-1">MIN_SCORE</label>
                                        <input
                                            type="number"
                                            placeholder="6.0"
                                            step="0.5"
                                            min="0"
                                            max="10"
                                            value={minScore}
                                            onChange={(e) => setMinScore(e.target.value)}
                                            className="w-full bg-zinc-900/50 border border-zinc-800 p-2 focus:outline-none focus:border-primary text-[10px]"
                                        />
                                    </div>
                                </div>
                                <button
                                    onClick={handleFilterSearch}
                                    className="w-full py-2 border border-primary text-primary text-[10px] uppercase tracking-widest font-bold hover:bg-primary hover:text-black transition-colors"
                                >
                                    {">"} EXECUTE_QUERY
                                </button>
                            </div>
                        </div>

                        {/* Footer Status */}
                        <div className="p-4 border-t border-zinc-800 bg-black">
                            <div className="flex justify-between items-center text-[9px] text-zinc-600">
                                <span>STATUS: ONLINE</span>
                                <span>V2.1.0_PROD</span>
                            </div>
                        </div>
                    </motion.div>
                ) : (
                    <motion.div
                        key="movie-inspector"
                        initial={{ opacity: 0, x: 20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 20 }}
                        className="flex flex-col h-full bg-black"
                    >
                        {/* Inspector Header */}
                        <div className="p-4 border-b border-zinc-800 flex items-center justify-between">
                            <span className="text-primary font-bold tracking-widest uppercase">DATA_INSPECTOR</span>
                            <button
                                onClick={onCloseInspector}
                                className="p-1 hover:text-primary border border-transparent hover:border-primary transition-colors"
                            >
                                <X size={16} />
                            </button>
                        </div>

                        <div className="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">
                            {inspectedMovie ? (
                                <>
                                    {/* Poster */}
                                    <div className="relative aspect-[2/3] w-48 mx-auto border border-zinc-800 transition-all">
                                        {inspectedMovie.poster_url ? (
                                            <Image
                                                src={getTMDBImageUrl(inspectedMovie.poster_url, "w342")}
                                                alt={inspectedMovie.title ?? "Movie poster"}
                                                fill
                                                sizes="192px"
                                                className="object-cover"
                                            />
                                        ) : (
                                            <div className="flex items-center justify-center h-full bg-zinc-900 text-zinc-700">NO_DATA</div>
                                        )}
                                    </div>

                                    {/* Title & Metadata */}
                                    <div className="space-y-2">
                                        <h2 className="text-lg font-bold uppercase leading-tight tracking-tighter">
                                            {inspectedMovie.title}
                                        </h2>
                                        <div className="flex gap-4 text-[10px] text-zinc-500 font-bold">
                                            <span>{inspectedMovie.year || "????"}</span>
                                            <span>{inspectedMovie.runtime ? `${inspectedMovie.runtime} MIN` : "?? MIN"}</span>
                                        </div>
                                    </div>

                                    {/* VB Score */}
                                    <div className="p-4 border border-zinc-800 bg-zinc-900/30">
                                        <div className="flex justify-between items-center mb-1">
                                            <span className="text-[10px] text-zinc-500 uppercase italic font-bold">VECTORBOX_SCORE</span>
                                            <span className="text-primary font-black text-xl">
                                                {inspectedMovie.vectorbox_score || inspectedMovie.match_score}
                                            </span>
                                        </div>
                                        <div className="w-full h-1 bg-zinc-800 overflow-hidden">
                                            <div
                                                className="h-full bg-primary"
                                                style={{ width: `${inspectedMovie.vectorbox_score || inspectedMovie.match_score}%` }}
                                            />
                                        </div>
                                    </div>

                                    {/* Synopsis */}
                                    <div className="space-y-2">
                                        <span className="text-[10px] text-zinc-500 uppercase tracking-widest block border-b border-zinc-800 pb-2">
                                            {">"} DATA_SYNOPSIS
                                        </span>
                                        <p className="text-zinc-400 text-[11px] leading-relaxed font-sans normal-case">
                                            {inspectedMovie.overview || "NO OVERVIEW DATA AVAILABLE IN LOCAL_CACHE."}
                                        </p>
                                    </div>

                                    {/* WHY RECOMMENDED */}
                                    <div className="space-y-2 border-t border-zinc-800 pt-3 mt-3">
                                        <p className="text-[10px] font-mono text-zinc-500 uppercase tracking-widest">
                                            Why This Film
                                        </p>
                                        {selectedContributors && selectedContributors.length > 0 ? (
                                            <div className="space-y-2">
                                                {selectedContributors.map((c: Contributor, i: number) => (
                                                    <div key={i} className="space-y-0.5">
                                                        {c.type === "anchor" && (
                                                            <>
                                                                <p className="text-xs font-mono text-primary">
                                                                    Similar to {c.seed_title} ({c.seed_year})
                                                                </p>
                                                                <p className="text-[11px] font-mono text-zinc-500">
                                                                    You rated it {c.seed_rating}★ · Similarity {Math.round((c.similarity ?? 0) * 100)}%
                                                                </p>
                                                            </>
                                                        )}
                                                        {c.type === "cluster" && (
                                                            <>
                                                                <p className="text-xs font-mono text-primary">
                                                                    Matches cluster: {c.cluster_name}
                                                                </p>
                                                                {c.medoid_title && (
                                                                    <p className="text-[11px] font-mono text-zinc-500">
                                                                        Anchored to: {c.medoid_title}
                                                                    </p>
                                                                )}
                                                            </>
                                                        )}
                                                        {(c.type === "vibe" || c.type === "auteur" || c.type === "crowd" || c.type === "cult_actor" || c.type === "watchlist") && (
                                                            <div className="flex items-center justify-between gap-2">
                                                                <p className="text-xs font-mono text-primary">{c.label}</p>
                                                                {selectedContributors!.filter(
                                                                    (x: Contributor) => ["vibe", "auteur", "crowd", "cult_actor", "watchlist"].includes(x.type)
                                                                ).length > 1 && (
                                                                    <p className="text-[11px] font-mono text-zinc-500 shrink-0">
                                                                        {Math.round((c.score ?? 0) * 100)}%
                                                                    </p>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                ))}
                                            </div>
                                        ) : (
                                            <p className="text-zinc-400 text-[10px] uppercase mt-2">
                                                <span className="text-primary opacity-80 mr-2">LOGIC:</span>
                                                {getSectionReason(selectedSectionId)}
                                            </p>
                                        )}
                                    </div>

                                    {/* Actions */}
                                    <div className="space-y-2 pt-4">
                                        <button className="w-full py-3 border border-zinc-800 text-zinc-600 flex items-center justify-center gap-2 cursor-not-allowed group">
                                            <Lock size={12} />
                                            <span className="uppercase text-[10px] tracking-widest font-bold font-mono group-hover:text-zinc-400 transition-colors">
                                                {">"} MARK_WATCHED [IN_DEV]
                                            </span>
                                        </button>
                                        <button className="w-full py-3 border border-zinc-800 text-zinc-600 flex items-center justify-center gap-2 cursor-not-allowed group">
                                            <Lock size={12} />
                                            <span className="uppercase text-[10px] tracking-widest font-bold font-mono group-hover:text-zinc-400 transition-colors">
                                                {">"} RATE_DATA [LOCKED]
                                            </span>
                                        </button>
                                    </div>
                                </>
                            ) : (
                                <div className="text-center text-zinc-700 py-20 italic uppercase tracking-widest">
                                    ERROR: FAILED_TO_FETCH_METADATA
                                </div>
                            )}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </aside>
    );
}
