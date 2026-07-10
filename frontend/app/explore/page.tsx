"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FeedContainer } from "@/components/feed-container";
import { api, markWatched, rejectMovie, type FeedItem } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import { scheduleFeedInvalidation } from "@/lib/feed-invalidation";
import { useLanguage } from "@/components/language-provider";
import { LanguageToggle } from "@/components/language-toggle";
import { RightConsole } from "@/components/right-console";
import { cn } from "@/lib/utils";

export default function ExplorePage() {
    const { push } = useRouter();
    const { t } = useLanguage();
    const searchParams = useSearchParams();
    const queryClient = useQueryClient();
    const [userId, setUserId] = useState<number | null>(null);
    const [isReady, setIsReady] = useState(false);

    // Inspector-only rail (like the watchlist): the DATA_INSPECTOR appears when a
    // card's info is pressed and hides on close — no persistent filter console.
    const [inspected, setInspected] = useState<{ movie: FeedItem; sectionId?: string } | null>(null);
    const [actionLoading, setActionLoading] = useState<"watched" | "rejected" | null>(null);
    const handleMarkWatched = async (tmdbId: number) => {
        setActionLoading("watched");
        try { await markWatched(tmdbId); scheduleFeedInvalidation(queryClient); setInspected(null); }
        catch (e) { console.error("mark watched failed:", e); }
        finally { setActionLoading(null); }
    };
    const handleReject = async (tmdbId: number) => {
        setActionLoading("rejected");
        try { await rejectMovie(tmdbId); scheduleFeedInvalidation(queryClient); setInspected(null); }
        catch (e) { console.error("reject failed:", e); }
        finally { setActionLoading(null); }
    };

    useEffect(() => {
        const initSession = async () => {
            try {
                const { data } = await api.post("/api/onboarding/init-session");
                setUserId(data.user_id);
            } catch (e) {
                console.error("Failed to init anonymous session:", e);
            } finally {
                setIsReady(true);
            }
        };
        initSession();
    }, []);

    // Refresh the feed immediately when a guest returns from /onboarding via
    // the VIEW EXPLORE button — they just rated films and the cached feed
    // doesn't reflect them yet. delayMs=0 bypasses the 3s debounce since
    // this is an explicit user "show me my new feed" intent.
    useEffect(() => {
        if (searchParams.get("onboarding_complete") === "true") {
            scheduleFeedInvalidation(queryClient, 0);
            const url = new URL(window.location.href);
            url.searchParams.delete("onboarding_complete");
            window.history.replaceState({}, "", url.pathname);
        }
    }, [searchParams, queryClient]);

    return (
        <div className="flex min-h-screen flex-col bg-bg text-fg">
            {/* Top bar */}
            <header className="shrink-0 border-b border-border-2 px-4 py-3">
                <div className="mx-auto flex max-w-[1600px] items-center justify-between">
                    <Link href="/explore" className="font-display text-lg uppercase tracking-tight">
                        <span className="text-fg">VECTOR</span>
                        <span className="ml-0.5 bg-primary px-1 text-primary-ink">BOX</span>
                    </Link>
                    <div className="flex items-center gap-2">
                        <span className="mr-1 hidden font-mono text-[9px] uppercase tracking-[0.18em] text-fg-3 sm:inline">
                            {t("explore.guest_session")}
                        </span>
                        <LanguageToggle />
                        <Link
                            href="/login"
                            className="border border-border-2 px-3 py-1.5 font-mono text-xs uppercase text-fg-2 transition-colors hover:border-fg-3 hover:text-fg"
                        >
                            {t("explore.signin")}
                        </Link>
                        <Link
                            href="/register"
                            className="bg-primary px-3 py-1.5 font-display text-xs font-bold uppercase text-primary-ink"
                        >
                            {t("explore.create")}
                        </Link>
                    </div>
                </div>
            </header>

            {/* Guest banner */}
            <div className="shrink-0 border-b border-border-2 bg-bg-2 px-4 py-2.5">
                <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-3">
                    <div className="font-mono text-xs text-fg-2">
                        <span className="mr-2 bg-primary px-1.5 py-0.5 font-display text-[9px] font-bold uppercase tracking-[0.1em] text-primary-ink">
                            {t("explore.guest_feed")}
                        </span>
                        {t("explore.save_note")}
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <button
                            onClick={() => push("/onboarding")}
                            className="border border-border-2 px-3 py-1.5 font-mono text-xs uppercase text-fg-2 transition-colors hover:border-primary hover:text-primary"
                        >
                            {t("explore.rate_more")}
                        </button>
                        <Link
                            href="/register"
                            className="border border-primary px-3 py-1.5 font-mono text-xs font-bold uppercase text-primary transition-colors hover:bg-primary hover:text-primary-ink"
                        >
                            {t("explore.save_profile")}
                        </Link>
                    </div>
                </div>
            </div>

            <main className={cn("mx-auto w-full max-w-[1600px] flex-1 px-4 py-4", inspected && "lg:pr-80")}>
                {!isReady ? (
                    <div className="flex items-center justify-center py-20">
                        <div className="space-y-4 text-center">
                            <div className="mx-auto size-8 animate-spin border-2 border-primary border-t-transparent" />
                            <p className="font-mono text-xs uppercase tracking-widest text-fg-3">initializing_session…</p>
                        </div>
                    </div>
                ) : userId ? (
                    <FeedContainer
                        userId={userId}
                        scope="global"
                        onInspect={(movie, sectionId) => setInspected({ movie, sectionId })}
                        inspected={inspected}
                        onCloseInspect={() => setInspected(null)}
                    />
                ) : (
                    <div className="space-y-4 py-20 text-center">
                        <p className="font-display text-4xl text-primary">◐</p>
                        <p className="font-mono text-sm text-fg-3">{t("explore.init_failed")}</p>
                    </div>
                )}
            </main>

            {/* Inspector-only rail (desktop, fixed) — shown only when a card is
                inspected; the FeedContainer renders the inline inspector on mobile. */}
            {inspected && (
                <RightConsole
                    selectedMovie={inspected.movie}
                    selectedSectionId={inspected.sectionId}
                    onCloseInspector={() => setInspected(null)}
                    scope="global"
                    onScopeChange={() => {}}
                    countryCode="ES"
                    onCountryChange={() => {}}
                    streamingProviders={[]}
                    onToggleProvider={() => {}}
                    onClearFilters={() => {}}
                    onMarkWatched={handleMarkWatched}
                    onReject={handleReject}
                    inspectorActionLoading={actionLoading}
                    filteredCount={null}
                />
            )}
        </div>
    );
}
