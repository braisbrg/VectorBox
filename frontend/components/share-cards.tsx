"use client";

// Group + pair shareable cards (handoff share-card.js buildGroup/buildPair),
// following taste-card.tsx: pure-canvas render → REAL PNG export (toBlob).
// Real TMDB posters are drawn with crossOrigin="anonymous" (clean load or
// error — never taints the canvas); tone-gradients are the fallback.

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { X, Download, Loader2 } from "lucide-react";
import { GroupVibeResponse, GroupRecommendation, getGroupVibe, getTMDBImageUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

const FORMATS = {
    story: { w: 1080, h: 1920, label: "story · 9:16" },
    square: { w: 1080, h: 1080, label: "square · 1:1" },
} as const;
type Fmt = keyof typeof FORMATS;

// ---------- data derivation (client-side, from GroupVibeResponse) ----------

function hashStr(s: string): number {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (Math.imul(h, 31) + s.charCodeAt(i)) | 0;
    return Math.abs(h);
}
export const filmTone = (title: string) => `hsl(${hashStr(title) % 360}, 45%, 30%)`;
const tokenLetter = (i: number) => "ABCDEFGH"[i] ?? "?";
const tokenColor = (i: number) => `hsl(${(i * 73) % 360}, 60%, 55%)`;
// contributor scores are cosine 0..1 (older shapes used 0..100) — normalize to %
const pct = (s: number) => Math.round(s <= 1 ? s * 100 : s);

interface CardFilm {
    title: string;
    q: number | null;
    m: number;
    tone: string;
    poster: string | null;
}
interface GroupCardData {
    members: { tok: string; h: string }[];
    compat: number;
    pairs: [string, string, number][];
    overlap: string[];
    films: CardFilm[];
}
interface PairCardData {
    a: { tok: string; h: string };
    b: { tok: string; h: string };
    match: number;
    overlap: string[];
    shared: CardFilm[];
    bridge: { from: string; t: string; tone: string; poster: string | null } | null;
}

function scoreOf(r: GroupRecommendation, username: string): number | null {
    const c = r.contributors.find((x) => x.username === username);
    return c ? (c.score <= 1 ? c.score : c.score / 100) : null;
}

/**
 * Taste match %: gap between the two users' MEAN fit to the shared pool.
 * Per-film scores have noise-level variance (sd≈0.01), so rank-correlation
 * flapped with the candidate pool (47%→25% just by adding a 3rd member);
 * the mean gap barely moves across pools and is the real signal ("how close
 * does this pool sit to each of your taste centroids"). Scaled to familiar
 * blend-style bands (user decision 2026-07-05): Δ0→99 · Δ0.1→~80 · Δ0.2→60 —
 * below 60 reads as genuinely divergent.
 */
function pairwiseAgreement(recs: GroupRecommendation[], a: string, b: string): number {
    const xs: number[] = [];
    const ys: number[] = [];
    for (const r of recs) {
        const sa = scoreOf(r, a);
        const sb = scoreOf(r, b);
        if (sa != null && sb != null) {
            xs.push(sa);
            ys.push(sb);
        }
    }
    if (!xs.length) return 0;
    const mean = (v: number[]) => v.reduce((s, x) => s + x, 0) / v.length;
    const gap = Math.abs(mean(xs) - mean(ys));
    // ceiling 96: only your own profile is a 99+ (user calibration 2026-07-05)
    return Math.min(96, Math.max(5, Math.round(96 - gap * 180)));
}

function topGenres(recs: GroupRecommendation[], n = 3): string[] {
    const freq = new Map<string, number>();
    for (const r of recs) for (const g of r.movie.genres ?? []) freq.set(g, (freq.get(g) ?? 0) + 1);
    return [...freq.entries()]
        .sort((x, y) => y[1] - x[1])
        .slice(0, n)
        .map(([g]) => g.toLowerCase());
}

const meanScore = (r: GroupRecommendation) =>
    r.contributors.length
        ? r.contributors.reduce((s, c) => s + (c.score <= 1 ? c.score : c.score / 100), 0) / r.contributors.length
        : (r.similarity_score <= 1 ? r.similarity_score : r.similarity_score / 100);

export function deriveGroupCard(data: GroupVibeResponse): GroupCardData {
    const members = data.members.map((m, i) => ({ tok: tokenLetter(i), h: `@${m.username}` }));
    const names = data.members.map((m) => m.username);
    const pairs: [string, string, number][] = [];
    const vals: number[] = [];
    for (let i = 0; i < names.length; i++) {
        for (let j = i + 1; j < names.length; j++) {
            const v = pairwiseAgreement(data.recommendations, names[i], names[j]);
            if (v > 0) {
                pairs.push([tokenLetter(i), tokenLetter(j), v]);
                vals.push(v);
            }
        }
    }
    const compat = vals.length ? Math.round(vals.reduce((s, v) => s + v, 0) / vals.length) : 0;
    const films = [...data.recommendations]
        .sort((x, y) => meanScore(y) - meanScore(x))
        .slice(0, 5)
        .map((r) => ({
            title: r.movie.title,
            q: r.movie.vectorbox_score != null ? Math.round(r.movie.vectorbox_score) : null,
            m: pct(meanScore(r)),
            tone: filmTone(r.movie.title),
            poster: r.movie.poster_path ? getTMDBImageUrl(r.movie.poster_path, "w342") : null,
        }));
    return { members, compat, pairs: pairs.slice(0, 6), overlap: topGenres(data.recommendations), films };
}

export function derivePairCard(data: GroupVibeResponse, you: string, peer: string): PairCardData {
    const iYou = data.members.findIndex((m) => m.username === you);
    const iPeer = data.members.findIndex((m) => m.username === peer);
    const a = { tok: tokenLetter(Math.max(iYou, 0)), h: `@${you}` };
    const b = { tok: tokenLetter(Math.max(iPeer, 0)), h: `@${peer}` };
    const match = pairwiseAgreement(data.recommendations, you, peer);

    const both = data.recommendations
        .map((r) => ({ r, sa: scoreOf(r, you), sb: scoreOf(r, peer) }))
        .filter((x): x is { r: GroupRecommendation; sa: number; sb: number } => x.sa != null && x.sb != null);

    const shared = [...both]
        .sort((x, y) => Math.min(y.sa, y.sb) - Math.min(x.sa, x.sb))
        .slice(0, 4)
        .map(({ r, sa, sb }) => ({
            title: r.movie.title,
            q: r.movie.vectorbox_score != null ? Math.round(r.movie.vectorbox_score) : null,
            m: pct(Math.min(sa, sb)),
            tone: filmTone(r.movie.title),
            poster: r.movie.poster_path ? getTMDBImageUrl(r.movie.poster_path, "w342") : null,
        }));

    // BRIDGE (redefined 2026-07-05 — the old "max gap" picked the RECEIVER's
    // worst-fitting film, which made no sense as a recommendation): among films
    // the receiver still fits AT OR ABOVE their own average, pick the one the
    // giver over-indexes on most. A safe stretch toward the giver's taste.
    let bridge: PairCardData["bridge"] = null;
    if (both.length >= 2) {
        const mean = (v: number[]) => v.reduce((s, x) => s + x, 0) / v.length;
        const ma = mean(both.map((x) => x.sa));
        const mb = mean(both.map((x) => x.sb));
        let best = 0.02; // minimum meaningful over-index
        for (const { r, sa, sb } of both) {
            const mk = (from: string) => ({
                from,
                t: r.movie.title,
                tone: filmTone(r.movie.title),
                poster: r.movie.poster_path ? getTMDBImageUrl(r.movie.poster_path, "w342") : null,
            });
            if (sb >= mb && sa - sb > best) {
                best = sa - sb;
                bridge = mk(a.tok);
            }
            if (sa >= ma && sb - sa > best) {
                best = sb - sa;
                bridge = mk(b.tok);
            }
        }
    }
    return { a, b, match, overlap: topGenres(data.recommendations), shared, bridge };
}

// ---------- real poster loading (CORS-safe) ----------

export type ImageMap = Map<string, HTMLImageElement>;

/** crossOrigin="anonymous": either loads clean (canvas stays exportable) or
 *  errors (→ tone-gradient fallback). It can never taint the canvas. */
export function loadImages(urls: (string | null)[]): Promise<ImageMap> {
    const uniq = [...new Set(urls.filter((u): u is string => !!u))];
    return Promise.all(
        uniq.map(
            (u) =>
                new Promise<[string, HTMLImageElement] | null>((res) => {
                    const img = new Image();
                    img.crossOrigin = "anonymous";
                    img.onload = () => res([u, img]);
                    img.onerror = () => res(null);
                    img.src = u;
                })
        )
    ).then((entries) => new Map(entries.filter((e): e is [string, HTMLImageElement] => e !== null)));
}

/** object-cover draw into a rect. */
function drawCover(ctx: CanvasRenderingContext2D, img: HTMLImageElement, x: number, y: number, w: number, h: number) {
    const s = Math.max(w / img.width, h / img.height);
    const sw = w / s;
    const sh = h / s;
    ctx.drawImage(img, (img.width - sw) / 2, (img.height - sh) / 2, sw, sh, x, y, w, h);
}

export function drawPoster(ctx: CanvasRenderingContext2D, f: { tone: string; poster: string | null }, images: ImageMap, x: number, y: number, w: number, h: number) {
    const img = f.poster ? images.get(f.poster) : undefined;
    if (img) {
        drawCover(ctx, img, x, y, w, h);
    } else {
        const g = ctx.createLinearGradient(x, y, x + w, y + h);
        g.addColorStop(0, f.tone);
        g.addColorStop(1, "#050505");
        ctx.fillStyle = g;
        ctx.fillRect(x, y, w, h);
    }
    ctx.strokeStyle = "#2c2c2c";
    ctx.strokeRect(x, y, w, h);
}

// ---------- canvas chrome shared with taste-card ----------

export function drawChrome(ctx: CanvasRenderingContext2D, W: number, H: number, P: string, sub: string) {
    ctx.fillStyle = "#0a0a0a";
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = "#1c1c1c";
    ctx.lineWidth = 1;
    for (let x = 0; x < W; x += 60) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
    for (let y = 0; y < H; y += 60) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
    ctx.strokeStyle = "#2c2c2c";
    ctx.lineWidth = 2;
    ctx.strokeRect(40, 40, W - 80, H - 80);

    const pad = 100;
    const y = H === 1920 ? 200 : 140;
    ctx.font = `56px 'Departure Mono', 'IBM Plex Mono', monospace`;
    ctx.fillStyle = "#f2f2f2";
    ctx.fillText("VECTOR", pad, y);
    const vw = ctx.measureText("VECTOR").width;
    ctx.fillStyle = P;
    ctx.fillRect(pad + vw + 8, y - 48, ctx.measureText("BOX").width + 24, 60);
    ctx.fillStyle = "#0a0a0a";
    ctx.fillText("BOX", pad + vw + 20, y);
    // sub label right-aligned
    ctx.font = `26px 'Departure Mono', 'IBM Plex Mono', monospace`;
    ctx.fillStyle = P;
    ctx.textAlign = "right";
    ctx.fillText(sub, W - pad, y - 10);
    ctx.textAlign = "left";
}

export function drawFooter(ctx: CanvasRenderingContext2D, W: number, H: number, cta: string) {
    ctx.font = `22px 'IBM Plex Mono', monospace`;
    ctx.fillStyle = "#666";
    ctx.textAlign = "center";
    ctx.fillText(`${cta} · vectorbox`, W / 2, H - 80);
    ctx.textAlign = "left";
}

function drawBigPct(ctx: CanvasRenderingContext2D, x: number, y: number, P: string, v: number, label: string, size = 170) {
    ctx.textAlign = "center";
    ctx.font = `${size}px 'Departure Mono', 'IBM Plex Mono', monospace`;
    ctx.fillStyle = P;
    const nw = ctx.measureText(`${v}`).width;
    ctx.fillText(`${v}`, x, y);
    ctx.font = `${Math.round(size * 0.4)}px 'Departure Mono', 'IBM Plex Mono', monospace`;
    ctx.fillText("%", x + nw / 2 + size * 0.22, y);
    ctx.font = `26px 'IBM Plex Mono', monospace`;
    ctx.fillStyle = "#999";
    ctx.fillText(label, x, y + 50);
    ctx.textAlign = "left";
}

export function drawBadges(ctx: CanvasRenderingContext2D, x: number, y: number, maxW: number, items: string[], P: string) {
    ctx.font = `24px 'IBM Plex Mono', monospace`;
    let bx = x;
    for (const b of items) {
        const w = ctx.measureText(b).width + 36;
        if (bx + w > x + maxW) break;
        ctx.strokeStyle = P;
        ctx.strokeRect(bx, y - 34, w, 48);
        ctx.fillStyle = "#ddd";
        ctx.fillText(b, bx + 18, y);
        bx += w + 14;
    }
}

function drawFilmStrip(ctx: CanvasRenderingContext2D, x: number, y: number, films: CardFilm[], pw: number, P: string, images: ImageMap) {
    const ph = pw * 1.5;
    films.forEach((f, i) => {
        const fx = x + i * (pw + 20);
        drawPoster(ctx, f, images, fx, y, pw, ph);
        if (f.q != null) {
            ctx.fillStyle = P;
            ctx.fillRect(fx + pw - 58, y, 58, 30);
            ctx.fillStyle = "#0a0a0a";
            ctx.font = `bold 20px 'Departure Mono', 'IBM Plex Mono', monospace`;
            ctx.textAlign = "center";
            ctx.fillText(`Q${f.q}`, fx + pw - 29, y + 22);
        }
        ctx.fillStyle = "rgba(0,0,0,0.8)";
        ctx.fillRect(fx, y + ph - 32, pw, 32);
        ctx.fillStyle = P;
        ctx.font = `bold 20px 'Departure Mono', 'IBM Plex Mono', monospace`;
        ctx.textAlign = "center";
        ctx.fillText(`${f.m}%`, fx + pw / 2, y + ph - 9);
        // title under the poster
        ctx.fillStyle = "#bbb";
        ctx.font = `18px 'IBM Plex Mono', monospace`;
        let t = f.title;
        while (ctx.measureText(t).width > pw && t.length > 3) t = t.slice(0, -1);
        if (t !== f.title) t = t.slice(0, -1) + "…";
        ctx.fillText(t, fx + pw / 2, y + ph + 28);
        ctx.textAlign = "left";
    });
}

function drawConstellation(ctx: CanvasRenderingContext2D, cx: number, cy: number, rw: number, rh: number, members: GroupCardData["members"], P: string) {
    const N = members.length;
    const nodes = members.map((m, i) => {
        const a = -Math.PI / 2 + i * ((2 * Math.PI) / N);
        return { ...m, x: cx + Math.cos(a) * rw, y: cy + Math.sin(a) * rh, col: tokenColor(i) };
    });
    ctx.strokeStyle = P;
    ctx.globalAlpha = 0.5;
    ctx.lineWidth = 3;
    for (const n of nodes) { ctx.beginPath(); ctx.moveTo(n.x, n.y); ctx.lineTo(cx, cy); ctx.stroke(); }
    ctx.globalAlpha = 1;
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, 100);
    g.addColorStop(0, "rgba(205,254,4,0.3)");
    g.addColorStop(1, "rgba(205,254,4,0)");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(cx, cy, 100, 0, 7); ctx.fill();
    ctx.fillStyle = P;
    ctx.shadowColor = P;
    ctx.shadowBlur = 24;
    ctx.beginPath(); ctx.arc(cx, cy, 18, 0, 7); ctx.fill();
    ctx.shadowBlur = 0;
    ctx.fillStyle = "#0a0a0a";
    ctx.font = `bold 24px 'Departure Mono', monospace`;
    ctx.textAlign = "center";
    ctx.fillText("★", cx, cy + 8);
    for (const n of nodes) {
        ctx.fillStyle = n.col;
        ctx.fillRect(n.x - 32, n.y - 32, 64, 64);
        ctx.fillStyle = "#0a0a0a";
        ctx.font = `bold 32px 'Departure Mono', monospace`;
        ctx.fillText(n.tok, n.x, n.y + 11);
        ctx.fillStyle = "#aaa";
        ctx.font = `20px 'IBM Plex Mono', monospace`;
        ctx.fillText(n.h, n.x, n.y + 64);
    }
    ctx.textAlign = "left";
}

function drawPairLink(ctx: CanvasRenderingContext2D, cx: number, cy: number, half: number, a: PairCardData["a"], b: PairCardData["b"], P: string) {
    const lx = cx - half;
    const rx = cx + half;
    ctx.strokeStyle = P;
    ctx.globalAlpha = 0.5;
    ctx.lineWidth = 3.5;
    ctx.beginPath(); ctx.moveTo(lx, cy); ctx.lineTo(rx, cy); ctx.stroke();
    ctx.globalAlpha = 1;
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, 90);
    g.addColorStop(0, "rgba(205,254,4,0.28)");
    g.addColorStop(1, "rgba(205,254,4,0)");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(cx, cy, 90, 0, 7); ctx.fill();
    ctx.fillStyle = P;
    ctx.shadowColor = P;
    ctx.shadowBlur = 20;
    ctx.beginPath(); ctx.arc(cx, cy, 15, 0, 7); ctx.fill();
    ctx.shadowBlur = 0;
    const sides: [number, string, string, string][] = [
        [lx, a.tok, a.h, "#7dd3fc"],
        [rx, b.tok, b.h, "#f0abfc"],
    ];
    ctx.textAlign = "center";
    for (const [x, tok, h, col] of sides) {
        ctx.fillStyle = col;
        ctx.fillRect(x - 40, cy - 40, 80, 80);
        ctx.fillStyle = "#0a0a0a";
        ctx.font = `bold 40px 'Departure Mono', monospace`;
        ctx.fillText(tok, x, cy + 14);
        ctx.fillStyle = "#aaa";
        ctx.font = `22px 'IBM Plex Mono', monospace`;
        ctx.fillText(h, x, cy + 80);
    }
    ctx.textAlign = "left";
}

// ---------- card renderers ----------

function drawGroupCard(cv: HTMLCanvasElement, d: GroupCardData, fmt: Fmt, P: string, images: ImageMap) {
    const { w: W, h: H } = FORMATS[fmt];
    cv.width = W;
    cv.height = H;
    const ctx = cv.getContext("2d")!;
    drawChrome(ctx, W, H, P, "GROUP MATCH");
    const pad = 100;

    if (fmt === "story") {
        drawConstellation(ctx, W / 2, 560, 330, 250, d.members, P);
        drawBigPct(ctx, W / 2, 1050, P, d.compat, "compatible");
        // pairwise chips centered
        ctx.font = `26px 'IBM Plex Mono', monospace`;
        const chipW = 176;
        const totalW = Math.min(d.pairs.length, 4) * chipW + (Math.min(d.pairs.length, 4) - 1) * 14;
        let px = W / 2 - totalW / 2;
        d.pairs.slice(0, 4).forEach(([a, b, v]) => {
            ctx.strokeStyle = "#2c2c2c";
            ctx.strokeRect(px, 1120, chipW, 52);
            ctx.fillStyle = "#999";
            ctx.fillText(`${a}×${b}`, px + 16, 1155);
            ctx.fillStyle = P;
            ctx.textAlign = "right";
            ctx.fillText(`${v}%`, px + chipW - 16, 1155);
            ctx.textAlign = "left";
            px += chipW + 14;
        });
        if (d.overlap.length) {
            ctx.font = `24px 'Departure Mono', 'IBM Plex Mono', monospace`;
            ctx.fillStyle = "#777";
            ctx.fillText("YOU ALL GRAVITATE TO", pad, 1260);
            drawBadges(ctx, pad, 1320, W - pad * 2, d.overlap, P);
        }
        ctx.font = `24px 'Departure Mono', 'IBM Plex Mono', monospace`;
        ctx.fillStyle = "#777";
        ctx.fillText("FILMS YOU'LL ALL LOVE", pad, 1420);
        drawFilmStrip(ctx, pad, 1450, d.films, (W - pad * 2 - 4 * 20) / 5, P, images);
    } else {
        drawConstellation(ctx, 300, 460, 190, 160, d.members, P);
        drawBigPct(ctx, 770, 420, P, d.compat, "compatible", 140);
        ctx.font = `24px 'IBM Plex Mono', monospace`;
        let py = 500;
        d.pairs.slice(0, 3).forEach(([a, b, v]) => {
            ctx.strokeStyle = "#2c2c2c";
            ctx.strokeRect(640, py, 260, 48);
            ctx.fillStyle = "#999";
            ctx.fillText(`${a}×${b}`, 656, py + 32);
            ctx.fillStyle = P;
            ctx.textAlign = "right";
            ctx.fillText(`${v}%`, 884, py + 32);
            ctx.textAlign = "left";
            py += 58;
        });
        // capped poster size + centered so the strip clears the footer (was overlapping)
        ctx.font = `24px 'Departure Mono', 'IBM Plex Mono', monospace`;
        ctx.fillStyle = "#777";
        ctx.fillText("FILMS YOU'LL ALL LOVE", pad, 716);
        const pw = 110;
        const total = 5 * pw + 4 * 20;
        drawFilmStrip(ctx, (W - total) / 2, 740, d.films.slice(0, 5), pw, P, images);
    }
    drawFooter(ctx, W, H, "match your group →");
}

function drawPairCard(cv: HTMLCanvasElement, d: PairCardData, fmt: Fmt, P: string, images: ImageMap) {
    const { w: W, h: H } = FORMATS[fmt];
    cv.width = W;
    cv.height = H;
    const ctx = cv.getContext("2d")!;
    drawChrome(ctx, W, H, P, "PAIR MATCH");
    const pad = 100;

    if (fmt === "story") {
        drawPairLink(ctx, W / 2, 480, 300, d.a, d.b, P);
        drawBigPct(ctx, W / 2, 850, P, d.match, "taste match");
        if (d.overlap.length) {
            ctx.font = `24px 'Departure Mono', 'IBM Plex Mono', monospace`;
            ctx.fillStyle = "#777";
            ctx.fillText("WHERE YOU OVERLAP", pad, 990);
            drawBadges(ctx, pad, 1050, W - pad * 2, d.overlap, P);
        }
        ctx.font = `24px 'Departure Mono', 'IBM Plex Mono', monospace`;
        ctx.fillStyle = "#777";
        ctx.fillText("YOU BOTH LOVE", pad, 1160);
        drawFilmStrip(ctx, pad, 1190, d.shared, (W - pad * 2 - 3 * 20) / 4, P, images);
        if (d.bridge) {
            const by = 1620;
            drawPoster(ctx, d.bridge, images, pad, by, 70, 105);
            const other = d.bridge.from === d.a.tok ? d.b.tok : d.a.tok;
            ctx.fillStyle = P;
            ctx.font = `bold 26px 'Departure Mono', 'IBM Plex Mono', monospace`;
            ctx.fillText(`${d.bridge.from} will convert ${other}`, pad + 96, by + 40);
            ctx.fillStyle = "#ccc";
            ctx.font = `26px 'IBM Plex Mono', monospace`;
            ctx.fillText(d.bridge.t, pad + 96, by + 78);
            ctx.fillStyle = "#666";
            ctx.font = `20px 'IBM Plex Mono', monospace`;
            ctx.fillText(`${d.bridge.from}'s strongest pick that still fits ${other} — a nudge toward ${d.bridge.from}'s taste`, pad + 96, by + 108);
        } else {
            // no meaningful taste gap → nothing to bridge; fill the slot instead of leaving it blank
            ctx.textAlign = "center";
            ctx.fillStyle = "#666";
            ctx.font = `24px 'IBM Plex Mono', monospace`;
            ctx.fillText("no bridge film needed — your tastes already overlap", W / 2, 1680);
            ctx.textAlign = "left";
        }
    } else {
        drawPairLink(ctx, 300, 400, 180, d.a, d.b, P);
        drawBigPct(ctx, 770, 380, P, d.match, "taste match", 140);
        if (d.bridge) {
            const other = d.bridge.from === d.a.tok ? d.b.tok : d.a.tok;
            ctx.fillStyle = P;
            ctx.font = `bold 24px 'Departure Mono', 'IBM Plex Mono', monospace`;
            ctx.textAlign = "center";
            ctx.fillText(`${d.bridge.from} → ${other} · bridge film`, 770, 490);
            ctx.fillStyle = "#ccc";
            ctx.font = `24px 'IBM Plex Mono', monospace`;
            let t = d.bridge.t;
            while (ctx.measureText(t).width > 320 && t.length > 3) t = t.slice(0, -1);
            ctx.fillText(t, 770, 526);
            ctx.textAlign = "left";
        } else {
            ctx.fillStyle = "#666";
            ctx.font = `20px 'IBM Plex Mono', monospace`;
            ctx.textAlign = "center";
            ctx.fillText("no bridge needed —", 770, 496);
            ctx.fillText("your tastes already overlap", 770, 526);
            ctx.textAlign = "left";
        }
        // capped poster size + centered so the strip clears the footer (was overlapping)
        ctx.font = `24px 'Departure Mono', 'IBM Plex Mono', monospace`;
        ctx.fillStyle = "#777";
        ctx.fillText("YOU BOTH LOVE", pad, 636);
        const pw = 120;
        const total = 4 * pw + 3 * 20;
        drawFilmStrip(ctx, (W - total) / 2, 660, d.shared, pw, P, images);
    }
    drawFooter(ctx, W, H, "match a friend →");
}

// ---------- shared modal shell ----------

/**
 * Resolve the theme's --primary to exact sRGB hex for canvas drawing.
 * getComputedStyle serializes oklch() colors AS oklch() — a regex for
 * "rgb(r, g, b)" silently mis-parsed it and produced a wrong colour (acid
 * rendered dark blue instead of #f7e800). Painting 1px and reading the pixel
 * back handles every colour syntax the browser knows.
 */
export function resolveAccent(): string {
    const probe = document.createElement("div");
    probe.style.color = "var(--primary)";
    probe.style.position = "absolute";
    probe.style.visibility = "hidden";
    document.body.appendChild(probe);
    const col = getComputedStyle(probe).color;
    probe.remove();
    const cv = document.createElement("canvas");
    cv.width = cv.height = 1;
    const ctx = cv.getContext("2d")!;
    ctx.fillStyle = col;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
    return `#${[r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

function ShareCardModal({
    title,
    filename,
    posterUrls,
    draw,
    onClose,
}: {
    title: string;
    filename: string;
    posterUrls: (string | null)[];
    draw: (cv: HTMLCanvasElement, fmt: Fmt, accent: string, images: ImageMap) => void;
    onClose: () => void;
}) {
    const [fmt, setFmt] = useState<Fmt>("story");
    const [images, setImages] = useState<ImageMap | null>(null);
    const ref = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        let live = true;
        loadImages(posterUrls).then((m) => live && setImages(m));
        return () => {
            live = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        if (!ref.current || !images) return;
        const run = () => draw(ref.current!, fmt, resolveAccent(), images);
        if (document.fonts?.ready) document.fonts.ready.then(run);
        else run();
    }, [draw, fmt, images]);

    const savePng = () => {
        ref.current?.toBlob((blob) => {
            if (!blob) return;
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `${filename}-${fmt}.png`;
            a.click();
            URL.revokeObjectURL(url);
        }, "image/png");
    };

    return (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
            <div
                className="flex max-h-[92vh] w-full max-w-md flex-col border-2 border-primary bg-bg shadow-acid-primary"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
            >
                <div className="flex items-center justify-between border-b border-border-2 px-4 py-3">
                    <span className="font-display text-[10px] uppercase tracking-[0.2em] text-primary">{title}</span>
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

export function GroupCardModal({ data, onClose }: { data: GroupVibeResponse; onClose: () => void }) {
    const card = useMemo(() => deriveGroupCard(data), [data]);
    return (
        <ShareCardModal
            title="group match"
            filename="vectorbox-group"
            posterUrls={card.films.map((f) => f.poster)}
            draw={(cv, fmt, accent, images) => drawGroupCard(cv, card, fmt, accent, images)}
            onClose={onClose}
        />
    );
}

export function PairCardModal({
    data,
    you,
    peer,
    onClose,
}: {
    /** Group response fallback (used until the dedicated pair run resolves). */
    data: GroupVibeResponse;
    you: string;
    peer: string;
    onClose: () => void;
}) {
    // Dedicated 2-member run — deriving from the loaded GROUP made the pair
    // numbers/films shift whenever a 3rd member changed the candidate pool.
    const { data: pairData, isLoading } = useQuery({
        queryKey: ["pair-vibe", you, peer],
        queryFn: () => getGroupVibe([you, peer], {}),
        staleTime: 10 * 60 * 1000,
        retry: 1,
    });
    const source = pairData ?? data;
    const card = useMemo(() => derivePairCard(source, you, peer), [source, you, peer]);

    if (isLoading) {
        return (
            <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
                <div
                    className="flex items-center gap-3 border-2 border-primary bg-bg px-6 py-5 font-mono text-xs text-fg-2 shadow-acid-primary"
                    onClick={(e) => e.stopPropagation()}
                >
                    <Loader2 className="size-4 animate-spin text-primary" /> computing pair match…
                </div>
            </div>
        );
    }

    return (
        <ShareCardModal
            title="pair match"
            filename={`vectorbox-pair-${peer}`}
            posterUrls={[...card.shared.map((f) => f.poster), card.bridge?.poster ?? null]}
            draw={(cv, fmt, accent, images) => drawPairCard(cv, card, fmt, accent, images)}
            onClose={onClose}
        />
    );
}
