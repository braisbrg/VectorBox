"use client";

// Taste card — shareable (handoff share-card.js "taste" type): trident radar
// (triskelion) + signature + stats + defining films, in story 9:16 or square
// 1:1. Rendered on <canvas>; "save image" is a REAL PNG export (canvas.toBlob).
// Short-link + OG image remain backend work (documented gap).

import { useEffect, useMemo, useRef, useState } from "react";
import { X, Download } from "lucide-react";
import { ProfileAggregates, getTMDBImageUrl } from "@/lib/api";
import {
    drawChrome, drawFooter, drawPoster,
    loadImages, filmTone, type ImageMap,
} from "@/components/share-cards";
import { resolveAccent, resolveDisplayFont } from "@/lib/accent";
import { cn } from "@/lib/utils";

// Defining films for the card — prefer the loved-films list (≥4★/liked), fall back
// to recently rated. Posters need the FULL TMDB URL (poster_url is a bare path).
function definingFilmsFor(profile: ProfileAggregates): { title: string; poster: string | null }[] {
    const src = (profile.defining_films?.length ? profile.defining_films : profile.recently_rated) ?? [];
    return src.slice(0, 5).map((f) => ({
        title: f.title,
        poster: f.poster_url ? getTMDBImageUrl(f.poster_url, "w342") : null,
    }));
}

// Taste-card badge sources — the user toggles which to stamp on the card. All are
// REAL profile data (top_clusters = LLM cluster labels, director/actor = highest
// avg-rated with ≥2 films, genres = most-watched). No fabricated descriptors.
type BadgeSource = "clusters" | "auteur" | "genres";
function badgesFor(profile: ProfileAggregates, on: Record<BadgeSource, boolean>): string[] {
    const out: string[] = [];
    if (on.clusters) out.push(...profile.top_clusters.slice(0, 3).map((c) => c.name).filter(Boolean));
    if (on.auteur) {
        if (profile.top_director) out.push(profile.top_director);
        if (profile.top_actor) out.push(profile.top_actor);
    }
    if (on.genres) out.push(...(profile.top_genres ?? []).slice(0, 3));
    return out;
}

const FORMATS = {
    story: { w: 1080, h: 1920, label: "story · 9:16" },
    square: { w: 1080, h: 1080, label: "square · 1:1" },
} as const;
type Fmt = keyof typeof FORMATS;

type DefFilm = { title: string; poster: string | null };

// Trident radar (triskelion) centered at (cx,cy) with radius R.
function drawRadar(ctx: CanvasRenderingContext2D, cx: number, cy: number, R: number, trident: { vibe: number; auteur: number; gems: number }, P: string, labelSize: number) {
    const display = (sz: number) => `${sz}px ${resolveDisplayFont()}`;
    const axes = [
        { k: "vibe" as const, a: -Math.PI / 2 },
        { k: "auteur" as const, a: -Math.PI / 2 + 2.094 },
        { k: "gems" as const, a: -Math.PI / 2 + 4.188 },
    ];
    ctx.strokeStyle = "#555";
    ctx.lineWidth = 1.5;
    [0.33, 0.66, 1].forEach((rr) => {
        ctx.beginPath();
        axes.forEach((ax, i) => {
            const x = cx + Math.cos(ax.a) * R * rr;
            const yy = cy + Math.sin(ax.a) * R * rr;
            i ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy);
        });
        ctx.closePath();
        ctx.stroke();
    });
    axes.forEach((ax) => {
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(ax.a) * R, cy + Math.sin(ax.a) * R);
        ctx.stroke();
    });
    ctx.beginPath();
    axes.forEach((ax, i) => {
        const v = trident[ax.k];
        const x = cx + Math.cos(ax.a) * R * v;
        const yy = cy + Math.sin(ax.a) * R * v;
        i ? ctx.lineTo(x, yy) : ctx.moveTo(x, yy);
    });
    ctx.closePath();
    ctx.fillStyle = `${P}2e`;
    ctx.fill();
    ctx.strokeStyle = P;
    ctx.lineWidth = 4;
    ctx.stroke();
    axes.forEach((ax) => {
        const v = trident[ax.k];
        const x = cx + Math.cos(ax.a) * R * v;
        const yy = cy + Math.sin(ax.a) * R * v;
        ctx.fillStyle = P;
        ctx.fillRect(x - 7, yy - 7, 14, 14);
    });
    ctx.font = display(labelSize);
    ctx.fillStyle = "#888";
    ctx.textAlign = "center";
    axes.forEach((ax) => {
        const lx = cx + Math.cos(ax.a) * (R + 48);
        const ly = cy + Math.sin(ax.a) * (R + 48);
        ctx.fillText(`${ax.k.toUpperCase()}.${Math.round(trident[ax.k] * 100)}`, lx, ly + 8);
    });
    ctx.textAlign = "left";
}

// Multi-row badge chips (drawBadges is single-row; the toggled sources can total
// 8+, and long cluster labels would fill one row and drop the rest). Returns new y.
function drawBadgesWrapped(ctx: CanvasRenderingContext2D, cx: number, y: number, maxW: number, items: string[], P: string): number {
    ctx.font = `24px 'IBM Plex Mono', monospace`;
    const chipH = 46, padX = 22, gap = 14, rowGap = 14;
    const rows: { text: string; w: number }[][] = [[]];
    let rowW = 0;
    for (const b of items) {
        const w = ctx.measureText(b).width + padX * 2;
        if (rowW + w + (rows[rows.length - 1].length ? gap : 0) > maxW && rows[rows.length - 1].length) {
            rows.push([]); rowW = 0;
        }
        rows[rows.length - 1].push({ text: b, w });
        rowW += w + gap;
    }
    for (const row of rows) {
        const totalW = row.reduce((a, c) => a + c.w, 0) + gap * (row.length - 1);
        let bx = cx - totalW / 2;
        for (const chip of row) {
            ctx.strokeStyle = P;
            ctx.lineWidth = 1.5;
            ctx.strokeRect(bx, y, chip.w, chipH);
            ctx.fillStyle = P;
            ctx.textAlign = "center";
            ctx.fillText(chip.text, bx + chip.w / 2, y + 31);
            bx += chip.w + gap;
        }
        y += chipH + rowGap;
    }
    ctx.textAlign = "left";
    return y;
}

function drawDefiningStrip(ctx: CanvasRenderingContext2D, cx: number, y: number, films: DefFilm[], pw: number, images: ImageMap): number {
    const gap = 22, ph = pw * 1.5;
    const stripW = films.length * pw + (films.length - 1) * gap;
    let fx = cx - stripW / 2;
    films.forEach((f) => {
        drawPoster(ctx, { tone: filmTone(f.title), poster: f.poster }, images, fx, y, pw, ph);
        ctx.fillStyle = "#bbb";
        ctx.font = `18px 'IBM Plex Mono', monospace`;
        ctx.textAlign = "center";
        let t = f.title;
        while (ctx.measureText(t).width > pw && t.length > 3) t = t.slice(0, -1);
        if (t !== f.title) t = t.slice(0, -1) + "…";
        ctx.fillText(t, fx + pw / 2, y + ph + 26);
        ctx.textAlign = "left";
        fx += pw + gap;
    });
    return y + ph + 46;
}

function drawTasteCard(
    cv: HTMLCanvasElement, profile: ProfileAggregates, fmt: Fmt, accent: string,
    badges: string[], defining: DefFilm[], images: ImageMap,
) {
    const { w: W, h: H } = FORMATS[fmt];
    cv.width = W;
    cv.height = H;
    const ctx = cv.getContext("2d")!;
    const P = accent;
    const mono = (sz: number) => `${sz}px 'IBM Plex Mono', monospace`;
    const display = (sz: number) => `${sz}px ${resolveDisplayFont()}`;
    const pad = 100;
    const cx = W / 2;

    drawChrome(ctx, W, H, P, "TASTE CARD");

    const sig = (profile.signature || "").trim();
    // Streak dropped — it depends on watched_date, which ZIP imports lack, so it
    // read ~0 for most users (see backlog: a login-days streak may replace it).
    const stats: [string, string][] = [
        [`${profile.stats.films}`, "FILMS"],
        [`${profile.stats.clusters}`, "CLUSTERS"],
        [profile.stats.avg_rating != null ? `${profile.stats.avg_rating}` : "—", "AVG ★"],
    ];

    const drawStatsRow = (sy: number) => {
        const cellW = (W - pad * 2) / stats.length;
        stats.forEach(([v, l], i) => {
            const x = pad + i * cellW;
            ctx.strokeStyle = "#2c2c2c";
            ctx.lineWidth = 2;
            ctx.strokeRect(x, sy, cellW, 130);
            ctx.font = display(52);
            ctx.fillStyle = P;
            ctx.textAlign = "center";
            ctx.fillText(v, x + cellW / 2, sy + 74);
            ctx.font = mono(20);
            ctx.fillStyle = "#777";
            ctx.fillText(l, x + cellW / 2, sy + 110);
        });
        ctx.textAlign = "left";
    };

    const drawSignature = (syTop: number): number => {
        if (!sig) return syTop;
        ctx.font = `italic ${mono(34)}`;
        ctx.fillStyle = P;
        const words = sig.split(" ");
        let line = "";
        const lines: string[] = [];
        for (const wd of words) {
            if ((line + wd).length > 38) { lines.push(line.trim()); line = ""; }
            line += wd + " ";
        }
        lines.push(line.trim());
        ctx.textAlign = "center";
        lines.slice(0, 3).forEach((l, i) => ctx.fillText(`“${l}”`, cx, syTop + i * 46));
        ctx.textAlign = "left";
        return syTop + Math.min(lines.length, 3) * 46;
    };

    if (fmt === "story") {
        let y = 278;
        ctx.font = display(44); ctx.fillStyle = "#f2f2f2";
        ctx.fillText(`@${profile.username}`, pad, y);
        y += 40;
        ctx.font = mono(26); ctx.fillStyle = "#999";
        ctx.fillText(profile.tier.toLowerCase(), pad, y);

        const R = 250;
        const rcy = y + 58 + R;
        drawRadar(ctx, cx, rcy, R, profile.trident, P, 26);
        y = rcy + R + 72;  // stats sit closer to the radar (was pushed far by a large offset)

        y = drawSignature(y) + 38;
        drawStatsRow(y);
        y += 178;
        if (badges.length) y = drawBadgesWrapped(ctx, cx, y, W - pad * 2, badges, P) + 20;
        if (defining.length) {
            ctx.font = display(24); ctx.fillStyle = "#777"; ctx.textAlign = "left";
            ctx.fillText("DEFINING FILMS", pad, y);
            y += 44;
            drawDefiningStrip(ctx, cx, y, defining.slice(0, 4), 150, images);
        }
    } else {
        const topY = 250;
        const R = 175;
        const lcx = pad + R;  // left column centre
        drawRadar(ctx, lcx, topY + R, R, profile.trident, P, 22);
        const rx = W / 2 + 20;
        let ry = topY + 20;
        ctx.font = display(40); ctx.fillStyle = "#f2f2f2"; ctx.textAlign = "left";
        ctx.fillText(`@${profile.username}`, rx, ry);
        ry += 38;
        ctx.font = mono(24); ctx.fillStyle = "#999";
        ctx.fillText(profile.tier.toLowerCase(), rx, ry);
        ry += 44;
        if (sig) {
            ctx.font = `italic ${mono(26)}`; ctx.fillStyle = P;
            const words = sig.split(" "); let line = ""; const lines: string[] = [];
            for (const wd of words) { if ((line + wd).length > 26) { lines.push(line.trim()); line = ""; } line += wd + " "; }
            lines.push(line.trim());
            lines.slice(0, 3).forEach((l, i) => ctx.fillText(`“${l}”`, rx, ry + i * 34));
            ry += Math.min(lines.length, 3) * 34 + 16;
        }
        // Full-width stats row below the two columns (radar left / info right).
        let y = Math.max(topY + 2 * R + 30, ry + 20);
        const cellW = (W - pad * 2) / stats.length, cellH = 108;
        stats.forEach(([v, l], i) => {
            const x = pad + i * cellW;
            ctx.strokeStyle = "#2c2c2c"; ctx.lineWidth = 2;
            ctx.strokeRect(x, y, cellW, cellH);
            ctx.font = display(44); ctx.fillStyle = P; ctx.textAlign = "center";
            ctx.fillText(v, x + cellW / 2, y + 62);
            ctx.font = mono(18); ctx.fillStyle = "#777";
            ctx.fillText(l, x + cellW / 2, y + 92);
        });
        ctx.textAlign = "left";
        y += cellH + 34;
        if (badges.length) y = drawBadgesWrapped(ctx, cx, y, W - pad * 2, badges, P) + 16;
        if (defining.length) drawDefiningStrip(ctx, cx, y, defining.slice(0, 5), 112, images);
    }

    drawFooter(ctx, W, H, "map your taste →");
}

const BADGE_TOGGLES: { k: BadgeSource; label: string }[] = [
    { k: "clusters", label: "clusters" },
    { k: "auteur", label: "director · actor" },
    { k: "genres", label: "genres" },
];

export function TasteCardModal({ profile, onClose }: { profile: ProfileAggregates | null; onClose: () => void }) {
    const [fmt, setFmt] = useState<Fmt>("story");
    const [sources, setSources] = useState<Record<BadgeSource, boolean>>({
        clusters: true, auteur: false, genres: false,
    });
    const [images, setImages] = useState<ImageMap>(new Map());
    const ref = useRef<HTMLCanvasElement>(null);

    const defining = useMemo(() => (profile ? definingFilmsFor(profile) : []), [profile]);

    // Preload the defining-films posters once (crossOrigin — never taints toBlob).
    useEffect(() => {
        if (!defining.length) return;
        loadImages(defining.map((f) => f.poster)).then(setImages);
    }, [defining]);

    useEffect(() => {
        if (!profile || !ref.current) return;
        // Ensure the display font is loaded before drawing text onto canvas.
        // resolveAccent paints 1px and reads it back — handles oklch (the old
        // rgb-regex probe mis-parsed oklch and drew the wrong accent).
        const badges = badgesFor(profile, sources);
        const draw = () => drawTasteCard(ref.current!, profile, fmt, resolveAccent(), badges, defining, images);
        if (document.fonts?.ready) document.fonts.ready.then(draw);
        else draw();
    }, [profile, fmt, sources, defining, images]);

    // A source is only offerable when it actually has data (honest — no empty toggles).
    const available: Record<BadgeSource, boolean> = {
        clusters: (profile?.top_clusters.length ?? 0) > 0,
        auteur: !!(profile?.top_director || profile?.top_actor),
        genres: (profile?.top_genres?.length ?? 0) > 0,
    };

    const savePng = () => {
        ref.current?.toBlob((blob) => {
            if (!blob) return;
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `vectorbox-taste-${profile?.username ?? "card"}-${fmt}.png`;
            a.click();
            URL.revokeObjectURL(url);
        }, "image/png");
    };

    if (!profile) return null;
    return (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
            <div
                className="flex max-h-[92vh] w-full max-w-md flex-col border-2 border-primary bg-bg shadow-acid-primary"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
            >
                <div className="flex items-center justify-between border-b border-border-2 px-4 py-3">
                    <span className="font-display text-[10px] uppercase tracking-[0.2em] text-primary">taste card</span>
                    <div className="flex items-center gap-2">
                        {(Object.keys(FORMATS) as Fmt[]).map((f) => (
                            <button
                                key={f}
                                onClick={() => setFmt(f)}
                                className={cn(
                                    "border px-2 py-1 font-mono text-[10px] transition-colors",
                                    fmt === f ? "border-primary bg-primary font-bold text-primary-ink" : "border-border-2 text-fg-2"
                                )}
                            >
                                {FORMATS[f].label}
                            </button>
                        ))}
                        <button onClick={onClose} aria-label="Close" className="p-1 text-fg-3 hover:text-primary">
                            <X size={16} />
                        </button>
                    </div>
                </div>
                {/* badge source toggles — pick which real taste data to stamp */}
                <div className="flex flex-wrap items-center gap-1.5 border-b border-border-2 px-4 py-2.5">
                    <span className="mr-1 font-mono text-[9px] uppercase tracking-[0.15em] text-fg-3">badges</span>
                    {BADGE_TOGGLES.filter((t) => available[t.k]).map((t) => (
                        <button
                            key={t.k}
                            onClick={() => setSources((s) => ({ ...s, [t.k]: !s[t.k] }))}
                            className={cn(
                                "border px-2 py-1 font-mono text-[10px] transition-colors",
                                sources[t.k] ? "border-primary bg-primary/10 text-primary" : "border-border-2 text-fg-3 hover:border-fg-3"
                            )}
                        >
                            {sources[t.k] ? "● " : "○ "}{t.label}
                        </button>
                    ))}
                </div>
                <div className="min-h-0 flex-1 overflow-auto bg-bg-3 p-4">
                    <canvas
                        ref={ref}
                        className="mx-auto block border border-border-2"
                        // 320 square fits the modal's inner width at 390px viewports (326 available).
                        style={{ width: fmt === "story" ? 250 : 320, height: fmt === "story" ? 444 : 320 }}
                    />
                </div>
                <div className="border-t border-border-2 p-3">
                    <button
                        onClick={savePng}
                        className="flex w-full items-center justify-center gap-2 border border-primary bg-primary px-4 py-2.5 font-mono text-[11px] font-bold uppercase tracking-[0.08em] text-primary-ink transition-colors hover:bg-transparent hover:text-primary"
                    >
                        <Download className="size-3.5" /> save image · png
                    </button>
                </div>
            </div>
        </div>
    );
}
