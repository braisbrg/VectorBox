"use client";

// Profile hub — handoff H2 · Split dossier (profiles.jsx ProfileH2):
// left editorial card (avatar · name · tier · signature · 2×2 stats ·
// recently-rated strip · actions) + right SYS_PROFILE terminal (vector band
// from the real /space projection · trident bars · activity sparkline ·
// top clusters). Taste-card share ships in P9 (shareables).

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getMyProfile, getSpace, getTMDBImageUrl, getLetterboxdUrl, unrateFilm, SpaceResponse } from "@/lib/api";
import { TasteCardModal } from "@/components/taste-card";
import { useShell } from "@/components/shell/shell-context";
import { useLanguage } from "@/components/language-provider";
import { useVectorboxLogout } from "@/hooks/useVectorboxLogout";
import { resolveAccent, resolveDisplayFont } from "@/lib/accent";
import { cn } from "@/lib/utils";

/** Static one-shot vector band drawn from the real /space projection. */
function VectorBand({ space, h = 150 }: { space: SpaceResponse; h?: number }) {
    const ref = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        const cv = ref.current;
        if (!cv || !space.points.length) return;
        const dpr = window.devicePixelRatio || 1;
        const cw = cv.clientWidth || 800;
        cv.width = cw * dpr;
        cv.height = h * dpr;
        const ctx = cv.getContext("2d")!;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        // Same reason as space/page.tsx: getComputedStyle hands back oklch()
        // verbatim, so resolve by painting a pixel instead of trusting a string.
        const P = resolveAccent();
        const FG2 = "#9e9e9e"; // --fg-2
        const FG3 = "#808080"; // --fg-3
        ctx.clearRect(0, 0, cw, h);

        // grid
        ctx.strokeStyle = "#292929"; // --border-2
        ctx.lineWidth = 0.5;
        ctx.globalAlpha = 0.6;
        for (let x = 0; x < cw; x += 40) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke(); }
        for (let y = 0; y < h; y += 40) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(cw, y); ctx.stroke(); }
        ctx.globalAlpha = 1;

        // points (band squeezes y into the strip)
        const px = (v: number) => v * cw;
        const py = (v: number) => 8 + v * (h - 16);
        for (const p of space.points) {
            const x = px(p.x);
            const y = py(p.y);
            if (p.seen) {
                ctx.fillStyle = FG2;
                ctx.globalAlpha = 0.85;
                ctx.fillRect(x - 1.6, y - 1.6, 3.2, 3.2);
            } else {
                ctx.strokeStyle = FG3;
                ctx.globalAlpha = 0.6;
                ctx.lineWidth = 0.8;
                ctx.strokeRect(x - 1.6, y - 1.6, 3.2, 3.2);
            }
        }
        ctx.globalAlpha = 1;

        // YOU centroid
        if (space.centroid) {
            const mx = px(space.centroid.x);
            const my = py(space.centroid.y);
            ctx.strokeStyle = P;
            ctx.globalAlpha = 0.4;
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.arc(mx, my, 15, 0, Math.PI * 2); ctx.stroke();
            ctx.globalAlpha = 0.18;
            ctx.beginPath(); ctx.arc(mx, my, 28, 0, Math.PI * 2); ctx.stroke();
            ctx.globalAlpha = 1;
            ctx.save();
            ctx.translate(mx, my);
            ctx.rotate(Math.PI / 4);
            ctx.shadowColor = P;
            ctx.shadowBlur = 12;
            ctx.fillStyle = P;
            ctx.fillRect(-4, -4, 8, 8);
            ctx.restore();
            ctx.shadowBlur = 0;
            ctx.fillStyle = P;
            ctx.font = "11px ${resolveDisplayFont()}";
            ctx.fillText("YOU", mx + 10, my + 3);
        }
    }, [space, h]);

    return <canvas ref={ref} className="block w-full" style={{ height: h }} />;
}

function TridentRows({ t }: { t: { vibe: number; auteur: number; gems: number } }) {
    const rows = [
        { k: "vibe", v: t.vibe },
        { k: "auteur", v: t.auteur },
        { k: "gems", v: t.gems },
    ];
    return (
        <div className="space-y-1.5">
            {rows.map((r) => (
                <div key={r.k} className="flex items-center gap-2.5 font-mono text-xs text-fg-3">
                    <span className="w-[54px]">{r.k}</span>
                    <span className="relative h-[7px] flex-1 border border-border-2 bg-bg-3">
                        <i className="absolute inset-y-0 left-0 bg-primary" style={{ width: `${r.v * 100}%` }} />
                    </span>
                    <b className="w-6 text-right font-display font-normal text-fg">{Math.round(r.v * 100)}</b>
                </div>
            ))}
        </div>
    );
}

function Spark({ values }: { values: number[] }) {
    const max = Math.max(...values, 1);
    return (
        <div className="flex h-[46px] items-end gap-[5px]">
            {values.map((v, i) => (
                <span key={i} className="flex-1 bg-primary opacity-70" style={{ height: `${Math.max(4, (v / max) * 100)}%` }} />
            ))}
        </div>
    );
}

export default function ProfilePage() {
    const { t } = useLanguage();
    const { session } = useShell();
    const queryClient = useQueryClient();
    const { data: profile, isLoading } = useQuery({ queryKey: ["profile"], queryFn: getMyProfile, staleTime: 5 * 60 * 1000 });
    const handleLogout = useVectorboxLogout();
    const handleUnrate = async (tmdbId: number) => {
        try {
            await unrateFilm(tmdbId);
            queryClient.invalidateQueries({ queryKey: ["profile"] });
        } catch (e) {
            console.error("unrate failed:", e);
        }
    };
    const { data: space } = useQuery({ queryKey: ["space"], queryFn: getSpace, staleTime: 10 * 60 * 1000 });
    const [shareOpen, setShareOpen] = useState(false);

    if (isLoading || !profile) {
        return (
            <div className="flex items-center justify-center py-32 font-mono text-xs uppercase tracking-widest text-fg-3">
                {t("you2.loading")}
            </div>
        );
    }

    // Streak dropped — depends on watched_date (sparse after ZIP import), read ~0
    // for most users. Backlog: a login-days streak could replace it.
    const stats: [string, string][] = [
        [`${profile.stats.films}`, "films"],
        [`${profile.stats.clusters}`, "clusters"],
        [profile.stats.avg_rating != null ? `${profile.stats.avg_rating}` : "—", "avg ★"],
    ];

    return (
        <div className="grid grid-cols-1 gap-6 pt-6 lg:grid-cols-[380px_1fr]">
            {/* LEFT — editorial card */}
            <div className="border border-border-2 bg-bg-2 p-6">
                <div className="mb-3.5 flex size-[60px] items-center justify-center bg-primary font-display text-3xl font-bold text-primary-ink">
                    {profile.username.charAt(0).toUpperCase()}
                </div>
                <h1 className="font-display text-2xl lowercase text-fg">{profile.username}</h1>
                <div className="mt-1 font-mono text-xs text-fg-3">
                    {profile.letterboxd_username ? `@${profile.letterboxd_username} · ` : ""}
                    {t("you2.since")} {profile.member_since ?? "—"}
                </div>
                <div className="mt-2.5 inline-block bg-primary px-2 py-1 font-display text-[9px] tracking-[0.14em] text-primary-ink">
                    {profile.tier}
                </div>
                {profile.signature && (
                    <p className="mt-4 font-mono text-sm italic leading-relaxed text-primary">“{profile.signature}”</p>
                )}

                <div className="my-4 grid grid-cols-3 gap-2.5">
                    {stats.map(([v, l]) => (
                        <div key={l} className="border border-border p-2.5 font-mono text-[9px] uppercase tracking-[0.06em] text-fg-3">
                            <span className="mb-0.5 block font-display text-xl normal-case text-primary">{v}</span>
                            {l}
                        </div>
                    ))}
                </div>

                <div className="eyebrow mb-2">{t("you2.recently_rated")}</div>
                <div className="mb-4 flex gap-2">
                    {profile.recently_rated.slice(0, 4).map((f) => (
                        <div key={f.tmdb_id} className="group relative h-[87px] w-[58px] shrink-0">
                            <a
                                href={getLetterboxdUrl(f.tmdb_id)}
                                target="_blank"
                                rel="noopener noreferrer"
                                title={`${f.title}${f.rating != null ? ` · ${f.rating}★` : ""}`}
                                className="poster-art relative block size-full border border-border-2 transition-colors hover:border-primary"
                            >
                                {f.poster_url && (
                                    <Image src={getTMDBImageUrl(f.poster_url, "w154")} alt={f.title} fill sizes="58px" className="object-cover" />
                                )}
                                {f.rating != null && (
                                    <span className="absolute inset-x-0 bottom-0 bg-black/85 text-center font-display text-[8px] text-primary">
                                        {f.rating}★
                                    </span>
                                )}
                            </a>
                            {/* un-rate — removes the rating (e.g. a stray onboarding tap).
                                Touch has no hover: keep the ✕ always visible (slightly bigger) below lg. */}
                            <button
                                onClick={(e) => { e.preventDefault(); handleUnrate(f.tmdb_id); }}
                                title={t("you2.unrate")}
                                aria-label={t("you2.unrate")}
                                className="absolute -right-1.5 -top-1.5 flex size-5 items-center justify-center border border-border-2 bg-bg text-[10px] leading-none text-fg-3 transition-opacity hover:border-danger hover:text-danger lg:size-4 lg:text-[9px] lg:opacity-0 lg:group-hover:opacity-100"
                            >
                                ✕
                            </button>
                        </div>
                    ))}
                </div>

                {/* Library rows (handoff /you hub — watchlist entry is the only mobile path to /watch) */}
                <div className="flex flex-col gap-2">
                    <Link
                        href="/watch"
                        className="flex min-h-[36px] items-center gap-2 border border-border-2 px-3.5 py-2 font-mono text-[11px] uppercase text-fg-2 transition-colors hover:border-primary hover:text-primary"
                    >
                        <span className="text-primary">☆</span> {t("you2.watchlist")}
                    </Link>
                    <Link
                        href="/onboarding"
                        className="flex min-h-[36px] items-center gap-2 border border-border-2 px-3.5 py-2 font-mono text-[11px] uppercase text-fg-2 transition-colors hover:border-primary hover:text-primary"
                    >
                        <span className="text-primary">★</span> {t("you2.rate_more")}
                    </Link>
                    {/* ?step=import: recalibrar es subir el ZIP otra vez, no repetir
                        el alta. Sin esto caías en el paso 1 del asistente de
                        onboarding, que ya habías completado. */}
                    <Link
                        href="/import?step=import"
                        className="flex min-h-[36px] items-center gap-2 border border-border-2 px-3.5 py-2 font-mono text-[11px] uppercase text-fg-2 transition-colors hover:border-primary hover:text-primary"
                    >
                        <span className="text-primary">↻</span> {t("you2.recalibrate")}
                    </Link>
                    <button
                        onClick={() => setShareOpen(true)}
                        className="flex min-h-[42px] items-center justify-center border border-primary bg-primary px-3.5 py-2 font-mono text-[11px] font-bold uppercase text-primary-ink transition-colors hover:bg-transparent hover:text-primary"
                    >
                        {t("you2.share")}
                    </button>
                    <Link
                        href="/space"
                        className="flex min-h-[36px] items-center justify-center border border-border-2 px-3.5 py-2 font-mono text-[11px] uppercase text-fg-2 transition-colors hover:border-primary hover:text-primary"
                    >
                        {t("you2.space")}
                    </Link>
                    <Link
                        href="/stats"
                        className="flex min-h-[36px] items-center justify-center border border-border-2 px-3.5 py-2 font-mono text-[11px] uppercase text-fg-2 transition-colors hover:border-primary hover:text-primary"
                    >
                        {t("you2.stats")}
                    </Link>
                    <Link
                        href="/set"
                        className="flex min-h-[36px] items-center justify-center border border-border-2 px-3.5 py-2 font-mono text-[11px] uppercase text-fg-2 transition-colors hover:border-primary hover:text-primary"
                    >
                        {t("you2.settings")}
                    </Link>
                    {/* Mobile-only: no sidebar below lg, so /you is the natural place to
                        sign out (desktop keeps it in settings → account). */}
                    <button
                        onClick={handleLogout}
                        className="flex min-h-[36px] items-center justify-center border border-border-2 px-3.5 py-2 font-mono text-[11px] uppercase text-fg-3 transition-colors hover:border-danger hover:text-danger lg:hidden"
                    >
                        {t("you2.logout")}
                    </button>
                </div>
            </div>

            {/* RIGHT — SYS_PROFILE terminal */}
            <div>
                <div className="mb-3.5 flex items-center gap-2 border-b border-border-2 pb-3 font-display text-xs uppercase tracking-[0.06em] text-fg-2">
                    <span className="size-2 bg-primary shadow-[0_0_8px_var(--primary)]" />
                    SYS_PROFILE_V2 · {profile.username}@vectorbox
                    <span className="ml-auto text-[10px] text-primary">live</span>
                </div>

                {space && space.points.length > 0 && (
                    <Link href="/space" className="relative mb-3 block overflow-hidden border border-border-2">
                        <VectorBand space={space} h={150} />
                        <span className="absolute left-2.5 top-2 font-display text-[9px] uppercase tracking-[0.14em] text-fg-3">
                            ● vector space
                        </span>
                        <span className="absolute bottom-2 right-2.5 border border-border-2 bg-bg/80 px-2 py-1 font-mono text-[10px] text-primary">
                            {t("you2.open_map")}
                        </span>
                    </Link>
                )}

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <div className="border border-border bg-bg-2 p-3.5">
                        <div className="mb-2.5 font-display text-[10px] uppercase tracking-[0.16em] text-primary">TRIDENT</div>
                        <TridentRows t={profile.trident} />
                    </div>
                    <div className="border border-border bg-bg-2 p-3.5">
                        <div className="mb-2.5 font-display text-[10px] uppercase tracking-[0.16em] text-primary">
                            {t("you2.activity_12")}
                        </div>
                        <Spark values={profile.activity_sparkline} />
                        <div className="mt-2 font-mono text-[10px] text-fg-3">{profile.stats.this_month} {t("you2.this_month")}</div>
                    </div>
                </div>

                <div className="mt-3 border border-border bg-bg-2 p-3.5">
                    <div className="mb-2.5 font-display text-[10px] uppercase tracking-[0.16em] text-primary">
                        {t("you2.clusters_top")} {profile.top_clusters.length} / {profile.stats.clusters}
                    </div>
                    {profile.top_clusters.map((c, i) => (
                        <div
                            key={c.id}
                            className={cn(
                                "flex items-center justify-between gap-3 py-1.5 font-mono text-xs text-fg-3",
                                i > 0 && "border-t border-border"
                            )}
                        >
                            <span className="truncate">
                                #{String(c.id).padStart(3, "0")} · {c.name?.toLowerCase()}
                            </span>
                            <b className="font-display font-normal text-fg">{c.films}</b>
                        </div>
                    ))}
                </div>

                <div className="mt-3.5 font-mono text-[10px] tracking-[0.04em] text-fg-3">
                    {t("you2.derived_1")} {profile.stats.films} {t("you2.derived_2")}
                </div>
            </div>

            {shareOpen && <TasteCardModal profile={profile} onClose={() => setShareOpen(false)} />}
        </div>
    );
}
