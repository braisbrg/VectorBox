"use client";

import Image from "next/image";
import Link from "next/link";
import type { Contributor } from "@/types/feed";
import { getTMDBImageUrl } from "@/lib/api";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { useLanguage } from "@/components/language-provider";

export interface MovieCardProps {
    id: number;
    title: string;
    posterPath?: string | null;
    year?: number;
    runtime?: number;
    rating?: number;
    matchScore?: number | null;
    vectorbox_score?: number;
    title_es?: string;
    onInspect?: (id: number, contributors?: Contributor[]) => void;
    onReject?: (id: number) => void;
    onMarkWatched?: (id: number) => void;
    priority?: boolean;
    className?: string;
    contributors?: Contributor[];
    letterboxd_rating?: number;
    href?: string;
    isRejecting?: boolean;
    isMarkingWatched?: boolean;
    /** Override the HUD's right slot (default ★ rating) — e.g. provider name on watchlist. */
    hudRight?: string;
}

// Status badge tones (handoff: restricted to upcoming · #1 today · leaving soon)
const STATUS_TONE: Record<string, string> = {
    upcoming: "bg-accent-purple text-fg",
    "top-pick": "bg-primary text-primary-ink",
    "leaving-soon": "bg-warn text-black",
};

const fmtDur = (m?: number) => (m ? (m >= 60 ? `${Math.floor(m / 60)}H${String(m % 60).padStart(2, "0")}` : `${m}M`) : "TBA");

/**
 * Locked home-feed card (handoff V2 "data strip"): Q corner-tag tab top-right,
 * status badge top-left, stacked action triad on hover (right edge), a bordered
 * HUD strip (runtime · ★) under the poster, title below. Poster click → Letterboxd.
 */
export function MovieCard({
    id,
    title,
    posterPath,
    year,
    runtime,
    rating,
    matchScore,
    vectorbox_score,
    title_es,
    onInspect,
    onReject,
    onMarkWatched,
    priority = false,
    className = "",
    letterboxd_rating,
    href,
    isRejecting = false,
    isMarkingWatched = false,
    contributors,
    hudRight,
}: MovieCardProps) {
    const [imageError, setImageError] = useState(false);
    const { language } = useLanguage();

    const displayTitle = language === "es" && title_es ? title_es : title;
    // Q is the VectorBox QUALITY score — never fall back to match_score
    // (similarity), so unscored films (e.g. upcoming "On Your Radar") show no Q.
    const q = vectorbox_score;
    // HUD right slot defaults to the YEAR. The real Letterboxd ★ is passed via
    // `hudRight` by the "Popular on Letterboxd" row only (ratings live in the
    // inspector for everything else — no fake TMDB-derived stars on cards).

    // Cualquier contribuidor con chapa, no sólo `upcoming`: la fila "Leaving Soon"
    // mandaba la suya desde el primer día y se perdía aquí, en el último borde.
    const badged = contributors?.find((c) => c.release_badge);
    const status = badged?.release_badge
        ? { label: badged.release_badge, tone: badged.type === "leaving_soon" ? "leaving-soon" : "upcoming" }
        : undefined;

    const movieLink = href || `https://letterboxd.com/tmdb/${id}/`;

    const triad = [
        { key: "×", title: "Not interested", tone: "text-danger", onClick: () => onReject?.(id), loading: isRejecting, show: !!onReject },
        { key: "✓", title: "Watched", tone: "text-primary", onClick: () => onMarkWatched?.(id), loading: isMarkingWatched, show: !!onMarkWatched },
        // show only with a handler: the landing renders this card for logged-out
        // visitors, where an "i" that opens nothing is worse than no button.
        { key: "i", title: "Inspect", tone: "text-fg", onClick: () => onInspect?.(id, contributors), loading: false, show: !!onInspect },
    ].filter((b) => b.show);

    return (
        <div className={cn("group relative w-full", className)}>
            <Link
                href={movieLink}
                target="_blank"
                rel="noopener noreferrer"
                className="block"
                onClick={(e) => {
                    // Mobile (handoff 1C): card tap opens the inline inspector, not Letterboxd.
                    if (onInspect && typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches) {
                        e.preventDefault();
                        onInspect(id, contributors);
                    }
                }}
            >
                {/* poster */}
                <div className="poster-art relative aspect-[2/3] w-full overflow-hidden border border-border-2">
                    {!imageError && posterPath ? (
                        <Image
                            src={getTMDBImageUrl(posterPath)}
                            alt={title}
                            fill
                            className="object-cover transition-[filter] duration-150 group-hover:brightness-110"
                            sizes="(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 20vw"
                            priority={priority}
                            onError={() => setImageError(true)}
                        />
                    ) : (
                        <div className="flex size-full items-center justify-center font-display text-3xl tracking-[0.1em] text-fg/5">
                            VBX
                        </div>
                    )}

                    {/* Q corner-tag tab (top-right) — only when a real quality score exists.
                        Unreleased films are excluded explicitly: VBS is built from IMDb/TMDB
                        ratings, so with no votes yet the formula floors them at ~15 rather
                        than at null, and "On Your Radar" was painting Q15 on every card as if
                        the film were bad. It isn't rated, which is a different thing. */}
                    {q != null && q > 0 && !status && (
                        <span className="absolute right-0 top-0 z-10 bg-primary px-[7px] py-[3px] font-display text-[11px] font-bold leading-none tracking-[0.04em] text-primary-ink">
                            Q{Math.round(q)}
                        </span>
                    )}

                    {/* status badge (top-left) */}
                    {status && (
                        <span
                            className={cn(
                                "absolute left-1.5 top-1.5 z-10 px-1.5 py-0.5 font-display text-[9px] font-bold uppercase leading-none tracking-[0.08em]",
                                STATUS_TONE[status.tone]
                            )}
                        >
                            {status.label}
                        </span>
                    )}

                    {/* stacked action triad (hover, right edge). z-20, not z-30+ —
                        this card sits below a sticky topbar (z-30) in the same
                        stacking context; matching or exceeding it lets a hovered
                        card's triad paint over the topbar on scroll. */}
                    {/* Sin acciones no hay barra. Se pintaba siempre, así que en la
                        landing —donde esta tarjeta no recibe ningún callback— el
                        hover mostraba una franja negra de 34px vacía: parecía
                        interactiva y no hacía nada. Los botones ya se filtraban por
                        `show`; lo que faltaba era filtrar el contenedor. */}
                    {triad.length > 0 && (
                    <div className="absolute inset-y-0 right-0 z-20 flex w-[34px] flex-col border-l border-border-2 bg-black/80 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                        {triad.map((b, i) => (
                            <button
                                key={b.title}
                                title={b.title}
                                disabled={b.loading}
                                onClick={(e) => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    b.onClick();
                                }}
                                className={cn(
                                    "flex flex-1 items-center justify-center font-display text-sm transition-colors hover:bg-white/10 disabled:opacity-40",
                                    i > 0 && "border-t border-border-2",
                                    b.tone
                                )}
                            >
                                {b.loading ? "·" : b.key}
                            </button>
                        ))}
                    </div>
                    )}
                </div>

                {/* HUD strip — runtime · (year | ★ letterboxd on the popular row) */}
                <div className="flex items-center justify-between border border-t-0 border-border-2 bg-bg-3 px-[7px] py-[5px] font-display text-[9px] tracking-[0.04em] md:text-[10px]">
                    <span className="text-fg-2">{fmtDur(runtime)}</span>
                    <span className="text-fg-2">{hudRight ?? (year || "????")}</span>
                </div>

                {/* title */}
                <div className="mt-1.5 truncate font-mono text-[11px] leading-tight text-fg transition-colors group-hover:text-primary md:text-[12px]">
                    {displayTitle}
                </div>
            </Link>
        </div>
    );
}
