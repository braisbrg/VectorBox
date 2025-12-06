"use client";

import { motion } from "framer-motion";
import Image from "next/image";
import { Star, Clock, Tv, Film, RotateCcw, HelpCircle, Zap } from "lucide-react";
import { getTMDBImageUrl } from "@/lib/api";
import { useState } from "react";
import { STREAMING_PROVIDERS } from "@/lib/constants";
import { useLanguage } from "@/components/language-provider";

export interface MovieCardProps {
    id: number;
    title: string;
    posterPath?: string | null;
    year?: number;
    runtime?: number;
    rating?: number;      // TMDB Vote Average (0-10)
    matchScore?: number;  // Similarity/Match Score (0-100)
    genres?: string[];
    providers?: string[];
    overview?: string;
    variant?: "overlay" | "grid"; // overlay = text over image (feed), grid = text below image (search)
    href?: string;
    onClick?: () => void;
    className?: string;
    priority?: boolean;
    badgeType?: "match" | "rating" | "none" | "letterboxd"; // Explicit control over badge
    contributors?: { seed_title: string; contribution: number }[];
    hideProvidersOnFront?: boolean;
    // Phase 12 Props
    vectorbox_score?: number;
    imdb_rating?: number;
    metacritic_rating?: number;
    rotten_tomatoes_rating?: number;
    title_es?: string;
    overview_es?: string;
    letterboxd_rating?: number;
    forceVectorBoxScore?: boolean; // New prop to force VB display
}

export function MovieCard({
    id,
    title,
    posterPath,
    year,
    runtime,
    rating,
    matchScore,
    genres,
    providers = [],
    overview,
    variant = "grid",
    href,
    onClick,
    className = "",
    priority = false,
    badgeType,
    contributors = [],
    hideProvidersOnFront = false,
    vectorbox_score,
    imdb_rating,
    metacritic_rating,
    rotten_tomatoes_rating,
    title_es,
    overview_es,
    letterboxd_rating,
    forceVectorBoxScore = false,
}: MovieCardProps) {
    const [flipContent, setFlipContent] = useState<"synopsis" | "contributors" | null>(null);
    const [imageError, setImageError] = useState(false);
    const { t, language } = useLanguage();

    const isFlipped = flipContent !== null;

    // i18n Logic
    const displayTitle = (language === 'es' && title_es) ? title_es : title;
    const displayOverview = (language === 'es' && overview_es) ? overview_es : overview;

    // Determine which badge to show
    // If forceVectorBoxScore is true, we prioritize VB score over match score
    const showVectorBox = (vectorbox_score !== undefined && vectorbox_score !== null);
    const showMatch = !forceVectorBoxScore && (badgeType === "match" || (!badgeType && matchScore !== undefined));
    const showRating = !forceVectorBoxScore && !showMatch && (badgeType === "rating" || (!badgeType && matchScore === undefined && rating !== undefined));
    const showLetterboxd = letterboxd_rating !== undefined && letterboxd_rating !== null;

    // Badge Logic - Acid Style
    const renderBadge = () => {
        // Prepare VectorBox Score Badge (Reusable)
        const vbBadge = (
            <div
                className="bg-purple-600 text-white text-xs font-mono font-bold px-2 py-1 border-b-2 border-l-2 border-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] cursor-help z-20"
                title={[
                    (vectorbox_score !== undefined && vectorbox_score !== null) ? `VectorBox Score: ${vectorbox_score.toFixed(1)} / 10` : "VectorBox Score: N/A",
                    rating ? `TMDB Rating: ${rating.toFixed(1)} / 10` : null,
                    imdb_rating ? `IMDb: ${imdb_rating} / 10` : null,
                    rotten_tomatoes_rating ? `Rotten Tomatoes: ${rotten_tomatoes_rating}%` : null,
                    metacritic_rating ? `Metacritic: ${metacritic_rating} / 100` : null,
                ].filter(Boolean).join("\n")}
            >
                VB: {(vectorbox_score !== undefined && vectorbox_score !== null) ? vectorbox_score.toFixed(1) : "N/A"}
            </div>
        );

        if (forceVectorBoxScore) {
            return (
                <div className="absolute top-0 right-0 z-10 flex flex-col items-end">
                    {vbBadge}
                </div>
            );
        }

        // Prepare Letterboxd Badge
        // If badgeType is explicitly "letterboxd", we show this prominently
        const isLetterboxdPrimary = badgeType === "letterboxd";

        const lbBadge = showLetterboxd ? (
            <div
                className={`text-black text-xs font-mono font-bold px-2 py-1 border-b-2 border-l-2 border-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] cursor-help z-20 ${isLetterboxdPrimary ? "bg-[#00e054] mb-1" : "bg-[#00e054] mt-1"}`}
                title={`Letterboxd Rating: ${letterboxd_rating?.toFixed(1)} / 5.0`}
            >
                LB: {letterboxd_rating?.toFixed(1)}
            </div>
        ) : null;

        // 1. Explicit Letterboxd Badge (Primary)
        if (isLetterboxdPrimary && showLetterboxd) {
            return (
                <div className="absolute top-0 right-0 z-10 flex flex-col items-end">
                    {lbBadge}
                    {/* Show VB Score below if available */}
                    {vbBadge}
                </div>
            );
        }

        // 2. Match Score (Default for recommendations)
        if (showMatch && matchScore !== undefined && matchScore !== null && matchScore > 0) {
            return (
                <div className="absolute top-0 right-0 z-10 flex flex-col items-end">
                    <div
                        className="bg-primary text-black text-xs font-mono font-bold px-2 py-1 border-b-2 border-l-2 border-black shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] cursor-help"
                        title={`Match Probability: ${Math.round(matchScore)}%`}
                    >
                        {Math.round(matchScore)}% MATCH
                    </div>
                    {/* Show VB Score below Match Score if available */}
                    {vbBadge}
                    {lbBadge}
                </div>
            );
        }

        // 3. VectorBox Score (Primary if no match score)
        if (showVectorBox) {
            return (
                <div className="absolute top-0 right-0 z-10 flex flex-col items-end">
                    {vbBadge}
                    {lbBadge}
                </div>
            );
        }

        // 4. TMDB Rating (Fallback)
        if (showRating && rating !== undefined && rating !== null) {
            return (
                <div className="absolute top-0 right-0 flex flex-col items-end z-10">
                    <div className="bg-black text-primary text-xs font-mono font-bold px-2 py-1 border-b-2 border-l-2 border-primary shadow-[2px_2px_0px_0px_rgba(204,255,0,1)]"
                        title={`TMDB: ${rating.toFixed(1)}`}>
                        ★ {rating.toFixed(1)}
                    </div>
                    {lbBadge}
                </div>
            );
        }
        return null;
    };

    const PosterContent = (
        <div
            className="relative aspect-[2/3] overflow-hidden bg-zinc-900 w-full border-b-2 border-zinc-800 group-hover/card:border-primary transition-all duration-200 group-hover/card:shadow-[4px_4px_0px_0px_var(--primary)] group-hover/card:-translate-y-1 group-hover/card:-translate-x-1"
            title={[
                (vectorbox_score !== undefined && vectorbox_score !== null) ? `VectorBox Score: ${vectorbox_score.toFixed(1)} / 10` : null,
                rating ? `TMDB Rating: ${rating.toFixed(1)} / 10` : null,
                imdb_rating ? `IMDb: ${imdb_rating} / 10` : null,
                rotten_tomatoes_rating ? `Rotten Tomatoes: ${rotten_tomatoes_rating}%` : null,
                metacritic_rating ? `Metacritic: ${metacritic_rating} / 100` : null,
            ].filter(Boolean).join("\n")}
        >
            {!imageError && posterPath ? (
                <Image
                    src={getTMDBImageUrl(posterPath)}
                    alt={title}
                    fill
                    className={`object-cover transition-all duration-300 group-hover/card:grayscale group-hover/card:contrast-125 ${isFlipped ? 'opacity-10' : 'opacity-100'}`}
                    sizes="(max-width: 768px) 50vw, (max-width: 1200px) 33vw, 20vw"
                    priority={priority}
                    onError={() => setImageError(true)}
                />
            ) : (
                <div className="w-full h-full flex items-center justify-center bg-zinc-900 text-zinc-700">
                    <Film className="w-10 h-10 opacity-20" />
                </div>
            )}

            {/* Scanline Overlay */}
            <div className="absolute inset-0 bg-[linear-gradient(rgba(18,16,16,0)_50%,rgba(0,0,0,0.25)_50%),linear-gradient(90deg,rgba(255,0,0,0.06),rgba(0,255,0,0.02),rgba(0,0,255,0.06))] z-[5] bg-[length:100%_2px,3px_100%] pointer-events-none" />

            {renderBadge()}

            {/* Actions (Flip / Why) - Acid Style */}
            <div className="absolute bottom-0 left-0 right-0 p-2 flex justify-between items-end z-20 opacity-0 group-hover/card:opacity-100 transition-opacity">
                <div className="flex gap-2">
                    {/* Flip Button */}
                    <button
                        onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                            setFlipContent(flipContent === "synopsis" ? null : "synopsis");
                        }}
                        className="p-1.5 bg-black border border-primary text-primary hover:bg-primary hover:text-black transition-colors"
                        title={t("movie.synopsis")}
                    >
                        <RotateCcw className="w-4 h-4" />
                    </button>

                    {/* Why Button */}
                    {contributors && contributors.length > 0 && (
                        <button
                            onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                setFlipContent(flipContent === "contributors" ? null : "contributors");
                            }}
                            className="p-1.5 bg-black border border-zinc-500 text-zinc-400 hover:border-white hover:text-white transition-colors"
                            title={t("movie.why_recommended")}
                        >
                            <HelpCircle className="w-4 h-4" />
                        </button>
                    )}
                </div>

                <div className="flex items-center gap-2 text-[10px] font-mono text-white bg-black/60 px-2 py-1 rounded backdrop-blur-sm border border-white/10">
                    {year && <span>{year}</span>}
                    {runtime && (
                        <>
                            <span className="text-zinc-500">|</span>
                            <span>{runtime}m</span>
                        </>
                    )}
                </div>
            </div>

            {/* Back of Card - Acid Style */}
            {isFlipped && (
                <div className="absolute inset-0 bg-black p-4 flex flex-col z-30 border border-primary">
                    <div className="flex items-center justify-between mb-4 border-b border-zinc-800 pb-2">
                        <h5 className="font-mono font-bold text-xs text-primary uppercase tracking-widest">
                            {flipContent === "synopsis" ? "SYNOPSIS_DATA" : "LOGIC_TRACE"}
                        </h5>
                        <button
                            onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                setFlipContent(null);
                            }}
                            className="text-zinc-500 hover:text-primary"
                        >
                            <RotateCcw className="w-4 h-4 rotate-180" />
                        </button>
                    </div>

                    <div className="flex-grow overflow-y-auto custom-scrollbar">
                        {flipContent === "synopsis" ? (
                            <div className="space-y-4">
                                <p className="text-xs text-zinc-300 font-mono leading-relaxed">
                                    {overview || t("movie.no_synopsis")}
                                </p>
                            </div>
                        ) : (
                            <div className="space-y-4">
                                {contributors && contributors.map((c, i) => (
                                    <div key={i} className="space-y-1">
                                        <div className="flex justify-between text-[10px] font-mono text-zinc-300">
                                            <span className="truncate pr-2">{c.seed_title}</span>
                                            <span className="text-primary">{Math.round(c.contribution * 100)}%</span>
                                        </div>
                                        <div className="h-1 bg-zinc-900 w-full">
                                            <div
                                                className="h-full bg-primary"
                                                style={{ width: `${Math.min(100, c.contribution * 100)}%` }}
                                            />
                                        </div>
                                    </div>
                                ))}
                                {(!contributors || contributors.length === 0) && (
                                    <p className="text-xs text-zinc-500 font-mono">
                                        {t("movie.no_contributors")}
                                    </p>
                                )}
                            </div>
                        )}
                    </div>

                    {/* Streaming Providers (Back) */}
                    {providers.length > 0 && (
                        <div className="mt-auto pt-3 border-t border-zinc-800">
                            <div className="flex items-center gap-2 text-[10px] text-zinc-400 font-mono">
                                <Tv className="w-3 h-3 text-primary" />
                                <span className="truncate">{providers.join(", ")}</span>
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );

    const GridContent = (
        <div className="flex flex-col h-full bg-black">
            {PosterContent}

            {/* Grid Variant Info (Below Poster) */}
            {variant === "grid" && (
                <div className="p-3 flex flex-col flex-grow border-t border-zinc-800">
                    <h4 className="font-mono font-bold text-sm leading-tight mb-2 text-zinc-100 group-hover/card:text-primary transition-colors line-clamp-1 uppercase truncate">
                        {displayTitle}
                    </h4>

                    <div className="flex items-center gap-3 text-[10px] font-mono text-zinc-500 mb-3">
                        {year && <span className="bg-zinc-900 px-1 py-0.5 text-zinc-300">{year}</span>}
                        {runtime && <span>{runtime}m</span>}
                    </div>

                    {displayOverview && (
                        <p className="text-xs text-zinc-400 line-clamp-3 mb-3 flex-grow font-mono leading-relaxed opacity-70">
                            {displayOverview}
                        </p>
                    )}

                    <div className="mt-auto space-y-2 pt-2 border-t border-zinc-900">
                        {genres && genres.length > 0 && (
                            <div className="flex flex-wrap gap-1">
                                {genres.slice(0, 2).map(g => (
                                    <span key={g} className="text-[9px] px-1 py-0.5 border border-zinc-800 text-zinc-500 uppercase">
                                        {g}
                                    </span>
                                ))}
                            </div>
                        )}

                        {providers.length > 0 && !hideProvidersOnFront && (
                            <div className="flex items-center gap-1 text-[10px] text-zinc-600 font-mono">
                                <Tv className="w-3 h-3" />
                                <span className="truncate">{providers.join(", ")}</span>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );

    const Container = motion.div;

    const content = (
        <Container
            className={`group/card relative bg-black border border-zinc-800 hover:border-primary transition-colors cursor-pointer h-full ${className}`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            whileHover={{ y: -2 }}
            transition={{ duration: 0.1 }} // Snappy transition
            onClick={onClick}
        >
            {GridContent}
        </Container>
    );

    if (href) {
        return (
            <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="block h-full"
            >
                {content}
            </a>
        );
    }

    return content;
}
