"use client";

// Why-this · DENSITY 3 — full-page recommendation breakdown with live
// re-weighting (handoff why-this.jsx Why_V3_FullPage). The presets re-weight
// the *displayed* composition client-side so users can see how the trident
// would shift under a different profile.

import { use, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getWhy } from "@/lib/api";
import { useContextualBack } from "@/lib/use-back";
import { SubScreenHeader } from "@/components/shell/sub-screen-header";
import { useLanguage } from "@/components/language-provider";
import { TridentBar, AnchorList, NeighborList, ClusterCard, Trident } from "@/components/why-breakdown";
import { cn } from "@/lib/utils";

const fmtDur = (m?: number) => (m ? `${m}min` : "");

type PresetKey = "as_ranked" | "vibe" | "auteur" | "gems" | "balanced";

// "as ranked" ({1,1,1}) is the DEFAULT view — it shows the film's real server
// composition unchanged. The three "-heavy" presets triple one axis as a what-if;
// balanced is the equal-thirds sentinel. (The old code mislabeled the {1,1,1}
// default as "vibe-heavy" — factually wrong: the server ranks vibe+auteur equal,
// gems lighter — and had no genuine vibe-heavy option.)
const PRESETS: { k: PresetKey; labelKey: string; mult: Trident }[] = [
    { k: "as_ranked", labelKey: "why2.preset_as_ranked", mult: { vibe: 1, auteur: 1, gems: 1 } },
    { k: "vibe", labelKey: "why2.preset_vibe", mult: { vibe: 3, auteur: 1, gems: 1 } },
    { k: "auteur", labelKey: "why2.preset_auteur", mult: { vibe: 1, auteur: 3, gems: 1 } },
    { k: "gems", labelKey: "why2.preset_gems", mult: { vibe: 1, auteur: 1, gems: 3 } },
    { k: "balanced", labelKey: "why2.preset_balanced", mult: { vibe: 0, auteur: 0, gems: 0 } }, // sentinel → equal thirds
];

function reweight(base: Trident, preset: PresetKey): Trident {
    if (preset === "balanced") return { vibe: 1 / 3, auteur: 1 / 3, gems: 1 / 3 };
    const m = PRESETS.find((p) => p.k === preset)!.mult;
    const raw = { vibe: base.vibe * m.vibe, auteur: base.auteur * m.auteur, gems: base.gems * m.gems };
    const total = raw.vibe + raw.auteur + raw.gems || 1;
    return { vibe: raw.vibe / total, auteur: raw.auteur / total, gems: raw.gems / total };
}

export default function WhyPage({ params }: { params: Promise<{ id: string }> }) {
    const { id } = use(params);
    const tmdbId = Number(id);
    const goBack = useContextualBack();
    const { t } = useLanguage();
    const [preset, setPreset] = useState<PresetKey>("as_ranked");

    const { data: why, isLoading } = useQuery({
        queryKey: ["why", tmdbId],
        queryFn: () => getWhy(tmdbId),
        staleTime: 10 * 60 * 1000,
    });

    const trident = useMemo(() => (why ? reweight(why.trident, preset) : null), [why, preset]);

    if (isLoading) {
        return (
            <div className="flex items-center justify-center py-32 font-mono text-xs uppercase tracking-widest text-fg-3">
                {t("why2.computing")}
            </div>
        );
    }
    if (!why || !trident) {
        return (
            <div className="py-32 text-center font-mono text-xs uppercase tracking-widest text-fg-3">
                {t("why2.none")} ·{" "}
                <button onClick={goBack} className="text-primary hover:underline">
                    {t("why2.back")}
                </button>
            </div>
        );
    }

    const q = why.q ?? 0;
    const rows = [
        { k: "vibe", cls: "border-l-primary", text: "text-primary", v: trident.vibe, label: t("why2.vibe_row") },
        {
            k: "auteur",
            cls: "border-l-accent-purple",
            text: "text-accent-purple",
            v: trident.auteur,
            label: why.auteur ? `${why.auteur.name.toLowerCase()} · ${why.auteur.note}` : t("why2.no_auteur"),
        },
        {
            k: "gems",
            cls: "border-l-fg-2",
            text: "text-fg-2",
            v: trident.gems,
            label: why.gem ? why.gem.note : t("why2.no_gem"),
        },
    ];

    return (
        <div className="space-y-6 pb-10 pt-4">
            {/* full-width back row on mobile (sub-screen), compact button ≥lg */}
            <SubScreenHeader crumb="crumbs.why" fallback="/feed" />
            <button
                onClick={goBack}
                className="hidden border border-border-2 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-fg-2 transition-colors hover:border-primary hover:text-primary lg:inline-block"
            >
                {t("ui.back")}
            </button>

            {/* page header */}
            <div className="flex flex-wrap items-end justify-between gap-4 border-b-2 border-primary pb-4">
                <div>
                    <div className="font-display text-[9px] uppercase tracking-[0.2em] text-primary">{t("why2.breakdown")}</div>
                    <h1 className="mt-1.5 font-display text-4xl uppercase tracking-[-0.02em] text-fg md:text-5xl">{why.title}</h1>
                    <div className="mt-1 font-mono text-[11px] text-fg-3">
                        {[why.director, why.year, fmtDur(why.runtime)].filter(Boolean).join(" · ")}
                    </div>
                </div>
                <div className="text-right">
                    <div className="font-display text-5xl font-bold leading-none tracking-[-0.04em] text-primary md:text-6xl">
                        Q{q ? Math.round(q) : "—"}
                    </div>
                    {why.rank != null && why.rank_pool != null && (
                        <div className="mt-1 font-mono text-[10px] tracking-[0.1em] text-fg-3">
                            RANK {why.rank}/{why.rank_pool}
                        </div>
                    )}
                </div>
            </div>

            {/* two columns */}
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
                {/* LEFT — composition */}
                <div>
                    <h3 className="mb-2.5 font-display text-[11px] uppercase tracking-[0.2em] text-fg-3">{t("why2.s1")}</h3>
                    <TridentBar trident={trident} height={16} showLabels={false} />
                    <div className="mt-3.5 flex flex-col gap-2">
                        {rows.map((r) => (
                            <div key={r.k} className={cn("border-l-[3px] bg-bg-2 px-3 py-2.5", r.cls)}>
                                <div className="mb-1 flex items-baseline justify-between">
                                    <span className={cn("font-display text-[11px] uppercase tracking-[0.15em]", r.text)}>{r.k}</span>
                                    <span className={cn("font-display text-[11px] font-bold", r.text)}>+{Math.round(r.v * q)} {t("why2.pts")}</span>
                                </div>
                                <div className="font-mono text-[11px] leading-snug text-fg-2">{r.label}</div>
                            </div>
                        ))}
                    </div>

                    <h3 className="mb-2.5 mt-6 font-display text-[11px] uppercase tracking-[0.2em] text-fg-3">{t("why2.s2")}</h3>
                    <AnchorList anchors={why.anchors} posterW={36} />
                </div>

                {/* RIGHT — context */}
                <div>
                    <h3 className="mb-2.5 font-display text-[11px] uppercase tracking-[0.2em] text-fg-3">{t("why2.s3")}</h3>
                    {why.cluster ? (
                        <ClusterCard cluster={why.cluster} />
                    ) : (
                        <div className="border border-dashed border-border-2 p-3.5 font-mono text-[11px] text-fg-3">
                            {t("why2.no_cluster")}
                        </div>
                    )}

                    <h3 className="mb-2.5 mt-6 font-display text-[11px] uppercase tracking-[0.2em] text-fg-3">{t("why2.s4")}</h3>
                    <NeighborList neighbors={why.neighbors} />

                    <h3 className="mb-2.5 mt-6 font-display text-[11px] uppercase tracking-[0.2em] text-fg-3">{t("why2.s5")}</h3>
                    <div className="border border-border-2 bg-bg-2 p-3.5">
                        <div className="mb-2 font-mono text-[11px] leading-relaxed text-fg-2">
                            {t("why2.reweight_hint")}
                        </div>
                        <div className="flex flex-wrap gap-1.5">
                            {PRESETS.map((p) => (
                                <button
                                    key={p.k}
                                    onClick={() => setPreset(p.k)}
                                    className={cn(
                                        "border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.05em] transition-colors",
                                        preset === p.k
                                            ? "border-primary bg-primary font-bold text-primary-ink"
                                            : "border-border-2 bg-transparent text-fg-2 hover:border-fg-3"
                                    )}
                                >
                                    {t(p.labelKey)}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
