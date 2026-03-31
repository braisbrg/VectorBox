"use client";

import { motion } from "framer-motion";
import Image from "next/image";
import Link from "next/link";
import { Info, Clock, Film, X } from "lucide-react";
import { getTMDBImageUrl } from "@/lib/api";
import { useState } from "react";
import { useLanguage } from "@/components/language-provider";

export interface MovieCardProps {
    id: number;
    title: string;
    posterPath?: string | null;
    year?: number;
    runtime?: number;
    rating?: number;
    matchScore?: number;
    vectorbox_score?: number;
    title_es?: string;
    overview?: string;
    overview_es?: string;
    genres?: string[];
    onInspect?: (id: number) => void;
    onReject?: (id: number) => void;
    priority?: boolean;
    className?: string;
    badgeType?: string;
    providers?: string[];
    contributors?: any[];
    forceVectorBoxScore?: boolean;
    imdb_rating?: number;
    metacritic_rating?: number;
    rotten_tomatoes_rating?: number;
    letterboxd_rating?: number;
    href?: string;
    variant?: "overlay" | "grid";
}

export function MovieCard({
    id,
    title,
    posterPath,
    year,
    runtime,
    matchScore,
    vectorbox_score,
    title_es,
    onInspect,
    onReject,
    priority = false,
    className = "",
    href,
}: MovieCardProps) {
    const [imageError, setImageError] = useState(false);
    const { t, language } = useLanguage();

    const displayTitle = (language === 'es' && title_es) ? title_es : title;
    const score = vectorbox_score || matchScore || 0;
    
    // Default link to Letterboxd if non provided
    const movieLink = href || `https://letterboxd.com/tmdb/${id}/`;

    return (
        <div className={`group relative bg-[#0a0a0a] border border-zinc-800 hover:border-primary transition-all duration-300 flex flex-col overflow-hidden h-full ${className}`}>
            <Link 
                href={movieLink}
                target="_blank"
                rel="noopener noreferrer"
                className="flex flex-col h-full"
            >
                {/* Poster Area */}
                <div className="relative aspect-[2/3] w-full overflow-hidden bg-black">
                    {!imageError && posterPath ? (
                        <Image
                            src={getTMDBImageUrl(posterPath)}
                            alt={title}
                            fill
                            className="object-cover transition-all duration-500 grayscale group-hover:grayscale-0 group-hover:scale-105 opacity-80 group-hover:opacity-100"
                            sizes="(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 20vw"
                            priority={priority}
                            onError={() => setImageError(true)}
                        />
                    ) : (
                        <div className="w-full h-full flex items-center justify-center text-zinc-800">
                            <Film size={48} strokeWidth={1} />
                        </div>
                    )}

                    {/* VB Score Overlay (Top Left) */}
                    {score > 0 && (
                        <div className="absolute top-0 left-0 z-20 bg-black/80 border-r border-b border-zinc-800 px-1.5 py-0.5 font-mono text-[10px] font-black text-primary tracking-tighter shadow-[2px_2px_0px_rgba(0,0,0,0.5)]">
                            {score.toFixed(0)}
                        </div>
                    )}

                    {/* Scanline Effect */}
                    <div className="absolute inset-0 bg-[linear-gradient(rgba(18,16,16,0)_50%,rgba(0,0,0,0.1)_50%)] z-10 bg-[length:100%_2px] pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity" />
                    
                    {/* Top Edge Glow */}
                    <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-primary/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>

                {/* Bottom Data Bar */}
                <div className="bg-black p-3 border-t border-zinc-800 flex flex-col gap-1 z-20">
                    <div className="flex justify-between items-start gap-2">
                        <h3 className="font-mono text-[11px] font-bold text-zinc-100 uppercase tracking-tighter truncate flex-1 group-hover:text-primary transition-colors">
                            {displayTitle}
                        </h3>
                    </div>

                    <div className="flex justify-between items-center text-[9px] font-mono text-zinc-600 font-bold tracking-widest mt-1">
                        <div className="flex gap-2">
                            <span>{year || "????"}</span>
                            {runtime && (
                                <>
                                    <span>|</span>
                                    <span>{runtime}M</span>
                                </>
                            )}
                        </div>
                    </div>
                </div>
            </Link>

            {/* Inspect Button - Absolute positioned to avoid Link click if desired, 
                but keeping it in a way that we can intercept it */}
            {/* Action Buttons */}
            <div className="absolute bottom-3 right-3 z-30 flex gap-1">
                {onReject && (
                    <button
                        onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            onReject(id);
                        }}
                        className="text-zinc-500 hover:text-red-500 p-1 bg-black/50 border border-zinc-800 hover:border-red-500 transition-all flex items-center gap-1 group/rej"
                        title="Not Interested"
                    >
                        <X size={10} className="group-hover/rej:animate-pulse" />
                    </button>
                )}
                <button
                    onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        onInspect?.(id);
                    }}
                    className="text-zinc-500 hover:text-primary p-1 bg-black/50 border border-zinc-800 hover:border-primary transition-all flex items-center gap-1 group/btn"
                    title={t("movie.inspect") || "INSPECT"}
                >
                    <Info size={10} className="group-hover/btn:animate-pulse" />
                    <span className="hidden group-hover/btn:inline text-[8px] uppercase font-mono">[i]</span>
                </button>
            </div>

            {/* Selection/Hover Frame */}
            <div className="absolute inset-0 border-2 border-primary opacity-0 group-hover:opacity-5 pointer-events-none transition-opacity z-10" />
        </div>
    );
}
