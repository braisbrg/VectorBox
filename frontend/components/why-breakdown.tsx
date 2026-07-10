"use client";

// Shared why-this primitives — exact ports of handoff_items/screens-v3/why-this.jsx.
// D1 (rail) composes them in why-this-film.tsx, D2 (dossier) and D3 (/why page)
// compose them in their pages.

import Image from "next/image";
import Link from "next/link";
import { getTMDBImageUrl, WhyBreakdown, WhyNeighbor, WhyAnchor } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";
import { cn } from "@/lib/utils";

export type Trident = { vibe: number; auteur: number; gems: number };

// ============= TRIDENT CONTRIBUTION VIZ =============
export function TridentBar({ trident, height = 10, showLabels = true }: { trident: Trident; height?: number; showLabels?: boolean }) {
    return (
        <div>
            <div className="flex gap-px border border-border-2" style={{ height }}>
                <div style={{ flex: trident.vibe }} className="bg-primary" />
                <div style={{ flex: trident.auteur }} className="bg-accent-purple" />
                <div style={{ flex: trident.gems }} className="bg-fg-2" />
            </div>
            {showLabels && (
                // Natural-width labels (proportional flex crushed low-weight labels
                // into overlap); `vibe 62%` reads as a score, zero axes hidden.
                <div className="mt-1.5 flex flex-wrap gap-x-3 font-display text-[9px] lowercase tracking-[0.05em]">
                    {[
                        { k: "vibe", v: trident.vibe, c: "text-primary" },
                        { k: "auteur", v: trident.auteur, c: "text-accent-purple" },
                        { k: "gems", v: trident.gems, c: "text-fg-2" },
                    ]
                        .filter((x) => Math.round(x.v * 100) > 0)
                        .map((x) => (
                            <span key={x.k} className={x.c}>
                                {x.k} {Math.round(x.v * 100)}%
                            </span>
                        ))}
                </div>
            )}
        </div>
    );
}

// ============= POSTER MICRO =============
function PMicro({ posterUrl, title, w = 46 }: { posterUrl?: string | null; title?: string; w?: number }) {
    return (
        <div
            className="poster-art relative shrink-0 overflow-hidden border border-border-2"
            style={{ width: w, height: w * 1.5 }}
            title={title}
        >
            {posterUrl && (
                <Image src={getTMDBImageUrl(posterUrl, "w154")} alt={title || ""} fill sizes={`${w}px`} className="object-cover" />
            )}
        </div>
    );
}

/** Dominant-axis one-liner (port of the prototype's reasonByDom).
 *  Pass `t` from useLanguage to localize; defaults to the EN strings. */
const idKey = (k: string) => k; // identity fallback → keys double as EN? no: see EN_WHY below
const EN_WHY: Record<string, string> = {
    "why.vibe_1": "drove this — close vector neighbor to",
    "why.auteur_1": "drove this —",
    "why.auteur_2": "aligns with your cluster",
    "why.gems_1": "drove this — under-watched (high Q-percentile) inside cluster",
};
export function dominantReason(why: WhyBreakdown, t?: (k: string) => string): React.ReactNode {
    const tr = (k: string) => (t ? t(k) : (EN_WHY[k] ?? idKey(k)));
    const { trident, neighbors, director, cluster } = why;
    const dom =
        trident.vibe >= trident.auteur && trident.vibe >= trident.gems
            ? "vibe"
            : trident.auteur >= trident.gems
              ? "auteur"
              : "gems";
    const cl = cluster ? `#${cluster.id}` : "—";
    if (dom === "vibe" && neighbors[0]) {
        return (
            <>
                <b className="text-primary">vibe</b> {tr("why.vibe_1")}{" "}
                <b className="text-fg">{neighbors[0].title.toLowerCase()}</b> (d={neighbors[0].dist.toFixed(2)}).
            </>
        );
    }
    if (dom === "auteur" && director) {
        return (
            <>
                <b className="text-accent-purple">auteur</b> {tr("why.auteur_1")} <b className="text-fg">{director}</b>{" "}
                {tr("why.auteur_2")} {cl}.
            </>
        );
    }
    return (
        <>
            <b className="text-primary">gems</b> {tr("why.gems_1")} {cl}.
        </>
    );
}

// ============= NEIGHBOR STRIP (shared horizontal poster row) =============
// ONE implementation for every horizontal "nearest neighbours" surface (rail D1
// mini-row, dossier D2 panel) — posters Link to each film's own dossier. The
// vertical NeighborList (D3, /why) stays separate: different density (adds the
// similarity bars).
export function NeighborStrip({
    neighbors,
    w = 80,
    max,
    showMeta = true,
}: {
    neighbors: WhyNeighbor[];
    /** poster width in px (height = 1.5w) */
    w?: number;
    /** cap the number of posters (rail mini-row uses 4) */
    max?: number;
    /** title + year under the poster (mini-row hides them) */
    showMeta?: boolean;
}) {
    if (!neighbors.length) return null;
    const shown = max ? neighbors.slice(0, max) : neighbors;
    return (
        <div className="flex gap-3 overflow-x-auto pb-1 scrollbar-hide">
            {shown.map((n) => (
                <Link
                    key={n.tmdb_id}
                    href={`/movie/${n.tmdb_id}`}
                    className="group block shrink-0"
                    style={{ width: w }}
                    title={`${n.title} · d=${n.dist.toFixed(2)}`}
                >
                    <div
                        className="poster-art relative border border-border-2 transition-colors group-hover:border-primary"
                        style={{ width: w, height: Math.round(w * 1.5) }}
                    >
                        {n.poster_url && (
                            <Image src={getTMDBImageUrl(n.poster_url, "w154")} alt={n.title} fill sizes={`${w}px`} className="object-cover" />
                        )}
                        <div className="absolute right-0 top-0 bg-primary px-1 py-px font-display text-[9px] font-bold text-primary-ink">
                            d{n.dist.toFixed(2)}
                        </div>
                    </div>
                    {showMeta && (
                        <>
                            <div className="mt-1 line-clamp-2 font-mono text-[10px] leading-tight text-fg transition-colors group-hover:text-primary">{n.title}</div>
                            <div className="font-mono text-[9px] text-fg-3">{n.year}</div>
                        </>
                    )}
                </Link>
            ))}
        </div>
    );
}

// ============= NEIGHBOR MINI-ROW (D1) =============
export function NeighborMiniRow({ neighbors }: { neighbors: WhyNeighbor[] }) {
    const { t } = useLanguage();
    if (!neighbors.length) return null;
    return (
        <div>
            <div className="eyebrow mb-1.5">{t("why.nearest")}</div>
            <NeighborStrip neighbors={neighbors} w={42} max={4} showMeta={false} />
        </div>
    );
}

// ============= CLUSTER PILL (D1) =============
export function ClusterPill({ cluster }: { cluster: WhyBreakdown["cluster"] }) {
    const { t } = useLanguage();
    if (!cluster) return null;
    return (
        <div className="border border-dashed border-border-2 bg-bg-3 px-2 py-1.5">
            <div className="font-display text-[9px] uppercase tracking-[0.1em] text-fg-3">{t("why.cluster_pill")} #{cluster.id}</div>
            <div className="mt-0.5 font-mono text-[11px] text-fg-2">{cluster.name}</div>
        </div>
    );
}

// ============= CONTRIBUTION CARDS (D2/D3) =============
export function ContributionCards({ why }: { why: WhyBreakdown }) {
    const q = why.q ?? 0;
    const cards = [
        {
            k: "VIBE",
            pct: why.trident.vibe,
            border: "border-t-primary",
            text: "text-primary",
            note: (
                <>
                    vector neighbor to films you love{why.neighbors[0] ? <> · nearest: <b>{why.neighbors[0].title}</b></> : null}.
                </>
            ),
        },
        {
            k: "AUTEUR",
            pct: why.trident.auteur,
            border: "border-t-accent-purple",
            text: "text-accent-purple",
            note: why.auteur ? (
                <>
                    <b>{why.auteur.name}</b> — {why.auteur.note}.
                </>
            ) : (
                <>no auteur overlap with your 4★+ films.</>
            ),
        },
        {
            k: "GEMS",
            pct: why.trident.gems,
            border: "border-t-fg-2",
            text: "text-fg-2",
            note: why.gem ? <>{why.gem.note}.</> : <>widely seen — no gem boost.</>,
        },
    ];
    return (
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {cards.map((c) => (
                <div key={c.k} className={cn("border-t-2 bg-bg-3 px-3 py-2.5", c.border)}>
                    <div className={cn("font-display text-[9px] uppercase tracking-[0.15em]", c.text)}>
                        {c.k} · {Math.round(c.pct * 100)}%
                    </div>
                    <div className="mt-1 font-display text-2xl tracking-[-0.02em] text-fg">{Math.round(c.pct * q)}</div>
                    <div className="mt-1 font-mono text-[10px] leading-snug text-fg-2">{c.note}</div>
                </div>
            ))}
        </div>
    );
}

// ============= ANCHOR LIST (D2/D3) =============
export function AnchorList({ anchors, posterW = 28 }: { anchors: WhyAnchor[]; posterW?: number }) {
    if (!anchors.length) return <div className="font-mono text-[11px] text-fg-3">no rated anchors found for this film.</div>;
    return (
        <div className="flex flex-col">
            {anchors.map((a, i) => (
                <div key={a.tmdb_id} className={cn("flex items-center gap-2.5 py-2", i > 0 && "border-t border-dashed border-border-2")}>
                    <PMicro posterUrl={a.poster_url} title={a.title} w={posterW} />
                    <div className="min-w-0 flex-1">
                        <div className="truncate font-mono text-[11px] text-fg">
                            {a.title} <span className="text-fg-3">· {a.year}</span>
                        </div>
                        <div className="mt-px font-mono text-[10px] text-fg-3">{a.reason}</div>
                    </div>
                    <div className="font-display text-[10px] tracking-[0.05em] text-primary">
                        {a.rating != null ? `${a.rating}★` : "—"}
                    </div>
                    <div className="h-1 w-[60px] border border-border-2 bg-bg-3">
                        <div className="h-full bg-primary" style={{ width: `${a.weight * 100}%` }} />
                    </div>
                    <div className="w-8 text-right font-display text-[9px] text-fg-2">{Math.round(a.weight * 100)}%</div>
                </div>
            ))}
        </div>
    );
}

// ============= NEIGHBOR LIST (D3, with distance bars) =============
export function NeighborList({ neighbors }: { neighbors: WhyNeighbor[] }) {
    if (!neighbors.length) return <div className="font-mono text-[11px] text-fg-3">no embedding neighbors available.</div>;
    return (
        <div className="flex flex-col">
            {neighbors.map((n, i) => (
                // Each neighbour navigates to its own dossier (user 2026-07-10).
                <Link
                    key={n.tmdb_id}
                    href={`/movie/${n.tmdb_id}`}
                    className={cn("group flex items-center gap-3 py-2 transition-colors hover:bg-bg-2", i > 0 && "border-t border-dashed border-border-2")}
                >
                    <div className="w-[18px] font-display text-[10px] text-fg-3">{String(i + 1).padStart(2, "0")}</div>
                    <PMicro posterUrl={n.poster_url} title={n.title} w={32} />
                    <div className="min-w-0 flex-1">
                        <div className="truncate font-mono text-xs text-fg transition-colors group-hover:text-primary">{n.title}</div>
                        <div className="font-mono text-[10px] text-fg-3">{n.year}</div>
                    </div>
                    <div className="w-20">
                        <div className="relative h-1 border border-border-2 bg-bg-3">
                            <div className="absolute inset-y-0 left-0 bg-primary" style={{ width: `${Math.max(0, 1 - n.dist) * 100}%` }} />
                        </div>
                        <div className="mt-0.5 text-right font-display text-[9px] text-fg-2">d {n.dist.toFixed(2)}</div>
                    </div>
                </Link>
            ))}
        </div>
    );
}

// ============= CLUSTER CARD (D2/D3) =============
export function ClusterCard({ cluster }: { cluster: WhyBreakdown["cluster"] }) {
    if (!cluster) return null;
    return (
        <div className="border border-primary bg-bg-2 p-3.5">
            <div className="font-display text-[9px] uppercase tracking-[0.2em] text-primary">cluster #{cluster.id}</div>
            <div className="mt-1.5 font-display text-lg tracking-[-0.01em] text-fg">{cluster.name}</div>
            <div className="mt-3 grid grid-cols-2 gap-3">
                <div>
                    <div className="font-display text-2xl text-primary">{cluster.movie_count}</div>
                    <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-fg-3">films in your library</div>
                </div>
                <div>
                    <div className="font-display text-2xl text-fg">{cluster.avg_rating?.toFixed(1) ?? "—"}</div>
                    <div className="font-mono text-[9px] uppercase tracking-[0.1em] text-fg-3">avg ★</div>
                </div>
            </div>
        </div>
    );
}

/** "see full breakdown →" CTA shared by D1/D2. */
export function BreakdownCta({ tmdbId, className }: { tmdbId: number; className?: string }) {
    const { t } = useLanguage();
    return (
        <Link
            href={`/why/${tmdbId}`}
            className={cn(
                "block w-full border border-border-2 bg-transparent px-2 py-1.5 text-center font-mono text-[10px] uppercase tracking-[0.1em] text-fg-2 transition-colors hover:border-primary hover:text-primary",
                className
            )}
        >
            {t("why.full_breakdown")}
        </Link>
    );
}
