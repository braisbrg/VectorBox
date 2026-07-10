"use client";

// Vector space — full-bleed interactive taste map (handoff: mobile-app.js
// initSpace/drawSpaceM, adapted to real PCA coords from /api/recommendations/space).
// Entrance ripple from the centroid, ambient breath/twinkle, pan / pinch / wheel
// zoom, tap → quick-look ('space' context), cluster chips filter + fly-to,
// "nearest to your centroid" drawer. All motion gated on prefers-reduced-motion.

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { getSpace, SpacePoint } from "@/lib/api";
import { QuickLook, QuickLookFilm } from "@/components/quick-look";
import { SubScreenHeader } from "@/components/shell/sub-screen-header";
import { useLanguage } from "@/components/language-provider";
import { cn } from "@/lib/utils";

const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3);
const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);

interface WorldPoint extends SpacePoint {
    wx: number;
    wy: number;
    d01: number; // entrance stagger: normalized 2-D distance from centroid
}

interface Ghost {
    wx: number;
    wy: number;
    seen: boolean;
    tw: number; // twinkle group (0 twinkles)
    d01: number;
}

interface Model {
    pts: WorldPoint[];
    ghosts: Ghost[];
    hulls: { id: number; poly: [number, number][] }[];
    edges: [WorldPoint, WorldPoint, number][];
    clusters: { id: number; name: string; x: number; y: number; mine: number; total: number }[];
    cx: number;
    cy: number;
    w: number;
    h: number;
}

// Convex hull (Andrew monotone chain) — cluster territory outlines (desktop prototype).
function convexHull(points: [number, number][]): [number, number][] {
    if (points.length < 3) return [];
    const p = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const cross = (o: [number, number], a: [number, number], b: [number, number]) =>
        (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lower: [number, number][] = [];
    for (const pt of p) {
        while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], pt) <= 0) lower.pop();
        lower.push(pt);
    }
    const upper: [number, number][] = [];
    for (let i = p.length - 1; i >= 0; i--) {
        while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p[i]) <= 0) upper.pop();
        upper.push(p[i]);
    }
    return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}

// Deterministic hash (prototype hashStr) — ghosts must not jump between renders.
function hashStr(s: string): number {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (Math.imul(h, 31) + s.charCodeAt(i)) | 0;
    return Math.abs(h);
}

function buildModel(points: SpacePoint[], centroid: { x: number; y: number } | null, clusters: { id: number; name: string }[], w: number, h: number): Model {
    const pts: WorldPoint[] = points.map((p) => ({ ...p, wx: p.x * w, wy: p.y * h, d01: 0 }));
    const cx = (centroid?.x ?? 0.5) * w;
    const cy = (centroid?.y ?? 0.5) * h;
    let maxD = 1;
    for (const p of pts) {
        const d = Math.hypot(p.wx - cx, p.wy - cy);
        (p as WorldPoint & { _d: number })._d = d;
        if (d > maxD) maxD = d;
    }
    for (const p of pts) p.d01 = (p as WorldPoint & { _d: number })._d / maxD;

    // nearest-neighbor lattice (single nearest per point, capped distance)
    const edges: [WorldPoint, WorldPoint, number][] = [];
    const thresh = Math.pow(Math.min(w, h) * 0.09, 2);
    for (let i = 0; i < pts.length; i++) {
        let best = -1;
        let bd = Infinity;
        for (let j = 0; j < pts.length; j++) {
            if (i === j) continue;
            const d = (pts[i].wx - pts[j].wx) ** 2 + (pts[i].wy - pts[j].wy) ** 2;
            if (d < bd) {
                bd = d;
                best = j;
            }
        }
        if (best >= 0 && bd < thresh) edges.push([pts[i], pts[best], Math.max(pts[i].d01, pts[best].d01)]);
    }

    // 2-D cluster centroids for halos + labels + fly-to (+ seen/total counts for the panel)
    const clusterPos = clusters
        .map((c) => {
            const members = pts.filter((p) => p.cluster_id === c.id);
            if (!members.length) return null;
            return {
                id: c.id,
                name: c.name,
                x: members.reduce((s, p) => s + p.wx, 0) / members.length,
                y: members.reduce((s, p) => s + p.wy, 0) / members.length,
                mine: members.filter((p) => p.seen).length,
                total: members.length,
            };
        })
        .filter(Boolean) as Model["clusters"];

    // cluster hulls — dashed territory outlines (desktop prototype wireSpace)
    const hulls = clusterPos
        .map((c) => ({
            id: c.id,
            poly: convexHull(pts.filter((p) => p.cluster_id === c.id).map((p) => [p.wx, p.wy] as [number, number])),
        }))
        .filter((h) => h.poly.length >= 3);

    // ghost dust — decorative twinkling points around each cluster (both prototypes).
    // Deterministic (hashStr) so the field is stable across renders; never hit-tested.
    const ghosts: Ghost[] = [];
    for (const c of clusterPos) {
        const n = 9 + (hashStr("g" + c.id) % 9);
        for (let i = 0; i < n; i++) {
            const hsh = hashStr("gh" + c.id + "·" + i * 31);
            const a = (hsh % 360) * (Math.PI / 180);
            const r = 10 + ((hsh >> 8) % 60);
            const gx = c.x + Math.cos(a) * r;
            const gy = c.y + Math.sin(a) * r * 0.8;
            if (gx > 8 && gx < w - 8 && gy > 8 && gy < h - 8) {
                ghosts.push({ wx: gx, wy: gy, seen: hsh % 3 > 0, tw: hsh % 5, d01: Math.hypot(gx - cx, gy - cy) / maxD });
            }
        }
    }

    return { pts, ghosts, hulls, edges, clusters: clusterPos, cx, cy, w, h };
}

export default function SpacePage() {
    const { t } = useLanguage();
    const { data, isLoading } = useQuery({ queryKey: ["space"], queryFn: getSpace, staleTime: 10 * 60 * 1000 });

    const wrapRef = useRef<HTMLDivElement>(null);
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const modelRef = useRef<Model | null>(null);
    const viewRef = useRef({ x: 0, y: 0, k: 1 });
    const selRef = useRef<number | null>(null);
    const focusRef = useRef<number | null>(null);
    const hoverRef = useRef<WorldPoint | null>(null);

    const [focusCluster, setFocusCluster] = useState<number | null>(null);
    const [quickLook, setQuickLook] = useState<QuickLookFilm | null>(null);
    focusRef.current = focusCluster;

    // Projected 2-D distance to the YOU marker — what the canvas actually
    // shows. The 768-d `dc` disagrees with the picture (PCA is lossy), which
    // made the drawer look wrong next to the map.
    const projD = (p: SpacePoint) =>
        Math.hypot(p.x - (data?.centroid?.x ?? 0.5), p.y - (data?.centroid?.y ?? 0.5));

    const nearest = useMemo(() => {
        if (!data) return [];
        // Unseen only: "nearest to your centroid" is a watch-next surface,
        // not a list of films you've already rated.
        let list = data.points.filter((p) => !p.seen);
        if (focusCluster != null) list = list.filter((p) => p.cluster_id === focusCluster);
        return list
            .map((p) => ({ ...p, d2: projD(p) }))
            .sort((a, b) => a.d2 - b.d2)
            .slice(0, 12);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [data, focusCluster]);

    const openPoint = (p: SpacePoint) => {
        selRef.current = p.tmdb_id;
        setQuickLook({
            tmdb_id: p.tmdb_id,
            title: p.title,
            year: p.year,
            poster_url: p.poster_url,
            q: p.q,
            contextLine: `d ${projD(p).toFixed(2)} from your centroid${p.seen ? " · seen" : ""}`,
        });
    };

    const flyToCluster = (id: number | null) => {
        setFocusCluster(id);
        const M = modelRef.current;
        const cv = canvasRef.current;
        if (!M || !cv) return;
        if (id == null) {
            viewRef.current = { x: 0, y: 0, k: 1 };
            return;
        }
        const c = M.clusters.find((x) => x.id === id);
        if (!c) return;
        const k = 1.9;
        viewRef.current = { k, x: cv.clientWidth / 2 - c.x * k, y: cv.clientHeight * 0.4 - c.y * k };
    };

    // canvas lifecycle: build model, wire gestures, run entrance + ambient loop
    useEffect(() => {
        const cv = canvasRef.current;
        const wrap = wrapRef.current;
        if (!cv || !wrap || !data || data.points.length === 0) return;

        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const ctx = cv.getContext("2d")!;
        const dpr = window.devicePixelRatio || 1;
        const w = wrap.clientWidth;
        const h = wrap.clientHeight;
        cv.width = w * dpr;
        cv.height = h * dpr;
        const M = buildModel(data.points, data.centroid, data.clusters, w, h);
        modelRef.current = M;
        viewRef.current = { x: 0, y: 0, k: 1 };

        const css = getComputedStyle(document.documentElement);
        const P = css.getPropertyValue("--primary").trim() || "#CCFF00";
        const FG2 = "#bbbbbb";
        const FG3 = "#777777";
        const BORDER = "#222222";

        // The two prototypes diverge: desktop space (Prototype.html wireSpace) has
        // hulls, hover-NN edges, pulse rings, spinning ◆, vignette, brackets and a
        // cursor coords readout; mobile (mobile-app.js drawSpaceM) keeps the simpler
        // breath + twinkle set. Match each on its own breakpoint.
        const desktopFX = window.matchMedia("(min-width: 1024px)").matches;
        let mouse: { mx: number; my: number } | null = null;

        const start = performance.now();
        const ENTER = desktopFX ? 1300 : 1200;
        let raf = 0;

        function draw(t: number, elapsed: number) {
            const V = viewRef.current;
            const k = V.k;
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            ctx.clearRect(0, 0, w, h);
            ctx.save();
            ctx.translate(V.x, V.y);
            ctx.scale(k, k);

            // grid
            ctx.strokeStyle = BORDER;
            ctx.lineWidth = 0.5 / k;
            ctx.globalAlpha = 0.5 * Math.min(1, t * 2);
            for (let x = -w; x < w * 2; x += 44) {
                ctx.beginPath(); ctx.moveTo(x, -h); ctx.lineTo(x, h * 2); ctx.stroke();
            }
            for (let y = -h; y < h * 2; y += 44) {
                ctx.beginPath(); ctx.moveTo(-w, y); ctx.lineTo(w * 2, y); ctx.stroke();
            }
            ctx.globalAlpha = 1;

            // cluster halos
            for (const c of M.clusters) {
                const foc = focusRef.current === c.id;
                const g = ctx.createRadialGradient(c.x, c.y, 0, c.x, c.y, 64);
                g.addColorStop(0, `rgba(204,255,0,${(foc ? 0.12 : 0.05) * t})`);
                g.addColorStop(1, "rgba(204,255,0,0)");
                ctx.fillStyle = g;
                ctx.beginPath(); ctx.arc(c.x, c.y, 64, 0, Math.PI * 2); ctx.fill();
            }

            // cluster hulls — dashed territory outlines (desktop prototype)
            if (desktopFX) {
                ctx.setLineDash([6 / k, 5 / k]);
                for (const hull of M.hulls) {
                    const hot = focusRef.current === hull.id || hoverRef.current?.cluster_id === hull.id;
                    ctx.beginPath();
                    hull.poly.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
                    ctx.closePath();
                    ctx.strokeStyle = hot ? P : FG3;
                    ctx.globalAlpha = (hot ? 0.55 : 0.28) * t;
                    ctx.lineWidth = 1 / k;
                    ctx.stroke();
                    if (hot) {
                        ctx.fillStyle = P;
                        ctx.globalAlpha = 0.03;
                        ctx.fill();
                    }
                }
                ctx.setLineDash([]);
                ctx.globalAlpha = 1;
            }

            // lattice
            ctx.strokeStyle = FG3;
            ctx.lineWidth = 0.6 / k;
            for (const [a, b, rank] of M.edges) {
                const ep = clamp01((t - 0.45 - rank * 0.15) / 0.4);
                if (ep <= 0) continue;
                ctx.globalAlpha = 0.15 * ep;
                ctx.beginPath(); ctx.moveTo(a.wx, a.wy); ctx.lineTo(b.wx, b.wy); ctx.stroke();
            }
            ctx.globalAlpha = 1;

            // ghost dust — decorative twinkle (both prototypes)
            const gsz = (desktopFX ? 2.4 : 1.5) / Math.sqrt(k);
            ctx.fillStyle = FG3;
            for (const g of M.ghosts) {
                const gp = clamp01((t - g.d01 * 0.5) / 0.4);
                if (gp <= 0) continue;
                const shimmer = reduceMotion
                    ? 0.26
                    : 0.15 + 0.14 * (0.5 + 0.5 * Math.sin(elapsed / 714 + g.wx * 0.137 + g.wy * 0.073));
                ctx.globalAlpha = shimmer * gp;
                if (g.seen) {
                    ctx.fillRect(g.wx - gsz / 2, g.wy - gsz / 2, gsz, gsz);
                } else {
                    ctx.strokeStyle = FG3;
                    ctx.lineWidth = 0.7 / k;
                    ctx.strokeRect(g.wx - gsz / 2, g.wy - gsz / 2, gsz, gsz);
                }
            }
            ctx.globalAlpha = 1;

            // hover — acid dashed edges to the 4 nearest neighbours + d labels (desktop prototype)
            const hovPt = hoverRef.current;
            if (desktopFX && hovPt) {
                const near = M.pts
                    .filter((q) => q.tmdb_id !== hovPt.tmdb_id)
                    .map((q) => ({ q, d: Math.hypot(q.wx - hovPt.wx, q.wy - hovPt.wy) }))
                    .sort((a, b) => a.d - b.d)
                    .slice(0, 4);
                ctx.strokeStyle = P;
                ctx.lineWidth = 1 / k;
                for (const o of near) {
                    ctx.setLineDash([4 / k, 3 / k]);
                    ctx.globalAlpha = 0.75;
                    ctx.beginPath(); ctx.moveTo(hovPt.wx, hovPt.wy); ctx.lineTo(o.q.wx, o.q.wy); ctx.stroke();
                    ctx.setLineDash([]);
                    ctx.globalAlpha = 1;
                    ctx.fillStyle = P;
                    ctx.font = `${8.5 / k}px 'Departure Mono', 'IBM Plex Mono', monospace`;
                    ctx.textAlign = "center";
                    ctx.fillText(`d ${(o.d / Math.min(w, h)).toFixed(2)}`, (hovPt.wx + o.q.wx) / 2, (hovPt.wy + o.q.wy) / 2 - 4 / k);
                }
                ctx.textAlign = "left";
                ctx.globalAlpha = 1;
            }

            // film points — filled = seen, hollow = unseen (watchlist).
            // Desktop intro: points FLY OUT from the centroid (prototype wireSpace);
            // mobile intro: staggered fade by distance (prototype drawSpaceM).
            M.pts.forEach((p, i) => {
                const pp = clamp01((t - p.d01 * 0.5) / 0.4);
                if (pp <= 0) return;
                const sc = easeOutCubic(pp);
                let px = p.wx;
                let py = p.wy;
                if (desktopFX && t < 1) {
                    const stag = (hashStr(p.tmdb_id + "fly") % 100) / 100 * 0.4;
                    const fq = clamp01((t - stag) / 0.6);
                    const fe = easeOutCubic(fq);
                    px = M.cx + (p.wx - M.cx) * fe;
                    py = M.cy + (p.wy - M.cy) * fe;
                }
                const dim = focusRef.current != null && p.cluster_id !== focusRef.current;
                const sel = selRef.current === p.tmdb_id;
                const hov = hoverRef.current?.tmdb_id === p.tmdb_id;
                let aMul = 1;
                if (t >= 1 && !reduceMotion && !p.seen && i % 7 === 0) {
                    aMul = 0.5 + 0.5 * (0.5 + 0.5 * Math.sin(elapsed / 520 + i));
                }
                const hs = (p.seen ? 2.4 : 1.7) * sc / Math.sqrt(k);
                if (desktopFX && (sel || hov)) {
                    ctx.shadowColor = P;
                    ctx.shadowBlur = 11;
                }
                ctx.globalAlpha = (dim ? 0.22 : p.seen ? 1 : 0.55) * pp * aMul;
                if (p.seen) {
                    ctx.fillStyle = sel || hov ? P : FG2;
                    ctx.fillRect(px - hs, py - hs, hs * 2, hs * 2);
                } else {
                    ctx.strokeStyle = sel || hov ? P : FG3;
                    ctx.lineWidth = 0.8 / k;
                    ctx.strokeRect(px - hs, py - hs, hs * 2, hs * 2);
                }
                ctx.shadowBlur = 0;
                if (sel || hov) {
                    ctx.strokeStyle = P;
                    ctx.lineWidth = 1.2 / k;
                    ctx.globalAlpha = 0.9;
                    ctx.strokeRect(px - hs * 2.4, py - hs * 2.4, hs * 4.8, hs * 4.8);
                }
                ctx.globalAlpha = 1;
            });

            // hover tooltip label
            const hov = hoverRef.current;
            if (hov) {
                ctx.font = `${10 / k}px 'Departure Mono', 'IBM Plex Mono', monospace`;
                ctx.fillStyle = P;
                ctx.fillText(`${hov.title}${hov.year ? ` · ${hov.year}` : ""}`, hov.wx + 8 / k, hov.wy - 8 / k);
            }

            // cluster labels — desktop: always visible `id · name` + leader tick
            // (prototype wireSpace); mobile: `#id`, zoom-gated (prototype drawSpaceM).
            ctx.textAlign = "center";
            if (desktopFX) {
                ctx.font = `${10.5 / k}px 'Departure Mono', 'IBM Plex Mono', monospace`;
                for (const c of M.clusters) {
                    const hot = focusRef.current === c.id || hoverRef.current?.cluster_id === c.id;
                    ctx.fillStyle = hot ? P : FG3;
                    ctx.globalAlpha = (hot ? 1 : 0.85) * t;
                    ctx.fillText(`${c.id} · ${c.name.toLowerCase()}`, c.x, c.y - 78 / k);
                    ctx.strokeStyle = ctx.fillStyle;
                    ctx.globalAlpha = (hot ? 0.6 : 0.3) * t;
                    ctx.lineWidth = 1 / k;
                    ctx.beginPath(); ctx.moveTo(c.x, c.y - 72 / k); ctx.lineTo(c.x, c.y - 56 / k); ctx.stroke();
                }
            } else {
                ctx.font = `${9 / k}px 'Departure Mono', 'IBM Plex Mono', monospace`;
                for (const c of M.clusters) {
                    const foc = focusRef.current === c.id;
                    if (!foc && k < 1.3) continue;
                    ctx.fillStyle = foc ? P : FG3;
                    ctx.globalAlpha = (foc ? 1 : 0.7) * t;
                    ctx.fillText(`#${c.id}`, c.x, c.y - 66 / k);
                }
            }
            ctx.globalAlpha = 1;
            ctx.textAlign = "left";

            // YOU centroid — desktop: crosshair + radiating pulse rings + slow-spinning ◆
            // (prototype wireSpace); mobile: breathing rings + static ◆ (drawSpaceM).
            const cp = clamp01((t - 0.55) / 0.45);
            if (cp > 0) {
                const ce = easeOutCubic(cp);
                if (desktopFX) {
                    // crosshair through the whole space
                    ctx.strokeStyle = P;
                    ctx.globalAlpha = 0.1 * ce;
                    ctx.lineWidth = 1 / k;
                    ctx.beginPath(); ctx.moveTo(-w, M.cy); ctx.lineTo(w * 2, M.cy); ctx.stroke();
                    ctx.beginPath(); ctx.moveTo(M.cx, -h); ctx.lineTo(M.cx, h * 2); ctx.stroke();
                    ctx.globalAlpha = 1;
                    // radiating pulse rings
                    if (reduceMotion) {
                        ctx.strokeStyle = P;
                        ctx.globalAlpha = 0.35 * ce;
                        ctx.beginPath(); ctx.arc(M.cx, M.cy, 26 / Math.sqrt(k), 0, Math.PI * 2); ctx.stroke();
                        ctx.globalAlpha = 1;
                    } else {
                        for (const off of [0, 1.3]) {
                            const ph = ((elapsed / 1000 + off) % 2.6) / 2.6;
                            ctx.strokeStyle = P;
                            ctx.globalAlpha = (1 - ph) * 0.45 * ce;
                            ctx.lineWidth = 1 / k;
                            ctx.beginPath(); ctx.arc(M.cx, M.cy, (10 + ph * 52) / Math.sqrt(k), 0, Math.PI * 2); ctx.stroke();
                        }
                        ctx.globalAlpha = 1;
                    }
                } else {
                    const breath = t >= 1 && !reduceMotion ? Math.sin(elapsed / 700) : 0;
                    ctx.strokeStyle = P;
                    ctx.lineWidth = 1 / k;
                    ctx.globalAlpha = 0.4 * ce;
                    ctx.beginPath(); ctx.arc(M.cx, M.cy, (16 * ce) / Math.sqrt(k), 0, Math.PI * 2); ctx.stroke();
                    ctx.globalAlpha = (0.18 + breath * 0.07) * ce;
                    ctx.beginPath(); ctx.arc(M.cx, M.cy, ((30 + breath * 3) * ce) / Math.sqrt(k), 0, Math.PI * 2); ctx.stroke();
                    ctx.globalAlpha = 1;
                }
                const ds = ((desktopFX ? 3.5 : 4.5) * ce) / Math.sqrt(k);
                ctx.save();
                ctx.translate(M.cx, M.cy);
                ctx.rotate(Math.PI / 4 + (desktopFX && !reduceMotion ? (elapsed / 1000) * 0.35 : 0));
                ctx.shadowColor = P;
                ctx.shadowBlur = desktopFX ? 14 : 12;
                ctx.fillStyle = P;
                ctx.fillRect(-ds, -ds, ds * 2, ds * 2);
                ctx.restore();
                ctx.shadowBlur = 0;
                const la = clamp01((t - 0.9) / 0.1);
                if (la > 0) {
                    ctx.globalAlpha = la;
                    ctx.fillStyle = P;
                    ctx.font = `${11 / k}px 'Departure Mono', 'IBM Plex Mono', monospace`;
                    ctx.fillText("YOU", M.cx + 12 / k, M.cy + 4 / k);
                    ctx.globalAlpha = 1;
                }
            }
            ctx.restore();

            // ===== screen-space chrome (desktop prototype) =====
            if (desktopFX) {
                // vignette
                const vg = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.38, w / 2, h / 2, Math.max(w, h) * 0.78);
                vg.addColorStop(0, "rgba(0,0,0,0)");
                vg.addColorStop(1, "rgba(0,0,0,0.5)");
                ctx.fillStyle = vg;
                ctx.fillRect(0, 0, w, h);
                // corner brackets
                ctx.strokeStyle = FG3;
                ctx.globalAlpha = 0.7;
                ctx.lineWidth = 1;
                const bl = 16, m = 9;
                for (const [x, y, sx, sy] of [[m, m, 1, 1], [w - m, m, -1, 1], [m, h - m, 1, -1], [w - m, h - m, -1, -1]] as const) {
                    ctx.beginPath(); ctx.moveTo(x, y + bl * sy); ctx.lineTo(x, y); ctx.lineTo(x + bl * sx, y); ctx.stroke();
                }
                ctx.globalAlpha = 1;
                // cursor crosshair + embedding-coords readout
                if (mouse) {
                    ctx.strokeStyle = P;
                    ctx.globalAlpha = 0.08;
                    ctx.beginPath(); ctx.moveTo(mouse.mx, 0); ctx.lineTo(mouse.mx, h); ctx.stroke();
                    ctx.beginPath(); ctx.moveTo(0, mouse.my); ctx.lineTo(w, mouse.my); ctx.stroke();
                    const V2 = viewRef.current;
                    const wx = ((mouse.mx - V2.x) / V2.k - w / 2) / (w / 2);
                    const wy = -((mouse.my - V2.y) / V2.k - h / 2) / (h / 2);
                    ctx.globalAlpha = 0.75;
                    ctx.fillStyle = FG3;
                    ctx.font = "9px 'Departure Mono', 'IBM Plex Mono', monospace";
                    ctx.fillText(`${wx.toFixed(3)} , ${wy.toFixed(3)}`, Math.min(mouse.mx + 12, w - 92), Math.min(mouse.my + 20, h - 12));
                    ctx.globalAlpha = 1;
                }
            }
        }

        function loop(now: number) {
            if (!cv!.isConnected) return;
            const elapsed = now - start;
            const t = reduceMotion ? 1 : clamp01(elapsed / ENTER);
            draw(easeOutCubic(t), elapsed);
            raf = requestAnimationFrame(loop);
        }
        raf = requestAnimationFrame(loop);

        // ---- gestures: pan / pinch / wheel-zoom / tap / hover ----
        const pts = new Map<number, { x: number; y: number }>();
        let pinch: { d: number; k: number } | null = null;
        let moved = 0;
        let downXY: { x: number; y: number } | null = null;
        cv.style.touchAction = "none";

        const hit = (cssX: number, cssY: number, radius = 20): WorldPoint | null => {
            const V = viewRef.current;
            const wx = (cssX - V.x) / V.k;
            const wy = (cssY - V.y) / V.k;
            const R = radius / V.k;
            let best: WorldPoint | null = null;
            let bd = R * R;
            for (const p of M.pts) {
                if (focusRef.current != null && p.cluster_id !== focusRef.current) continue;
                const d = (p.wx - wx) ** 2 + (p.wy - wy) ** 2;
                if (d < bd) {
                    bd = d;
                    best = p;
                }
            }
            return best;
        };

        // A finger tap jitters — `moved` accumulates |dx|+|dy| per event, so the
        // mouse threshold (8) silently ate many mobile taps. Wider thumb radius too.
        let tapThresh = 8;
        let tapRadius = 20;

        const onDown = (e: PointerEvent) => {
            try { cv.setPointerCapture(e.pointerId); } catch {}
            pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
            moved = 0;
            tapThresh = e.pointerType === "touch" ? 18 : 8;
            tapRadius = e.pointerType === "touch" ? 28 : 20;
            downXY = { x: e.clientX, y: e.clientY };
            if (pts.size === 2) {
                const [a, b] = [...pts.values()];
                pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), k: viewRef.current.k };
            }
        };
        const onMove = (e: PointerEvent) => {
            const p = pts.get(e.pointerId);
            if (!p) {
                // hover (desktop, no button held) + cursor crosshair/coords readout
                const r = cv.getBoundingClientRect();
                const mx = e.clientX - r.left;
                const my = e.clientY - r.top;
                hoverRef.current = hit(mx, my);
                if (desktopFX && e.pointerType === "mouse") mouse = { mx, my };
                return;
            }
            mouse = null; // dragging — hide the crosshair (prototype)
            const dx = e.clientX - p.x;
            const dy = e.clientY - p.y;
            moved += Math.abs(dx) + Math.abs(dy);
            p.x = e.clientX;
            p.y = e.clientY;
            const V = viewRef.current;
            if (pts.size === 2 && pinch) {
                const [a, b] = [...pts.values()];
                const nd = Math.hypot(a.x - b.x, a.y - b.y);
                const r = cv.getBoundingClientRect();
                const mx = (a.x + b.x) / 2 - r.left;
                const my = (a.y + b.y) / 2 - r.top;
                const nk = Math.max(0.6, Math.min(4, pinch.k * (nd / pinch.d)));
                const kk = nk / V.k;
                V.x = mx - (mx - V.x) * kk;
                V.y = my - (my - V.y) * kk;
                V.k = nk;
            } else if (pts.size === 1) {
                V.x += dx;
                V.y += dy;
            }
        };
        const onUp = (e: PointerEvent) => {
            const wasTap = moved < tapThresh && pts.size === 1;
            pts.delete(e.pointerId);
            if (pts.size < 2) pinch = null;
            if (wasTap && downXY) {
                const r = cv.getBoundingClientRect();
                const h2 = hit(downXY.x - r.left, downXY.y - r.top, tapRadius);
                if (h2) openPoint(h2);
            }
        };
        const onCancel = (e: PointerEvent) => {
            pts.delete(e.pointerId);
            if (pts.size < 2) pinch = null;
        };
        const onWheel = (e: WheelEvent) => {
            e.preventDefault();
            const V = viewRef.current;
            const r = cv.getBoundingClientRect();
            const mx = e.clientX - r.left;
            const my = e.clientY - r.top;
            const nk = Math.max(0.6, Math.min(4, V.k * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
            const kk = nk / V.k;
            V.x = mx - (mx - V.x) * kk;
            V.y = my - (my - V.y) * kk;
            V.k = nk;
        };

        const onLeave = () => {
            mouse = null;
            hoverRef.current = null;
        };

        cv.addEventListener("pointerdown", onDown);
        cv.addEventListener("pointermove", onMove);
        cv.addEventListener("pointerup", onUp);
        cv.addEventListener("pointercancel", onCancel);
        cv.addEventListener("pointerleave", onLeave);
        cv.addEventListener("wheel", onWheel, { passive: false });

        return () => {
            cancelAnimationFrame(raf);
            cv.removeEventListener("pointerdown", onDown);
            cv.removeEventListener("pointermove", onMove);
            cv.removeEventListener("pointerup", onUp);
            cv.removeEventListener("pointercancel", onCancel);
            cv.removeEventListener("pointerleave", onLeave);
            cv.removeEventListener("wheel", onWheel);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [data]);

    const zoomBy = (factor: number) => {
        const cv = canvasRef.current;
        if (!cv) return;
        const V = viewRef.current;
        const cx = cv.clientWidth / 2;
        const cy = cv.clientHeight * 0.4;
        const nk = Math.max(0.6, Math.min(4, V.k * factor));
        const k = nk / V.k;
        V.x = cx - (cx - V.x) * k;
        V.y = cy - (cy - V.y) * k;
        V.k = nk;
    };

    if (isLoading) {
        return (
            <div className="flex items-center justify-center py-32 font-mono text-xs uppercase tracking-widest text-fg-3">
                PROJECTING_TASTE_MAP…
            </div>
        );
    }
    if (!data || data.points.length === 0) {
        return (
            <div className="py-24">
                <div className="mx-auto max-w-md border border-border-2 bg-bg-2 p-8 text-center shadow-acid">
                    <p className="font-display text-4xl text-primary">◐</p>
                    <h1 className="mt-4 font-display text-2xl uppercase tracking-tight text-fg">Vector space</h1>
                    <p className="mt-3 text-xs uppercase tracking-widest text-fg-3">
                        rate a few films to place yourself in the space
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div className="grid grid-cols-1 gap-4 pt-4 lg:grid-cols-[1fr_300px]">
            {/* /space is a mobile sub-screen (tab bar hidden) — back row first */}
            <div className="lg:hidden">
                <SubScreenHeader crumb="crumbs.space" fallback="/you" />
            </div>
            {/* canvas */}
            <div
                ref={wrapRef}
                className="relative h-[52dvh] min-h-[380px] overflow-hidden border border-border-2 bg-bg lg:h-[calc(100vh-140px)]"
            >
                <canvas ref={canvasRef} className="size-full" />
                {/* HUD */}
                <div className="pointer-events-none absolute left-3 top-3">
                    <div className="font-display text-sm uppercase tracking-tight text-fg">{t("space2.title")}</div>
                    <div className="mt-0.5 font-mono text-[9px] uppercase tracking-[0.15em] text-fg-3">
                        {data.total} · {data.clusters.length} · {t("space2.hud")}
                    </div>
                </div>
                {/* zoom */}
                <div className="absolute right-3 top-3 flex flex-col border border-border-2 bg-bg/85">
                    {[
                        { k: "+", fn: () => zoomBy(1.4), label: "zoom in" },
                        { k: "−", fn: () => zoomBy(1 / 1.4), label: "zoom out" },
                        { k: "◱", fn: () => flyToCluster(null), label: "fit" },
                    ].map((b, i) => (
                        <button
                            key={b.k}
                            onClick={b.fn}
                            aria-label={b.label}
                            className={cn(
                                "flex size-9 items-center justify-center font-display text-sm text-fg-2 transition-colors hover:text-primary",
                                i > 0 && "border-t border-border-2"
                            )}
                        >
                            {b.k}
                        </button>
                    ))}
                </div>
                {/* legend */}
                <div className="pointer-events-none absolute bottom-3 left-3 flex gap-3 font-mono text-[9px] uppercase tracking-[0.1em] text-fg-3">
                    <span className="flex items-center gap-1.5">
                        <span className="size-2 bg-fg-2" /> {t("space2.seen")}
                    </span>
                    <span className="flex items-center gap-1.5">
                        <span className="size-2 border border-fg-3" /> {t("space2.unseen")}
                    </span>
                    <span className="flex items-center gap-1.5">
                        <span className="size-2 rotate-45 bg-primary" /> {t("space2.you")}
                    </span>
                </div>
            </div>

            {/* sheet — cluster chips + nearest drawer */}
            <div className="space-y-3 lg:max-h-[calc(100vh-140px)] lg:overflow-y-auto lg:pr-1 lg:scrollbar-hide">
                <div className="flex flex-wrap gap-1.5">
                    <button
                        onClick={() => flyToCluster(null)}
                        className={cn(
                            "border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.05em] transition-colors",
                            focusCluster == null
                                ? "border-primary bg-primary font-bold text-primary-ink"
                                : "border-border-2 text-fg-2 hover:border-fg-3"
                        )}
                    >
                        {t("space2.all_clusters")}
                    </button>
                    {data.clusters.map((c) => (
                        <button
                            key={c.id}
                            onClick={() => flyToCluster(c.id)}
                            title={`${data.points.filter((p) => p.cluster_id === c.id && p.seen).length}/${data.points.filter((p) => p.cluster_id === c.id).length} seen`}
                            className={cn(
                                "border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.05em] transition-colors",
                                focusCluster === c.id
                                    ? "border-primary bg-primary font-bold text-primary-ink"
                                    : "border-border-2 text-fg-2 hover:border-fg-3"
                            )}
                        >
                            #{c.id} · {c.name}
                        </button>
                    ))}
                </div>

                <div className="flex items-baseline justify-between border-b border-border-2 pb-1.5">
                    <span className="eyebrow">
                        {focusCluster != null ? `${t("space2.nearest_in")} #${focusCluster}` : t("space2.nearest_centroid")}
                    </span>
                    <span className="font-display text-[10px] text-fg-3">{nearest.length}</span>
                </div>
                <div className="space-y-0">
                    {nearest.map((p, i) => (
                        <button
                            key={p.tmdb_id}
                            onClick={() => openPoint(p)}
                            className={cn(
                                "flex w-full items-center gap-3 py-2 text-left transition-colors hover:bg-bg-2",
                                i > 0 && "border-t border-dashed border-border"
                            )}
                        >
                            <div className="poster-art relative h-[42px] w-7 shrink-0 border border-border-2">
                                {p.q != null && (
                                    <span className="absolute inset-x-0 bottom-0 bg-black/85 text-center font-display text-[7px] text-primary">
                                        Q{Math.round(p.q)}
                                    </span>
                                )}
                            </div>
                            <div className="min-w-0 flex-1">
                                <div className="truncate font-mono text-[11px] text-fg">{p.title}</div>
                                <div className="font-mono text-[9px] text-fg-3">
                                    {p.year}
                                    {p.cluster_id != null ? ` · #${p.cluster_id}` : ""}
                                </div>
                            </div>
                            <span className="font-display text-[9px] text-fg-2">d {p.d2.toFixed(2)}</span>
                        </button>
                    ))}
                    {nearest.length === 0 && (
                        <p className="py-3 font-mono text-[10px] leading-relaxed text-fg-3">{t("space2.empty")}</p>
                    )}
                </div>

                {/* calibration (desktop prototype .sp-cal — real actions only, no fake drift stats) */}
                <div className="space-y-2 border border-border-2 bg-bg-2 p-3">
                    <div className="eyebrow">{t("space2.calibration")}</div>
                    <Link
                        href="/import"
                        className="block border border-border-2 px-2.5 py-2 font-mono text-[10px] uppercase tracking-[0.08em] text-fg-2 transition-colors hover:border-primary hover:text-primary"
                    >
                        {t("space2.recal")}
                    </Link>
                    <Link
                        href="/onboarding"
                        className="block border border-border-2 px-2.5 py-2 font-mono text-[10px] uppercase tracking-[0.08em] text-fg-2 transition-colors hover:border-primary hover:text-primary"
                    >
                        {t("space2.rate_more")}
                    </Link>
                </div>
            </div>

            <QuickLook film={quickLook} context="space" onClose={() => setQuickLook(null)} />
        </div>
    );
}
