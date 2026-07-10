"use client";

// Mobile feed inspector — handoff decision 1C: a compact lime-bordered panel
// injected INLINE below the tapped card's carousel row (not a bottom sheet).
// Deliberately smaller than the desktop DATA_INSPECTOR (per prototype
// renderInlineInsp): poster · title/meta/Q · why → · 3 actions.

import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { m, AnimatePresence, useReducedMotion } from "framer-motion";
import { X, Check, Plus, Loader2 } from "lucide-react";
import { FeedItem, getTMDBImageUrl, setWatchlist, markWatched } from "@/lib/api";
import { scheduleFeedInvalidation } from "@/lib/feed-invalidation";
import { useLanguage } from "@/components/language-provider";

interface FeedInlineInspectorProps {
    movie: FeedItem;
    onClose: () => void;
}

export function FeedInlineInspector({ movie, onClose }: FeedInlineInspectorProps) {
    const queryClient = useQueryClient();
    const reduceMotion = useReducedMotion();
    const { language, t } = useLanguage();
    const [onWatchlist, setOnWatchlist] = useState(false);
    const [busy, setBusy] = useState<"watchlist" | "seen" | null>(null);

    const q = movie.vectorbox_score ? Math.round(movie.vectorbox_score) : null;
    const displayTitle = language === "es" && movie.title_es ? movie.title_es : movie.title;
    const displayOverview = language === "es" && movie.overview_es ? movie.overview_es : movie.overview;

    const toggleWatchlist = async () => {
        if (busy) return;
        setBusy("watchlist");
        try {
            await setWatchlist(movie.id, !onWatchlist);
            setOnWatchlist((v) => !v);
        } catch (e) {
            console.error("watchlist toggle failed", e);
        } finally {
            setBusy(null);
        }
    };

    const markSeen = async () => {
        if (busy) return;
        setBusy("seen");
        try {
            await markWatched(movie.id);
            scheduleFeedInvalidation(queryClient);
            onClose();
        } catch (e) {
            console.error("mark watched failed", e);
        } finally {
            setBusy(null);
        }
    };

    return (
        <AnimatePresence>
            <m.div
                initial={reduceMotion ? false : { height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={reduceMotion ? undefined : { height: 0, opacity: 0 }}
                transition={{ duration: reduceMotion ? 0 : 0.18 }}
                className="overflow-hidden lg:hidden"
            >
                <div className="mx-1 mt-2 border border-primary bg-bg-2 p-3 font-mono">
                    <div className="flex gap-3">
                        <div className="poster-art relative aspect-[2/3] w-20 shrink-0 overflow-hidden border border-border-2">
                            {movie.poster_url && (
                                <Image src={getTMDBImageUrl(movie.poster_url, "w185")} alt={movie.title} fill sizes="80px" className="object-cover" />
                            )}
                        </div>
                        <div className="min-w-0 flex-1">
                            <div className="flex items-start justify-between gap-2">
                                <h3 className="font-display text-sm uppercase leading-tight text-fg">{displayTitle}</h3>
                                <button onClick={onClose} aria-label="Close" className="shrink-0 p-0.5 text-fg-3 hover:text-primary">
                                    <X size={14} />
                                </button>
                            </div>
                            <div className="mt-1 flex items-center gap-2 text-[10px] text-fg-3">
                                <span>{movie.year || "????"}</span>
                                <span>·</span>
                                <span>{movie.runtime ? `${movie.runtime} min` : "?? min"}</span>
                                {q != null && q > 0 && <span className="ml-auto font-display text-sm text-primary">Q{q}</span>}
                            </div>
                            {displayOverview && (
                                <p className="mt-2 line-clamp-3 text-[10px] normal-case leading-relaxed text-fg-2">{displayOverview}</p>
                            )}
                            <Link
                                href={`/why/${movie.id}`}
                                className="mt-2 inline-block text-[10px] uppercase tracking-wider text-primary hover:underline"
                            >
                                {t("inline.why")}
                            </Link>
                        </div>
                    </div>

                    {/* Actions — handoff: + watchlist · ✓ seen · i (full page) */}
                    <div className="mt-3 grid grid-cols-3 gap-1.5">
                        <button
                            onClick={toggleWatchlist}
                            disabled={busy !== null}
                            className="flex items-center justify-center gap-1.5 border border-border-2 py-2 text-[10px] uppercase tracking-wide text-fg-2 transition-colors hover:border-primary hover:text-primary disabled:opacity-50"
                        >
                            {busy === "watchlist" ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
                            {onWatchlist ? t("inline.saved") : t("inline.watchlist")}
                        </button>
                        <button
                            onClick={markSeen}
                            disabled={busy !== null}
                            className="flex items-center justify-center gap-1.5 border border-border-2 py-2 text-[10px] uppercase tracking-wide text-fg-2 transition-colors hover:border-primary hover:text-primary disabled:opacity-50"
                        >
                            {busy === "seen" ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
                            {t("inline.seen")}
                        </button>
                        <Link
                            href={`/movie/${movie.id}`}
                            onClick={onClose}
                            className="flex items-center justify-center gap-1.5 border border-border-2 py-2 text-[10px] uppercase tracking-wide text-fg-2 transition-colors hover:border-primary hover:text-primary"
                        >
                            {t("inline.full_page")}
                        </Link>
                    </div>
                </div>
            </m.div>
        </AnimatePresence>
    );
}
