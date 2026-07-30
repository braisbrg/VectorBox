"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import Image from "next/image";
import { SlidersHorizontal, X, Check, Loader2, RotateCw } from "lucide-react";
import { MovieCarousel } from "./movie-carousel";
import { FeedInlineInspector } from "@/components/feed-inline-inspector";
import { UploadZone } from "@/components/upload-zone";
import { AcidError } from "@/components/ui/acid-error";
import { InfoTooltip } from "./info-tooltip";
import { BracketToggle } from "@/components/ui/bracket-toggle";
import { dominantReason } from "@/components/why-breakdown";
import { useLanguage } from "@/components/language-provider";
import { getFeed, getWhy, setWatchlist, getTMDBImageUrl, getLetterboxdUrl, FeedResponse, VectorboxUser, FeedItem } from "@/lib/api";
import type { Contributor } from "@/types/feed";
import { cn } from "@/lib/utils";

interface FeedContainerProps {
    userId: number;
    scope: "global" | "watchlist";
    countryCode?: string;
    streamingProviders?: number[];
    initialData?: FeedResponse | null;
    registeredUsers?: VectorboxUser[];
    onInspect?: (movie: FeedItem, sectionId?: string) => void;
    /** Currently inspected movie — drives the mobile inline inspector (handoff 1C). */
    inspected?: { movie: FeedItem; sectionId?: string } | null;
    onCloseInspect?: () => void;
    /** F8: rail EXECUTE_QUERY result — a sectioned filtered feed (null = none active). */
    filteredResults?: FeedResponse | null;
    isFiltering?: boolean;
    onClearFilterResults?: () => void;
}

// section.id → sec_info.* i18n key; resolved via t() at render (see sectionTitle area).
// Rows that need minimum TASTE data to appear (backend returns nothing until a
// director hits 3+ high ratings, clusters exist, etc.) — drives the bottom hint.
const RATING_GATED_SECTIONS = ["picked_for_you", "because_you_watched", "auteur", "cult_actor"];

const SECTION_DESCRIPTIONS: Record<string, string> = {
    picked_for_you: "sec_info.picked_for_you",
    because_you_watched: "sec_info.because_you_watched",
    niche_picks: "sec_info.niche_picks",
    wildcard: "sec_info.wildcard",
    random_picks: "sec_info.random_picks",
    hidden_gems: "sec_info.hidden_gems",
    available_now: "sec_info.available_now",
    popular_letterboxd: "sec_info.popular_letterboxd",
    auteur: "sec_info.auteur",
    cult_actor: "sec_info.cult_actor",
    upcoming: "sec_info.upcoming",
    group_vibe: "sec_info.group_vibe",
};

const TITLE_MAP: Record<string, string> = {
    popular_movies: "sections.popular_letterboxd",
    hidden_gems: "sections.hidden_gems",
    random_picks: "sections.random_picks",
    wildcard: "sections.wildcard",
    available_now: "sections.available_now",
};

// The rotating niche section always ships id="niche_picks" (cache key) while
// its title rotates through the backend GLOBAL_THEMES — map the EN title back
// to its sections.* key so ES rotates too.
const NICHE_THEME_TITLES: Record<string, string> = {
    "sleep optional": "sections.sleep_optional",
    "comfort watch": "sections.comfort_watch",
    "your brain called": "sections.your_brain_called",
    "your parents haven't seen either": "sections.parents_havent_seen",
    "slow burn": "sections.slow_burn",
    "beautiful chaos": "sections.beautiful_chaos",
    "bring tissues": "sections.bring_tissues",
    "subtitles required": "sections.subtitles_required",
    "based on true crime": "sections.based_on_true_crime",
};

const EMPTY_PROVIDERS: number[] = [];
const HIDDEN_KEY = "vb_hidden_sections";

function agoLabel(ts: number): string {
    const m = Math.max(0, Math.round((Date.now() - ts) / 60000));
    if (m < 1) return "just now";
    if (m < 60) return `${m}m ago`;
    const h = Math.round(m / 60);
    return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

function contributorReason(c?: Contributor): string | undefined {
    if (!c) return undefined;
    if (c.seed_title) return `Because you watched ${c.seed_title}`;
    if (c.director) return `From the director ${c.director}`;
    if (c.actor) return `Featuring ${c.actor}`;
    if (c.cluster_name) return `From your cluster · ${c.cluster_name}`;
    if (c.label) return c.label;
    return undefined;
}

// Feed hero — prototype .feed-hero (vectorBox Prototype.html:336): 3-col grid
// [220px poster | body | vertical actions], wide-backdrop field, 3px lime left
// edge, corner-Q on the poster, tag → title → sub → synopsis → why-box → legs.
// "not tonight" is a SOFT skip (cycles to the next trident pick) — not a reject.
function FeedHero({ item, onSkip }: { item: FeedItem; onSkip: () => void }) {
    const { language, t } = useLanguage();
    const q = item.vectorbox_score ? Math.round(item.vectorbox_score) : null;
    const fallbackReason = contributorReason(item.contributors?.[0]);
    const displayTitle = language === "es" && item.title_es ? item.title_es : item.title;
    const displayOverview = language === "es" && item.overview_es ? item.overview_es : item.overview;
    const [onWatchlist, setOnWatchlist] = useState(false);
    const [saving, setSaving] = useState(false);

    const { data: why } = useQuery({
        queryKey: ["why", item.id],
        queryFn: () => getWhy(item.id),
        staleTime: 10 * 60 * 1000,
        retry: 1,
    });

    const toggleWatchlist = async () => {
        if (saving) return;
        setSaving(true);
        try {
            await setWatchlist(item.id, !onWatchlist);
            setOnWatchlist((v) => !v);
        } catch (e) {
            console.error("watchlist toggle failed", e);
        } finally {
            setSaving(false);
        }
    };

    const actionCls =
        "flex w-full items-center gap-2.5 border border-border-2 px-3.5 py-2.5 text-left font-display text-[11px] font-bold uppercase tracking-[0.1em] text-fg transition-colors hover:border-primary hover:text-primary disabled:opacity-50";

    return (
        <div
            className="relative grid grid-cols-[220px_1fr_auto] gap-6 overflow-hidden border border-border-2 p-5"
            style={{ background: "linear-gradient(135deg, var(--hero-from) 0%, var(--bg-deep) 110%)" }}
        >
            {/* 3px lime left edge (prototype .feed-hero::before) */}
            <span aria-hidden className="absolute inset-y-0 left-0 w-[3px] bg-primary" />
            {/* wide backdrop art — clearly visible field behind body + actions */}
            {(item.backdrop_url || item.poster_url) && (
                <div
                    aria-hidden
                    className={cn("pointer-events-none absolute inset-0", item.backdrop_url ? "opacity-[0.22]" : "opacity-[0.08]")}
                    style={{
                        backgroundImage: `url(${getTMDBImageUrl(item.backdrop_url || item.poster_url!, "w1280")})`,
                        backgroundSize: "cover",
                        backgroundPosition: "center 25%",
                        maskImage: "linear-gradient(90deg, transparent 5%, black 45%)",
                    }}
                />
            )}

            {/* poster — corner Q top-right */}
            <Link
                href={getLetterboxdUrl(item.id)}
                target="_blank"
                rel="noopener noreferrer"
                className="poster-art relative aspect-[2/3] min-h-[280px] overflow-hidden border border-border-2"
            >
                {item.poster_url && (
                    <Image src={getTMDBImageUrl(item.poster_url)} alt={item.title} fill className="object-cover" sizes="220px" priority />
                )}
                {q != null && q > 0 && (
                    <span className="absolute right-0 top-0 bg-primary px-2.5 py-[5px] font-display text-sm font-bold tracking-[0.04em] text-primary-ink">
                        Q{q}
                    </span>
                )}
            </Link>

            {/* body — tag → title → sub → why box → trident legs */}
            <div className="relative flex min-w-0 flex-col gap-2.5 py-1.5">
                <div className="flex items-center gap-2 font-display text-[10px] uppercase tracking-[0.2em] text-primary">
                    {why?.rank != null && (
                        <span className="bg-primary px-1.5 py-0.5 font-bold text-primary-ink">#{why.rank}</span>
                    )}
                    {t("hero.tonight")}
                </div>

                <h2 className="font-display text-5xl uppercase leading-[0.95] tracking-[-0.02em] text-fg">{displayTitle}</h2>

                <div className="flex flex-wrap items-baseline gap-2 font-mono text-[13px] text-fg-2">
                    {item.year && <span>{item.year}</span>}
                    {item.runtime ? (
                        <>
                            <span className="text-fg-3">·</span>
                            <span>{item.runtime >= 60 ? `${Math.floor(item.runtime / 60)}h ${item.runtime % 60}m` : `${item.runtime}m`}</span>
                        </>
                    ) : null}
                    {item.letterboxd_rating != null && (
                        <>
                            <span className="text-fg-3">·</span>
                            <span>★ {item.letterboxd_rating.toFixed(1)}</span>
                        </>
                    )}
                    {item.streaming_providers?.length ? (
                        <>
                            <span className="text-fg-3">·</span>
                            <span className="lowercase">{item.streaming_providers.slice(0, 2).join(" · ")}</span>
                        </>
                    ) : null}
                </div>

                {displayOverview && (
                    <p className="line-clamp-3 max-w-[520px] font-mono text-xs leading-relaxed text-fg-2">{displayOverview}</p>
                )}

                {(why || fallbackReason) && (
                    <div className="mt-1.5 max-w-[520px] border border-border-2 bg-black/40 px-3 py-2.5 font-mono text-xs leading-[1.55] text-fg-2">
                        <b className="font-semibold text-primary">{t("hero.why_this")}</b>{" "}
                        {why ? dominantReason(why, t) : fallbackReason}
                        {why?.cluster ? <> {t("hero.cluster")} {why.cluster.name.toLowerCase()}.</> : null}
                    </div>
                )}

                <div className="mt-auto flex gap-4 pt-2 font-mono text-[11px]">
                    {why ? (
                        <>
                            <HeroLeg label="vibe" value={why.trident.vibe.toFixed(2)} valueCls="text-primary" />
                            <HeroLeg label="auteur" value={why.trident.auteur.toFixed(2)} valueCls="text-accent-purple" />
                            <HeroLeg label="gems" value={why.trident.gems.toFixed(2)} />
                            {why.neighbors?.[0] && <HeroLeg label="d" value={why.neighbors[0].dist.toFixed(2)} />}
                        </>
                    ) : null}
                </div>
            </div>

            {/* actions — vertical stack, right (prototype .hero-actions) */}
            <div className="relative flex min-w-[170px] flex-col justify-end gap-2">
                <button
                    onClick={toggleWatchlist}
                    disabled={saving}
                    className={cn(
                        actionCls,
                        !onWatchlist && "border-primary bg-primary text-primary-ink hover:bg-transparent hover:text-primary"
                    )}
                >
                    {saving ? <Loader2 className="size-3.5 animate-spin" /> : onWatchlist ? <Check className="size-3.5" /> : null}
                    {onWatchlist ? t("hero.on_watchlist") : t("hero.add_watchlist")}
                </button>
                <Link href={`/why/${item.id}`} className={actionCls}>
                    {t("hero.why_link")}
                </Link>
                <Link href={`/movie/${item.id}`} className={actionCls}>
                    {t("hero.full_page")}
                </Link>
                <Link href={getLetterboxdUrl(item.id)} target="_blank" rel="noopener noreferrer" className={actionCls}>
                    letterboxd ↗
                </Link>
                <button onClick={onSkip} title={t("hero.not_tonight_tip")} className={cn(actionCls, "text-fg-3")}>
                    {t("hero.not_tonight")}
                </button>
            </div>
        </div>
    );
}

function HeroLeg({ label, value, valueCls }: { label: string; value: string; valueCls?: string }) {
    return (
        <div className="flex flex-col gap-0.5">
            <span className="font-display text-[9px] uppercase tracking-[0.18em] text-fg-3">{label}</span>
            <span className={cn("font-semibold text-fg", valueCls)}>{value}</span>
        </div>
    );
}

function ManageSections({
    sections,
    hidden,
    onToggle,
    onClose,
}: {
    sections: { id: string; title: string }[];
    hidden: Set<string>;
    onToggle: (id: string) => void;
    onClose: () => void;
}) {
    return (
        <div className="absolute right-0 top-full z-40 mt-2 w-72 border border-border-2 bg-bg p-3 shadow-acid">
            <div className="mb-2 flex items-center justify-between">
                <span className="eyebrow">manage sections</span>
                <button onClick={onClose} aria-label="Close" className="text-fg-3 hover:text-fg">
                    <X className="size-4" />
                </button>
            </div>
            <ul className="max-h-72 space-y-1 overflow-y-auto scrollbar-hide">
                {sections.map((s) => (
                    <li key={s.id} className="flex items-center justify-between gap-2 py-1">
                        <span className="truncate text-xs text-fg-2">{s.title}</span>
                        <BracketToggle
                            checked={!hidden.has(s.id)}
                            onChange={() => onToggle(s.id)}
                            label={`Show ${s.title}`}
                            className="min-h-[32px] min-w-[52px] px-2 py-1"
                        />
                    </li>
                ))}
            </ul>
        </div>
    );
}

export function FeedContainer({
    userId,
    scope,
    countryCode = "ES",
    streamingProviders = EMPTY_PROVIDERS,
    initialData,
    registeredUsers,
    onInspect,
    inspected,
    onCloseInspect,
    filteredResults,
    isFiltering,
    onClearFilterResults,
}: FeedContainerProps) {
    // Mobile toggle-close (handoff: tapping the inspected card again closes the panel).
    const handleInspect = (movie: FeedItem, sectionId?: string) => {
        if (
            typeof window !== "undefined" &&
            window.matchMedia("(max-width: 1023px)").matches &&
            inspected?.movie.id === movie.id &&
            inspected?.sectionId === sectionId
        ) {
            onCloseInspect?.();
        } else {
            onInspect?.(movie, sectionId);
        }
    };
    const { data: feedData, isLoading, error, dataUpdatedAt } = useQuery<FeedResponse>({
        queryKey: ["feed", userId, scope, countryCode, streamingProviders],
        queryFn: async () => getFeed(scope, countryCode, streamingProviders),
        staleTime: 5 * 60 * 1000,
        initialData: initialData ?? undefined,
        // Toggling a rail provider changes the key → background refetch. Keep the
        // current feed on screen so it doesn't visibly reload (rail filters only
        // take effect on EXECUTE_QUERY; the base feed shouldn't flicker meanwhile).
        placeholderData: (prev) => prev,
    });
    const { language, t } = useLanguage();
    const queryClient = useQueryClient();

    const [hidden, setHidden] = useState<Set<string>>(new Set());
    const [manageOpen, setManageOpen] = useState(false);
    const [heroSkip, setHeroSkip] = useState(0);

    useEffect(() => {
        try {
            const raw = localStorage.getItem(HIDDEN_KEY);
            if (raw) setHidden(new Set(JSON.parse(raw)));
        } catch {}
    }, []);

    const toggleHidden = (id: string) => {
        setHidden((prev) => {
            const next = new Set(prev);
            next.has(id) ? next.delete(id) : next.add(id);
            localStorage.setItem(HIDDEN_KEY, JSON.stringify([...next]));
            return next;
        });
    };

    // Backend titles ARE the English source of truth (incl. dynamic ones like
    // "Because You Love Villeneuve") — only translate when the UI is in es.
    const sectionTitle = (section: { id: string; title: string; type?: string }) => {
        if (language !== "es") return section.title;
        // dynamic seed title: translate the prefix, keep the film name
        if (section.id.startsWith("because_you_watched")) {
            const film = section.title.replace(/^because you watched\s*/i, "");
            return film && film !== section.title ? `${t("sections.byw_prefix")} ${film}` : section.title;
        }
        const key =
            (section.id === "niche_picks" && NICHE_THEME_TITLES[section.title.toLowerCase()]) ||
            TITLE_MAP[section.id] ||
            `sections.${section.id}`;
        const v = t(key);
        return v === key ? section.title : v;
    };

    if (isFiltering) {
        return (
            <div className="flex items-center justify-center py-20 font-mono text-xs uppercase tracking-widest text-fg-3">
                {t("feed_meta.executing")}
            </div>
        );
    }

    if (filteredResults !== null && filteredResults !== undefined) {
        // F8: render the filtered feed as SECTIONS (wide rows only) through the same
        // path as the live feed, instead of one flat "Filter results" carousel.
        const fsecs = filteredResults.feed;
        const total = fsecs.reduce((n, s) => n + s.items.length, 0);
        // Hero of the filtered feed = its top pick (picked_for_you), same as the live feed.
        const fHeroSection = fsecs.find((s) => s.id === "picked_for_you") ?? fsecs[0];
        const fHeroItem = fHeroSection?.items?.length
            ? fHeroSection.items[heroSkip % fHeroSection.items.length]
            : undefined;
        return (
            <div className="space-y-8 pt-6">
                <div className="flex items-center justify-between border border-border-2 px-4 py-2 font-mono text-xs">
                    <span className="text-fg-2">
                        {t("feed_meta.filtered_view")} · {total} {t("feed_meta.results")}
                    </span>
                    <button onClick={onClearFilterResults} className="text-primary hover:underline">
                        {t("feed_meta.back_to_feed")}
                    </button>
                </div>
                {total > 0 ? (
                    <div className="space-y-8">
                        {fHeroItem && (
                            <div className="hidden lg:block">
                                <FeedHero item={fHeroItem} onSkip={() => setHeroSkip((s) => s + 1)} />
                            </div>
                        )}
                        <div className="space-y-2">
                        {fsecs.map((section, index) => (
                            <div key={section.id}>
                                <MovieCarousel
                                    title={sectionTitle(section)}
                                    items={section.items}
                                    heroId={section.id === fHeroSection?.id ? fHeroItem?.id : undefined}
                                    userId={userId}
                                    sectionId={section.id}
                                    type={section.type}
                                    priority={index === 0}
                                    onInspect={handleInspect}
                                    titlePrefix={
                                        SECTION_DESCRIPTIONS[section.id] ? (
                                            <InfoTooltip
                                                id={`feed-section-${section.id}`}
                                                title={sectionTitle(section)}
                                                description={t(SECTION_DESCRIPTIONS[section.id])}
                                            />
                                        ) : undefined
                                    }
                                />
                                {inspected?.sectionId === section.id && (
                                    <FeedInlineInspector movie={inspected.movie} onClose={() => onCloseInspect?.()} />
                                )}
                            </div>
                        ))}
                        </div>
                    </div>
                ) : (
                    <div className="py-16 text-center font-mono text-xs uppercase tracking-widest text-fg-3">{t("feed_meta.no_results")}</div>
                )}
            </div>
        );
    }

    if (isLoading) {
        return (
            <div className="space-y-10 pt-6" role="status" aria-label="Loading recommendations" aria-live="polite">
                {[1, 2, 3, 4].map((i) => (
                    <div key={i} className="space-y-3">
                        <div className="h-5 w-40 animate-pulse bg-bg-3" />
                        <div className="flex gap-2.5 overflow-hidden md:gap-3">
                            {[1, 2, 3, 4, 5, 6].map((j) => (
                                <div key={j} className="h-[210px] w-[118px] shrink-0 animate-pulse border border-border bg-bg-2 md:h-[270px] md:w-[180px]" />
                            ))}
                        </div>
                    </div>
                ))}
            </div>
        );
    }

    if (error) {
        return (
            <div className="flex w-full justify-center pt-6">
                <AcidError message="DATA_STREAM_INTERRUPTED" onRetry={() => window.location.reload()} className="max-w-2xl" />
            </div>
        );
    }

    if (!feedData || feedData.feed.length === 0 || feedData.status === "incomplete") {
        const isIncomplete = feedData?.status === "incomplete";
        return (
            <div className="flex min-h-[50vh] flex-col items-center justify-center gap-8 pt-6">
                <div className="space-y-2 text-center">
                    <h2 className={cn("font-display text-2xl uppercase", isIncomplete ? "text-warn" : "text-primary")}>
                        {isIncomplete ? t("feed_meta.data_incomplete") : t("feed_meta.data_missing")}
                    </h2>
                    <p className="mx-auto max-w-md text-sm text-fg-2">
                        {isIncomplete ? t("feed_meta.data_incomplete_body") : t("feed_meta.data_missing_body")}
                    </p>
                </div>
                <div className="w-full max-w-xl border border-border-2 bg-bg-2 p-6 shadow-acid">
                    <UploadZone
                        registeredUsers={registeredUsers || [{ id: userId, username: "" }]}
                        activeSessionUserId={userId}
                        onUploadSuccess={() => window.location.reload()}
                        onUserCreated={() => {}}
                    />
                </div>
            </div>
        );
    }

    const visible = feedData.feed.filter((s) => !hidden.has(s.id));
    // Hero = the top TRIDENT pick (picked_for_you — the personalized RRF ranking,
    // same list /why ranks against), NOT just whatever section renders first.
    // "not tonight" cycles heroSkip through that list without rejecting anything.
    const heroSection = visible.find((s) => s.id === "picked_for_you") ?? visible[0];
    const heroItem = heroSection?.items?.length ? heroSection.items[heroSkip % heroSection.items.length] : undefined;

    return (
        <div className="space-y-8 pt-6">
            {/* Feed header — freshness · refresh · manage-sections */}
            <div className="relative flex items-center justify-between">
                <p className="eyebrow text-fg-3">
                    {t("feed_meta.picked")} · {visible.length} {t("feed_meta.sections")}
                </p>
                <div className="flex items-center gap-2">
                    {dataUpdatedAt > 0 && (
                        <span className="hidden font-mono text-[10px] text-fg-3 sm:inline">generated {agoLabel(dataUpdatedAt)}</span>
                    )}
                    <button
                        onClick={() => queryClient.invalidateQueries({ queryKey: ["feed"] })}
                        title={t("feed_meta.refresh")}
                        aria-label={t("feed_meta.refresh")}
                        className="inline-flex items-center gap-2 border border-border-2 px-2.5 py-1.5 text-[10px] uppercase tracking-wider text-fg-3 transition-colors hover:border-primary hover:text-primary"
                    >
                        <RotateCw className="size-3.5" />
                    </button>
                    <button
                        onClick={() => setManageOpen((o) => !o)}
                        className="inline-flex items-center gap-2 border border-border-2 px-2.5 py-1.5 text-[10px] uppercase tracking-wider text-fg-3 transition-colors hover:border-primary hover:text-primary"
                    >
                        <SlidersHorizontal className="size-3.5" /> {t("feed_meta.manage")}
                    </button>
                </div>
                {manageOpen && (
                    <ManageSections
                        sections={feedData.feed.map((s) => ({ id: s.id, title: sectionTitle(s) }))}
                        hidden={hidden}
                        onToggle={toggleHidden}
                        onClose={() => setManageOpen(false)}
                    />
                )}
            </div>

            {/* Hero strip — desktop only (handoff mobile feed has no hero) */}
            {heroItem && (
                <div className="hidden lg:block">
                    <FeedHero item={heroItem} onSkip={() => setHeroSkip((s) => s + 1)} />
                </div>
            )}

            {/* Rows — on mobile, the inline inspector (1C) injects below the inspected row.
                The hero film is removed from its own row (desktop) so it never shows twice. */}
            <div className="space-y-2">
                {visible.map((section, index) => (
                    <div key={section.id}>
                        <MovieCarousel
                            title={sectionTitle(section)}
                            items={section.items}
                            heroId={section.id === heroSection?.id ? heroItem?.id : undefined}
                            userId={userId}
                            sectionId={section.id}
                            type={section.type}
                            forceVectorBoxScore={section.id === "wildcard"}
                            priority={index === 0}
                            onInspect={handleInspect}
                            titlePrefix={
                                SECTION_DESCRIPTIONS[section.id] ? (
                                    <InfoTooltip
                                        id={`feed-section-${section.id}`}
                                        title={sectionTitle(section)}
                                        description={t(SECTION_DESCRIPTIONS[section.id])}
                                    />
                                ) : undefined
                            }
                        />
                        {inspected?.sectionId === section.id && (
                            <FeedInlineInspector movie={inspected.movie} onClose={() => onCloseInspect?.()} />
                        )}
                    </div>
                ))}
            </div>

            {/* B-31 (minimal, per user): when rating-gated rows are missing (sparse taste
                data), a single muted line at the very bottom — non-invasive, only seen
                if you scroll looking for more. */}
            {RATING_GATED_SECTIONS.some((id) => !feedData.feed.some((s) => s.id === id)) && (
                <p className="pt-2 text-center font-mono text-[10px] lowercase tracking-wide text-fg-3">
                    {t("feed_meta.more_rows_hint")}
                </p>
            )}
        </div>
    );
}
