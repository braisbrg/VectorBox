"use client";

import { useQuery } from "@tanstack/react-query";
import type { Contributor } from "@/types/feed";
import { getWhy } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";
import {
    TridentBar,
    NeighborMiniRow,
    ClusterPill,
    BreakdownCta,
    dominantReason,
} from "@/components/why-breakdown";

function sectionReason(sectionId?: string): string {
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
}

const WEIGHTED = ["vibe", "auteur", "crowd", "cult_actor", "watchlist"];

/** Legacy contributor-based rendering — fallback when /why is unavailable. */
function ContributorFallback({ contributors, sectionId }: { contributors?: Contributor[]; sectionId?: string }) {
    const { t } = useLanguage();
    if (!contributors || contributors.length === 0) {
        return (
            <p className="mt-2 text-[10px] uppercase text-fg-2">
                <span className="mr-2 text-primary opacity-80">LOGIC:</span>
                {sectionReason(sectionId)}
            </p>
        );
    }
    return (
        <div className="space-y-2">
            {contributors.map((c) => (
                <div key={`${c.type}:${c.seed_title ?? ""}:${c.director ?? c.actor ?? c.cluster_name ?? ""}`} className="space-y-0.5">
                    {c.type === "anchor" && (
                        <>
                            <p className="text-xs text-primary">{t("why.similar_to")} {c.seed_title} ({c.seed_year})</p>
                            <p className="text-[11px] text-fg-3">
                                {t("why.you_rated")} {c.seed_rating}★ · {t("why.similarity")} {Math.round((c.similarity ?? 0) * 100)}%
                            </p>
                        </>
                    )}
                    {c.type === "cluster" && (
                        <>
                            <p className="text-xs text-primary">{t("why.matches_cluster")} {c.cluster_name}</p>
                            {c.medoid_title && <p className="text-[11px] text-fg-3">{t("why.anchored_to")} {c.medoid_title}</p>}
                        </>
                    )}
                    {c.type === "upcoming" && (
                        <div className="space-y-0.5">
                            <p className="text-xs text-primary">{c.release_badge}</p>
                            {c.release_note && <p className="text-[11px] text-fg-3">{c.release_note}</p>}
                        </div>
                    )}
                    {/* El inspector es el sitio con espacio para decir de QUÉ servicio
                        se va: en la carátula sólo cabe el nombre y los días. */}
                    {c.type === "leaving_soon" && (
                        <div className="space-y-0.5">
                            <p className="text-xs text-warn">{c.label}</p>
                            {c.release_note && <p className="text-[11px] text-fg-3">{c.release_note}</p>}
                        </div>
                    )}
                    {WEIGHTED.includes(c.type) && (
                        <div className="flex items-center justify-between gap-2">
                            <p className="text-xs text-primary">{c.label}</p>
                            {contributors.filter((x) => WEIGHTED.includes(x.type)).length > 1 && (
                                <p className="shrink-0 text-[11px] text-fg-3">{Math.round((c.score ?? 0) * 100)}%</p>
                            )}
                        </div>
                    )}
                </div>
            ))}
        </div>
    );
}

interface WhyThisFilmProps {
    tmdbId: number;
    contributors?: Contributor[];
    sectionId?: string;
}

/**
 * Why-this · DENSITY 1 (compact rail) — handoff why-this.jsx Why_V1_Compact.
 * Trident bar → dominant-axis one-liner → cluster pill → nearest-neighbor
 * mini-row → "see full breakdown →". Falls back to contributor rendering
 * when the /why endpoint has no data for this user/film.
 */
export function WhyThisFilm({ tmdbId, contributors, sectionId }: WhyThisFilmProps) {
    const { t } = useLanguage();
    const { data: why } = useQuery({
        queryKey: ["why", tmdbId],
        queryFn: () => getWhy(tmdbId),
        staleTime: 10 * 60 * 1000,
        retry: 1,
    });

    return (
        <div className="space-y-3">
            <p className="font-display text-[11px] uppercase tracking-[0.15em] text-primary">{t("why.title")}</p>
            {why ? (
                <>
                    <TridentBar trident={why.trident} />
                    <div className="font-mono text-[11px] leading-relaxed text-fg">{dominantReason(why, t)}</div>
                    <ClusterPill cluster={why.cluster} />
                    <NeighborMiniRow neighbors={why.neighbors} />
                    <BreakdownCta tmdbId={tmdbId} />
                </>
            ) : (
                <ContributorFallback contributors={contributors} sectionId={sectionId} />
            )}
        </div>
    );
}
