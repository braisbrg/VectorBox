"use client";

import Image from "next/image";
import Link from "next/link";
import { X, Check, Loader2 } from "lucide-react";
import { FeedItem, getTMDBImageUrl } from "@/lib/api";
import { WhyThisFilm } from "@/components/why-this-film";
import { BottomSheet } from "@/components/shell/bottom-sheet";
import { useLanguage } from "@/components/language-provider";

interface MobileInspectorProps {
    movie: FeedItem | null;
    sectionId?: string;
    onClose: () => void;
    onMarkWatched?: (tmdbId: number) => void;
    onReject?: (tmdbId: number) => void;
    actionLoading?: "watched" | "rejected" | null;
}

/** Mobile inspector — bottom sheet mirroring the desktop right-console DATA_INSPECTOR. */
export function MobileInspector({ movie, sectionId, onClose, onMarkWatched, onReject, actionLoading }: MobileInspectorProps) {
    const { language, t } = useLanguage();
    const displayTitle = language === "es" && movie?.title_es ? movie.title_es : movie?.title;
    const displayOverview = language === "es" && movie?.overview_es ? movie.overview_es : movie?.overview;
    return (
        <BottomSheet open={!!movie} onClose={onClose} ariaLabel="Data inspector">
            {movie && (
                <>
                        <div className="flex items-center justify-between border-b border-border-2 px-5 pb-3">
                            <div className="flex items-center gap-2">
                                <span className="trident-mark"><b /><b /><b /></span>
                                <span className="font-bold uppercase tracking-widest text-primary">DATA_INSPECTOR</span>
                            </div>
                            <button onClick={onClose} className="p-1 text-fg-3 hover:text-primary" aria-label="Close">
                                <X size={18} />
                            </button>
                        </div>

                        <div className="space-y-5 overflow-y-auto px-5 py-5 pb-12">
                            <div className="flex gap-4">
                                <div className="poster-art relative aspect-[2/3] w-28 shrink-0 overflow-hidden border border-border-2">
                                    {movie.poster_url && (
                                        <Image src={getTMDBImageUrl(movie.poster_url, "w342")} alt={movie.title} fill sizes="112px" className="object-cover" />
                                    )}
                                </div>
                                <div className="min-w-0 flex-1">
                                    <h2 className="font-display text-lg uppercase leading-tight tracking-tight text-fg">{displayTitle}</h2>
                                    <div className="mt-1 flex gap-3 text-[10px] font-bold text-fg-3">
                                        <span>{movie.year || "????"}</span>
                                        <span>{movie.runtime ? `${movie.runtime} MIN` : "?? MIN"}</span>
                                    </div>
                                    <div className="mt-3 flex items-baseline gap-3">
                                        <span className="font-display text-2xl text-primary">Q{Math.round(movie.vectorbox_score || movie.match_score || 0)}</span>
                                        {movie.letterboxd_rating != null && <span className="text-xs text-fg-2">★ {movie.letterboxd_rating.toFixed(1)}</span>}
                                    </div>
                                </div>
                            </div>

                            {displayOverview && (
                                <p className="text-[11px] normal-case leading-relaxed text-fg-2">{displayOverview}</p>
                            )}

                            {movie.streaming_providers && movie.streaming_providers.length > 0 && (
                                <div className="space-y-2">
                                    <span className="block border-b border-border pb-2 text-[10px] uppercase tracking-widest text-fg-3">
                                        {">"} {t("insp.available_on")}
                                    </span>
                                    <div className="flex flex-wrap gap-1.5">
                                        {movie.streaming_providers.map((p) => (
                                            <span key={p} className="border border-primary bg-primary/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-primary">
                                                {p}
                                            </span>
                                        ))}
                                    </div>
                                    {/* TMDB API ToS: JustWatch credit wherever provider data renders */}
                                    <p className="text-[9px] normal-case text-fg-3/70">{t("insp.justwatch")}</p>
                                </div>
                            )}

                            <div className="border-t border-border pt-4">
                                <WhyThisFilm tmdbId={movie.id} contributors={movie.contributors} sectionId={sectionId} />
                            </div>

                            <Link
                                href={`/movie/${movie.id}`}
                                onClick={onClose}
                                className="block w-full border border-border-2 py-2.5 text-center font-mono text-[10px] uppercase tracking-[0.1em] text-fg-2 transition-colors hover:border-primary hover:text-primary"
                            >
                                {t("ql.full_page")}
                            </Link>

                            <div className="space-y-2 pt-2">
                                <button
                                    onClick={() => onMarkWatched?.(movie.id)}
                                    disabled={actionLoading !== null && actionLoading !== undefined}
                                    className="flex w-full items-center justify-center gap-2 border border-border-2 py-3 text-fg-2 transition-colors hover:border-primary hover:text-primary disabled:opacity-50"
                                >
                                    {actionLoading === "watched" ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                                    <span className="text-[10px] font-bold uppercase tracking-widest">{">"} {t("insp.mark_watched")}</span>
                                </button>
                                <button
                                    onClick={() => onReject?.(movie.id)}
                                    disabled={actionLoading !== null && actionLoading !== undefined}
                                    className="flex w-full items-center justify-center gap-2 border border-border-2 py-3 text-fg-3 transition-colors hover:border-danger hover:text-danger disabled:opacity-50"
                                >
                                    {actionLoading === "rejected" ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
                                    <span className="text-[10px] font-bold uppercase tracking-widest">{">"} {t("insp.not_interested")}</span>
                                </button>
                            </div>
                        </div>
                </>
            )}
        </BottomSheet>
    );
}
