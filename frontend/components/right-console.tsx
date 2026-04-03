"use client";

import { useState, useEffect } from "react";
import { X, Globe, Tv, RotateCcw, Info, CheckCircle2, Lock } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import Image from "next/image";
import { useLanguage } from "@/components/language-provider";
import { COUNTRIES, getProvidersForCountry } from "@/lib/constants";
import { ClusterInfo, FeedItem, api, getTMDBImageUrl } from "@/lib/api";

interface RightConsoleProps {
    selectedMovieId: number | null;
    selectedSectionId?: string;
    selectedContributors?: any[];
    onCloseInspector: () => void;
    scope: "watchlist" | "global";
    onScopeChange: (scope: "watchlist" | "global") => void;
    countryCode: string;
    onCountryChange: (code: string) => void;
    streamingProviders: number[];
    onToggleProvider: (id: number) => void;
    onClearFilters: () => void;
}

export function RightConsole({
    selectedMovieId,
    selectedSectionId,
    selectedContributors,
    onCloseInspector,
    scope,
    onScopeChange,
    countryCode,
    onCountryChange,
    streamingProviders,
    onToggleProvider,
    onClearFilters,
}: RightConsoleProps) {
    const { t } = useLanguage();
    const [inspectedMovie, setInspectedMovie] = useState<FeedItem | null>(null);
    const [isLoadingMovie, setIsLoadingMovie] = useState(false);

    const getSectionReason = (sectionId?: string) => {
        if (!sectionId) return "MATRIX GENERAL ALGORITHM";
        if (sectionId.startsWith("because_you_watched")) return "Item-to-Item Semantic Similarity from Anchors";
        if (sectionId === "your_taste") return "Matches User Medoid Cluster Profiling";
        if (sectionId === "picked_for_you") return "Hybrid RRF Signal Fusion Mechanism";
        if (sectionId === "available_now") return "Streaming Availability Cross-reference";
        if (sectionId === "hidden_gems") return "Algorithmically Detected Discovery Metrics";
        if (sectionId.startsWith("watchlist")) return "User Curated Dataset";
        if (sectionId === "auteur") return "Auteur/Director Affinity Calculation";
        if (sectionId === "cult_actor") return "Actor Representation Calculation";
        if (sectionId.includes("wildcard") || sectionId.includes("random")) return "Anti-Routine Parameter Matrix Injection";
        return "STANDARD VECTORBOX MATCH";
    };

    useEffect(() => {
        if (selectedMovieId) {
            setIsLoadingMovie(true);
            // Fetch movie details - utilizing existing search/detail logic pattern
            // For Phase 1, we might use a mock or fetch if endpoint exists.
            // Assuming we can get minimal info from recommendations cached if we had it, 
            // but for a clean Inspector, we fetch.
            api.get(`/api/movies/${selectedMovieId}`)
                .then(res => setInspectedMovie(res.data))
                .catch(() => setInspectedMovie(null))
                .finally(() => setIsLoadingMovie(false));
        } else {
            setInspectedMovie(null);
        }
    }, [selectedMovieId]);

    const activeProvidersCount = streamingProviders.length;

    return (
        <aside className="hidden lg:flex fixed right-0 top-0 w-80 h-screen bg-[#050505] border-l border-zinc-800 flex-col z-40 overflow-hidden font-mono text-xs">
            <AnimatePresence mode="wait">
                {!selectedMovieId ? (
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
                            {isLoadingMovie ? (
                                <div className="flex items-center justify-center h-40">
                                    <div className="w-1 h-1 bg-primary animate-ping" />
                                </div>
                            ) : inspectedMovie ? (
                                <>
                                    {/* Mock Poster / Visual Area */}
                                    <div className="relative aspect-[2/3] w-48 mx-auto border border-zinc-800 grayscale hover:grayscale-0 transition-all">
                                        {inspectedMovie.poster_url || (inspectedMovie as any).poster_path ? (
                                            <Image
                                                src={inspectedMovie.poster_url || getTMDBImageUrl((inspectedMovie as any).poster_path, "w342")}
                                                alt={inspectedMovie.title}
                                                fill
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
                                                {selectedContributors.map((c: any, i: number) => (
                                                    <div key={i} className="space-y-0.5">
                                                        {c.type === "anchor" && (
                                                            <>
                                                                <p className="text-xs font-mono text-primary">
                                                                    Similar to {c.seed_title} ({c.seed_year})
                                                                </p>
                                                                <p className="text-[11px] font-mono text-zinc-500">
                                                                    You rated it {c.seed_rating}★ · Similarity {Math.round(c.similarity * 100)}%
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
                                                        {(c.type === "vibe" || c.type === "auteur" || c.type === "crowd") && (
                                                            <div className="flex items-center justify-between">
                                                                <p className="text-xs font-mono text-primary">{c.label}</p>
                                                                <p className="text-[11px] font-mono text-zinc-500">
                                                                    {Math.round(c.score * 100)}%
                                                                </p>
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
