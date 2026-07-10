"use client";

// Group rec — handoff screens-v3/group-rec.jsx full flow: 2-6 handle chips with
// coloured letter-tokens (A/B/C…) → peer cards → shared-centroid panel →
// peer-agreement matrix (token = on that member's watchlist) → quick-look (grp).
// You are always member A (the backend requires the requester in the group).

import { useMemo, useState } from "react";
import Image from "next/image";
import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { getGroupVibe, getTMDBImageUrl, GroupVibeResponse, GroupRecommendation } from "@/lib/api";
import { getProvidersForCountry } from "@/lib/constants";
import { useLanguage } from "@/components/language-provider";
import { QuickLook, QuickLookFilm } from "@/components/quick-look";
import { GroupCardModal, PairCardModal } from "@/components/share-cards";
import { cn } from "@/lib/utils";

const MAX_MEMBERS = 6;
const tokenColor = (i: number) => `hsl(${(i * 73) % 360}, 60%, 55%)`;
const tokenLetter = (i: number) => String.fromCharCode(65 + i);

type SortKey = "prediction" | "watchlisted" | "quality";

export function GroupVibePicker({ currentUsername }: { currentUsername: string }) {
    const { language, t } = useLanguage();
    const [handles, setHandles] = useState<string[]>([currentUsername]);
    const [input, setInput] = useState("");
    const [sort, setSort] = useState<SortKey>("prediction");
    const [data, setData] = useState<GroupVibeResponse | null>(null);
    const [quickLook, setQuickLook] = useState<QuickLookFilm | null>(null);
    // shareables (F3): group PNG + per-peer pair PNG
    const [shareGroup, setShareGroup] = useState(false);
    const [sharePeer, setSharePeer] = useState<string | null>(null);
    // B-34: handles forced to the Letterboxd/RSS path (default = auto-detect,
    // which prefers a VectorBox account with that name).
    const [forcedLb, setForcedLb] = useState<Set<string>>(new Set());
    // "tonight favours X" — scoring leans on this member instead of the group max
    const [focus, setFocus] = useState<string | null>(null);
    // session filters: "we only have 90 min and filmin+hbo"
    const [maxRuntime, setMaxRuntime] = useState<number | null>(null);
    const [providerFilter, setProviderFilter] = useState<Set<string>>(new Set());

    const runMutation = useMutation({
        mutationFn: () =>
            getGroupVibe(handles, {
                sources: forcedLb.size ? Object.fromEntries([...forcedLb].map((h) => [h, "letterboxd" as const])) : undefined,
                focus: focus && handles.includes(focus) ? focus : null,
                maxRuntime,
                providers: [...providerFilter],
            }),
        onSuccess: setData,
    });

    const addHandle = () => {
        const h = input.trim().replace(/^@/, "").replace(/^https?:\/\/letterboxd\.com\//, "").replace(/\/$/, "");
        if (!h || handles.includes(h) || handles.length >= MAX_MEMBERS) return;
        setHandles((prev) => [...prev, h]);
        setInput("");
    };
    const removeHandle = (h: string) => {
        if (h === currentUsername) return; // you're always in the group
        setHandles((prev) => prev.filter((x) => x !== h));
        setForcedLb((prev) => {
            if (!prev.has(h)) return prev;
            const next = new Set(prev);
            next.delete(h);
            return next;
        });
    };
    const toggleSource = (h: string) =>
        setForcedLb((prev) => {
            const next = new Set(prev);
            if (next.has(h)) next.delete(h);
            else next.add(h);
            return next;
        });

    const sorted = useMemo(() => {
        if (!data) return [];
        const recs = [...data.recommendations];
        const pred = (r: GroupRecommendation) =>
            r.contributors.length ? r.contributors.reduce((s, c) => s + c.score, 0) / r.contributors.length : r.similarity_score;
        if (sort === "watchlisted") recs.sort((a, b) => b.watchlisted_by.length - a.watchlisted_by.length || pred(b) - pred(a));
        else if (sort === "prediction") recs.sort((a, b) => pred(b) - pred(a));
        else recs.sort((a, b) => (b.movie.vectorbox_score ?? 0) - (a.movie.vectorbox_score ?? 0));
        return recs;
    }, [data, sort]);

    // agreement signal: how tight the per-member scores are across the recs
    const agreement = useMemo(() => {
        if (!data) return null;
        const spreads = data.recommendations
            .filter((r) => r.contributors.length >= 2)
            .map((r) => {
                const scores = r.contributors.map((c) => c.score);
                return 1 - (Math.max(...scores) - Math.min(...scores));
            });
        if (!spreads.length) return null;
        return spreads.reduce((s, v) => s + v, 0) / spreads.length;
    }, [data]);

    const canRun = handles.length >= 2 && !runMutation.isPending;

    return (
        <div className="space-y-6 pt-6">
            <div>
                <h1 className="font-display text-2xl uppercase tracking-[-0.02em] text-fg">
                    handles · {handles.length}/{MAX_MEMBERS}
                </h1>
                <p className="tiny mt-1">{t("grp.subtitle")}</p>
                <p className="tiny mt-0.5 text-fg-3">
                    {t("grp.source_hint_1")} <span className="text-fg-2">{t("grp.source_hint_auto")}</span>{" "}
                    {t("grp.source_hint_2")} <span className="text-primary">{t("grp.source_hint_lbxd")}</span>{" "}
                    {t("grp.source_hint_3")}
                </p>
            </div>

            {/* INPUT BAR — handle chips */}
            <div className="border border-border-2 bg-bg-2 p-3.5">
                <div className="flex flex-wrap items-center gap-2">
                    {handles.map((h, i) => (
                        <span key={h} className="inline-flex items-center border border-border-2 bg-bg-3 font-mono text-[11px]">
                            <span
                                className="px-[7px] py-[4px] font-display text-[10px] font-bold tracking-[0.05em] text-black"
                                style={{ background: tokenColor(i) }}
                            >
                                {tokenLetter(i)}
                            </span>
                            <span className="px-2 py-[4px] text-fg">@{h}</span>
                            {h !== currentUsername ? (
                                <>
                                    {/* B-34 source toggle: auto (vectorbox preferred) ↔ forced letterboxd RSS */}
                                    <button
                                        onClick={() => toggleSource(h)}
                                        title={
                                            forcedLb.has(h)
                                                ? "Reading the public Letterboxd RSS — click for auto (VectorBox account preferred)"
                                                : "Auto: uses the VectorBox account if one matches — click to force Letterboxd RSS"
                                        }
                                        className={cn(
                                            "border-l border-border-2 px-[7px] py-[4px] font-display text-[9px] uppercase tracking-[0.05em] transition-colors",
                                            forcedLb.has(h) ? "text-primary" : "text-fg-3 hover:text-fg-2"
                                        )}
                                    >
                                        {forcedLb.has(h) ? "lbxd" : "auto"}
                                    </button>
                                    <button
                                        onClick={() => removeHandle(h)}
                                        aria-label={`Remove ${h}`}
                                        className="border-l border-border-2 px-[7px] py-[4px] text-[11px] text-fg-3 transition-colors hover:text-danger"
                                    >
                                        ×
                                    </button>
                                </>
                            ) : (
                                <span className="border-l border-dashed border-border-2 px-[7px] py-[4px] text-[9px] uppercase text-fg-3">you</span>
                            )}
                        </span>
                    ))}
                    {handles.length < MAX_MEMBERS && (
                        <form
                            onSubmit={(e) => {
                                e.preventDefault();
                                addHandle();
                            }}
                            className="flex items-center gap-1.5 border border-dashed border-border-2 px-2 py-1"
                        >
                            <input
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                placeholder={t("grp.paste")}
                                className="w-32 bg-transparent font-mono text-[11px] text-fg placeholder:text-fg-3 focus:outline-none"
                            />
                            <span className="font-display text-[9px] text-fg-3">↵</span>
                        </form>
                    )}
                    <div className="flex-1" />
                    <button
                        onClick={() => runMutation.mutate()}
                        disabled={!canRun}
                        className="flex items-center gap-2 bg-primary px-3.5 py-1.5 font-display text-[11px] font-bold tracking-[0.1em] text-primary-ink transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
                    >
                        {runMutation.isPending && <Loader2 className="size-3 animate-spin" />}
                        {t("grp.run")}
                    </button>
                </div>
                {/* session options — favours · runtime · providers (re-run to apply) */}
                {handles.length >= 2 && (
                    <div className="mt-3 space-y-2 border-t border-dashed border-border-2 pt-3">
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                            <span className="w-[92px] font-mono text-[10px] uppercase tracking-[0.08em] text-fg-3">{t("grp.favours")}</span>
                            <div className="flex border border-border-2">
                                <button
                                    onClick={() => setFocus(null)}
                                    className={cn(
                                        "border-r border-border-2 px-2.5 py-1 font-mono text-[10px] transition-colors",
                                        focus === null ? "bg-primary font-bold text-primary-ink" : "text-fg-3 hover:text-fg"
                                    )}
                                >
                                    {t("grp.balanced")}
                                </button>
                                {handles.map((h, i) => (
                                    <button
                                        key={h}
                                        onClick={() => setFocus(h)}
                                        title={`Lean the ranking toward @${h}'s taste`}
                                        className={cn(
                                            "flex items-center gap-1.5 border-r border-border-2 px-2.5 py-1 font-mono text-[10px] transition-colors last:border-r-0",
                                            focus === h ? "bg-primary font-bold text-primary-ink" : "text-fg-2 hover:text-fg"
                                        )}
                                    >
                                        <span
                                            className="inline-block size-[9px] font-display font-bold leading-none"
                                            style={{ background: tokenColor(i) }}
                                        >
                                            <span className="sr-only">{tokenLetter(i)}</span>
                                        </span>
                                        {h}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                            <span className="w-[92px] font-mono text-[10px] uppercase tracking-[0.08em] text-fg-3">{t("grp.max_runtime")}</span>
                            <div className="flex border border-border-2">
                                {[
                                    { v: null, label: t("grp.any") },
                                    { v: 90, label: "≤ 90m" },
                                    { v: 120, label: "≤ 2h" },
                                    { v: 150, label: "≤ 2h30" },
                                ].map((o) => (
                                    <button
                                        key={o.label}
                                        onClick={() => setMaxRuntime(o.v)}
                                        className={cn(
                                            "border-r border-border-2 px-2.5 py-1 font-mono text-[10px] transition-colors last:border-r-0",
                                            maxRuntime === o.v ? "bg-primary font-bold text-primary-ink" : "text-fg-3 hover:text-fg"
                                        )}
                                    >
                                        {o.label}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                            <span className="w-[92px] font-mono text-[10px] uppercase tracking-[0.08em] text-fg-3">{t("grp.providers")}</span>
                            <div className="flex flex-wrap gap-1">
                                {getProvidersForCountry("ES").map((p) => {
                                    const on = providerFilter.has(p.name);
                                    return (
                                        <button
                                            key={p.id}
                                            onClick={() =>
                                                setProviderFilter((prev) => {
                                                    const next = new Set(prev);
                                                    if (next.has(p.name)) next.delete(p.name);
                                                    else next.add(p.name);
                                                    return next;
                                                })
                                            }
                                            className={cn(
                                                "border px-2 py-1 font-mono text-[10px] lowercase transition-colors",
                                                on ? "border-primary bg-primary/10 text-primary" : "border-border-2 text-fg-3 hover:border-fg-3"
                                            )}
                                        >
                                            {p.name}
                                        </button>
                                    );
                                })}
                                <span className="self-center font-mono text-[9px] text-fg-3">{t("grp.empty_rerun")}</span>
                            </div>
                        </div>
                    </div>
                )}
                {handles.length < 2 && (
                    <p className="mt-2 font-mono text-[10px] text-fg-3">{t("grp.add_more")}</p>
                )}
                {runMutation.isError && (
                    <p className="mt-2 font-mono text-[10px] text-danger">
                        {t("grp.failed")}
                    </p>
                )}
            </div>

            {runMutation.isPending && (
                <div className="border border-dashed border-border-2 bg-bg-2 p-10 text-center font-mono text-xs uppercase tracking-widest text-fg-3">
                    {t("grp.computing")}
                </div>
            )}

            {data && !runMutation.isPending && (
                <>
                    {/* PEER CARDS */}
                    <div>
                        <div className="mb-2 flex items-baseline justify-between">
                            <span className="eyebrow">{t("grp.peers")}</span>
                            <button
                                onClick={() => setShareGroup(true)}
                                className="font-mono text-[10px] uppercase tracking-[0.08em] text-primary hover:underline"
                            >
                                {t("grp.share_group")}
                            </button>
                        </div>
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                            {/* tokens by RESPONSE position — lookup via typed handles broke on any
                                casing/normalization mismatch and stamped every card "A" */}
                            {data.members.map((mbr, i) => {
                                return (
                                    <div key={mbr.username} className="flex gap-3 border border-border-2 bg-bg-2 p-3.5">
                                        <div
                                            className="flex size-[42px] shrink-0 items-center justify-center font-display text-[22px] font-bold text-black"
                                            style={{ background: tokenColor(i) }}
                                        >
                                            {tokenLetter(i)}
                                        </div>
                                        <div className="min-w-0 flex-1">
                                            <div className="truncate font-mono text-[13px] text-fg">@{mbr.username}</div>
                                            <div className="mt-0.5 font-mono text-[10px] text-fg-3">
                                                {mbr.source === "vectorbox"
                                                    ? `${mbr.films ?? 0} rated · vectorbox`
                                                    : "letterboxd guest · public diary"}
                                            </div>
                                            {mbr.username !== currentUsername && (
                                                <button
                                                    onClick={() => setSharePeer(mbr.username)}
                                                    className="mt-1.5 font-mono text-[10px] uppercase tracking-[0.05em] text-fg-3 transition-colors hover:text-primary"
                                                >
                                                    you × {tokenLetter(i)} ↗
                                                </button>
                                            )}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                        {data.members.some((mbr) => mbr.source === "letterboxd") && (
                            <p className="mt-2 border border-dashed border-border-2 bg-bg-2 px-3 py-2 font-mono text-[10px] leading-relaxed text-fg-3">
                                <span className="text-primary">{t("grp.guest_tip_1")}</span> ·{" "}
                                {data.members
                                    .filter((mbr) => mbr.source === "letterboxd")
                                    .map((mbr) => `@${mbr.username}`)
                                    .join(" + ")}{" "}
                                {data.members.filter((mbr) => mbr.source === "letterboxd").length > 1
                                    ? t("grp.guest_tip_plural")
                                    : t("grp.guest_tip_single")}{" "}
                                {t("grp.guest_tip_2")}
                            </p>
                        )}
                    </div>

                    {/* CENTROID PANEL */}
                    <div className="border-2 border-primary bg-bg-2 p-4">
                        <div className="mb-2 font-display text-[11px] uppercase tracking-[0.18em] text-primary">shared centroid</div>
                        <div className="grid grid-cols-1 gap-4 font-mono text-[11px] text-fg-2 sm:grid-cols-3">
                            <div>
                                <div className="eyebrow mb-1">members</div>
                                <div className="text-[13px] font-semibold text-fg">
                                    {data.members.length} handles · {data.members.filter((mbr) => mbr.source === "vectorbox").length} on vectorbox
                                </div>
                            </div>
                            <div>
                                <div className="eyebrow mb-1">candidate pool</div>
                                <div className="text-[13px] font-semibold text-fg">top {data.recommendations.length} · unseen by all</div>
                            </div>
                            <div>
                                <div className="eyebrow mb-1">agreement signal</div>
                                <div className="text-[13px] font-semibold text-fg">
                                    {agreement != null
                                        ? `${agreement.toFixed(2)} · ${agreement >= 0.8 ? "strong · safe to recommend" : agreement >= 0.6 ? "moderate" : "divergent tastes"}`
                                        : "—"}
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* MATRIX */}
                    <div>
                        <div className="mb-2 flex items-baseline justify-between">
                            <span className="eyebrow">recommendations · matrix</span>
                            <div className="flex items-center gap-1 font-mono text-[10px] text-fg-3">
                                <span>sort:</span>
                                {(["prediction", "watchlisted", "quality"] as SortKey[]).map((s) => (
                                    <button
                                        key={s}
                                        onClick={() => setSort(s)}
                                        className={cn(
                                            "px-1.5 py-0.5 transition-colors",
                                            sort === s ? "text-primary underline underline-offset-4" : "text-fg-2 hover:text-fg"
                                        )}
                                    >
                                        {s}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <div className="border border-border-2 bg-bg-2">
                            <div className="grid grid-cols-[26px_44px_1fr_auto_64px_50px] items-center gap-2 border-b border-border-2 bg-bg-3 px-3.5 py-2.5">
                                <div className="font-display text-[9px] uppercase tracking-[0.15em] text-fg-3">#</div>
                                <div />
                                <div className="font-display text-[9px] uppercase tracking-[0.15em] text-fg-3">film</div>
                                <div className="text-center font-display text-[9px] uppercase tracking-[0.15em] text-fg-3">{t("grp.watchlisted")}</div>
                                <div className="text-right font-display text-[9px] uppercase tracking-[0.15em] text-fg-3">predict</div>
                                <div className="text-right font-display text-[9px] uppercase tracking-[0.15em] text-fg-3">Q</div>
                            </div>
                            {sorted.map((r, i) => {
                                const pred = r.contributors.length
                                    ? r.contributors.reduce((s, c) => s + c.score, 0) / r.contributors.length
                                    : r.similarity_score;
                                const why =
                                    r.watchlisted_by.length > 0
                                        ? `on ${r.watchlisted_by.map((w) => `@${w}`).join(" + ")}'s watchlist`
                                        : "centroid pick · unseen by all";
                                return (
                                    <button
                                        key={r.movie.tmdb_id}
                                        onClick={() =>
                                            setQuickLook({
                                                tmdb_id: r.movie.tmdb_id,
                                                title: r.movie.title,
                                                year: r.movie.year,
                                                runtime: r.movie.runtime,
                                                overview: r.movie.overview,
                                                poster_url: r.movie.poster_path,
                                                q: r.movie.vectorbox_score,
                                                contextLine: r.contributors
                                                    .map((c) => `@${c.username} ${(c.score * 100).toFixed(0)}%`)
                                                    .join(" · "),
                                            })
                                        }
                                        className={cn(
                                            "grid w-full grid-cols-[26px_44px_1fr_auto_64px_50px] items-center gap-2 px-3.5 py-2.5 text-left transition-colors hover:bg-bg-3",
                                            i > 0 && "border-t border-dashed border-border-2"
                                        )}
                                    >
                                        <span className="font-display text-[11px] text-fg-3">{String(i + 1).padStart(2, "0")}</span>
                                        <div className="poster-art relative h-[54px] w-9 border border-border-2">
                                            {r.movie.poster_path && (
                                                <Image
                                                    src={getTMDBImageUrl(r.movie.poster_path, "w154")}
                                                    alt={r.movie.title}
                                                    fill
                                                    sizes="36px"
                                                    className="object-cover"
                                                />
                                            )}
                                        </div>
                                        <div className="min-w-0">
                                            <div className="truncate font-mono text-[13px] text-fg">
                                                {language === "es" && r.movie.title_es ? r.movie.title_es : r.movie.title}{" "}
                                                <span className="text-fg-3">· {r.movie.year}</span>
                                            </div>
                                            <div className="mt-px truncate font-mono text-[10px] text-fg-3">{why}</div>
                                        </div>
                                        <div className="flex justify-center gap-[3px]">
                                            {/* Only VectorBox members can light up — guest (RSS) watchlists
                                                aren't reachable, so their slot shows a dimmed token. */}
                                            {data.members.map((mbr2, j) => {
                                                const on = r.watchlisted_by.includes(mbr2.username);
                                                const guest = mbr2.source === "letterboxd";
                                                return (
                                                    <span
                                                        key={mbr2.username}
                                                        title={
                                                            guest
                                                                ? `@${mbr2.username} — letterboxd guest · watchlist not visible`
                                                                : `@${mbr2.username} ${on ? "watchlisted" : "not on watchlist"}`
                                                        }
                                                        className="flex size-[18px] items-center justify-center font-display text-[9px] font-bold text-black"
                                                        style={{
                                                            background: on ? tokenColor(j) : "transparent",
                                                            border: on ? "1px solid transparent" : "1px dashed var(--border-2)",
                                                            color: on ? undefined : "var(--fg-3)",
                                                            opacity: guest && !on ? 0.45 : 1,
                                                        }}
                                                    >
                                                        {on ? tokenLetter(j) : guest ? "·" : ""}
                                                    </span>
                                                );
                                            })}
                                        </div>
                                        <div className="text-right">
                                            <div className="font-display text-[13px] font-bold text-primary">{(pred * 100).toFixed(0)}%</div>
                                            <div className="font-mono text-[9px] text-fg-3">{t("grp.predicted")}</div>
                                        </div>
                                        {/* Q as the app-wide filled badge — glance-readable next to the lime predicted % */}
                                        <div className="flex justify-end">
                                            {r.movie.vectorbox_score != null ? (
                                                <span className="bg-primary px-1.5 py-1 font-display text-[11px] font-bold leading-none text-primary-ink">
                                                    Q{Math.round(r.movie.vectorbox_score)}
                                                </span>
                                            ) : (
                                                <span className="font-mono text-[11px] text-fg-3">—</span>
                                            )}
                                        </div>
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                </>
            )}

            <QuickLook film={quickLook} context="grp" onClose={() => setQuickLook(null)} />

            {/* shareables (F3) — PNG-only per locked decision */}
            {shareGroup && data && <GroupCardModal data={data} onClose={() => setShareGroup(false)} />}
            {sharePeer && data && (
                <PairCardModal data={data} you={currentUsername} peer={sharePeer} onClose={() => setSharePeer(null)} />
            )}
        </div>
    );
}
