"use client";

import { useState, useEffect } from "react";
import { Wordmark } from "@/components/ui/wordmark";
import { useRouter, usePathname } from "next/navigation";
import { Loader2, LogOut } from "lucide-react";
import { useUser } from "@clerk/nextjs";
import { useQueryClient } from "@tanstack/react-query";

import { getProvidersForCountry } from "@/lib/constants";
import {
    api,
    VectorboxUser,
    UserSession,
    getCurrentUser,
    getUsers,
    FeedItem,
    FeedResponse,
    FilterSearchParams,
    getFilteredFeed,
    markWatched,
    rejectMovie,
    USER_SESSION_KEY,
} from "@/lib/api";
import { useVectorboxLogout } from "@/hooks/useVectorboxLogout";
import { cn } from "@/lib/utils";
import { useLanguage } from "@/components/language-provider";
import { scheduleFeedInvalidation, cancelPendingFeedInvalidation } from "@/lib/feed-invalidation";

import { Sidebar } from "@/components/sidebar";
import { ShellTopbar } from "@/components/shell/topbar";
import { MobileTabBar } from "@/components/shell/mobile-tab-bar";
import { RightConsole } from "@/components/right-console";
import { MagicBoxModal } from "@/components/magic-box";
import { MobileInspector } from "@/components/shell/mobile-inspector";
import { FeedFilterSheet } from "@/components/shell/feed-filter-sheet";
import { ImportWizard } from "@/components/onboarding/import-wizard";
import { ShellProvider } from "@/components/shell/shell-context";

export function AppShell({ children }: { children: React.ReactNode }) {
    const router = useRouter();
    const pathname = usePathname();
    const { user: clerkUser, isLoaded: isClerkLoaded } = useUser();
    const handleLogout = useVectorboxLogout();
    const { t } = useLanguage();
    const queryClient = useQueryClient();

    const [currentUserSession, setCurrentUserSession] = useState<UserSession | null>(null);
    const [users, setUsers] = useState<VectorboxUser[]>([]);
    const [isLoadingAuth, setIsLoadingAuth] = useState(true);
    const [ratingsCount, setRatingsCount] = useState(0);

    // Shared shell state (formerly dashboard.tsx)
    const [scope, setScope] = useState<"watchlist" | "global">("global");
    const [countryCode, setCountryCode] = useState("ES");
    const [streamingProviders, setStreamingProviders] = useState<number[]>([]);
    const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

    // Sidebar V2: persist collapse + auto-collapse below 1200px (handoff).
    useEffect(() => {
        const stored = localStorage.getItem("vb_sidebar_collapsed");
        const mq = window.matchMedia("(max-width: 1199px)");
        const apply = () => setSidebarCollapsed(mq.matches ? true : stored === "true");
        apply();
        mq.addEventListener("change", apply);
        return () => mq.removeEventListener("change", apply);
    }, []);
    const toggleSidebar = () =>
        setSidebarCollapsed((c) => {
            const next = !c;
            localStorage.setItem("vb_sidebar_collapsed", String(next));
            return next;
        });
    const [inspectedMovie, setInspectedMovie] = useState<{ movie: FeedItem; sectionId?: string } | null>(null);
    // Rail: persistent filter console on /feed; on /watch it's inspector-only —
    // shown when a card's info is pressed, hidden on close (the watchlist owns its
    // own filters, so the SYS_CONSOLE filter panel is redundant there).
    const showRail = pathname === "/feed" || (pathname === "/watch" && !!inspectedMovie);
    // F8: the rail now returns a SECTIONED filtered feed, not a flat item list.
    const [filteredResults, setFilteredResults] = useState<FeedResponse | null>(null);
    const [isFiltering, setIsFiltering] = useState(false);
    const filteredCount = filteredResults
        ? filteredResults.feed.reduce((n, s) => n + s.items.length, 0)
        : null;
    const [inspectorActionLoading, setInspectorActionLoading] = useState<"watched" | "rejected" | null>(null);

    // ?onboarding_complete=true → welcome refresh (read from location to avoid a
    // useSearchParams Suspense boundary around the whole shell).
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        if (params.get("onboarding_complete") === "true") {
            scheduleFeedInvalidation(queryClient, 0);
            const url = new URL(window.location.href);
            url.searchParams.delete("onboarding_complete");
            window.history.replaceState({}, "", url.pathname);
        }
    }, [queryClient]);

    useEffect(() => () => cancelPendingFeedInvalidation(), []);

    // Auth check + session hydration (ported from dashboard.tsx)
    useEffect(() => {
        if (!isClerkLoaded) return;
        if (!clerkUser) {
            localStorage.removeItem(USER_SESSION_KEY);
            router.push("/login");
            return;
        }

        const storedUser = localStorage.getItem(USER_SESSION_KEY);
        if (storedUser) {
            try {
                const parsed = JSON.parse(storedUser);
                const validId = Number(parsed.id || parsed.user_id);
                if (validId && !isNaN(validId)) {
                    setCurrentUserSession({
                        ...parsed,
                        id: validId,
                        username: parsed.username,
                        letterboxd_username: parsed.letterboxd_username,
                    });
                }
            } catch (e) {
                console.warn("Corrupt local session", e);
                localStorage.removeItem(USER_SESSION_KEY);
            }
        }

        getCurrentUser()
            .then((verifiedUser) => {
                const fullSession: UserSession = {
                    id: verifiedUser.user_id,
                    username: verifiedUser.username,
                    letterboxd_username: verifiedUser.letterboxd_username,
                    has_data: verifiedUser.has_data,
                };
                setCurrentUserSession(fullSession);
                localStorage.setItem(USER_SESSION_KEY, JSON.stringify(fullSession));
            })
            .catch(async (err) => {
                console.error("Session verification failed:", err);
                if (err.response && err.response.status === 401) await handleLogout();
            })
            .finally(() => setIsLoadingAuth(false));

        getUsers().then(setUsers).catch((err) => console.error("Failed to fetch users", err));
    }, [isClerkLoaded, clerkUser, router]);

    // Onboarding jail redirect (ported from dashboard.tsx)
    useEffect(() => {
        if (!currentUserSession) return;
        api.get("/api/onboarding/status")
            .then(({ data }) => {
                const { ratings_count, completed } = data;
                setRatingsCount(ratings_count);
                const skipped = typeof window !== "undefined" && localStorage.getItem("vb_skip_onboarding") === "true";

                if (!completed && ratings_count < 15) {
                    if (ratings_count === 0) {
                        if (typeof window !== "undefined") localStorage.removeItem("vb_skip_onboarding");
                        router.replace("/onboarding");
                        return;
                    }
                    if (!skipped) {
                        router.replace("/onboarding");
                        return;
                    }
                } else if (ratings_count >= 15) {
                    if (typeof window !== "undefined") localStorage.removeItem("vb_skip_onboarding");
                }
            })
            .catch(() => {});
    }, [currentUserSession?.has_data, router]);

    // Clear invalid providers when country changes
    useEffect(() => {
        const validProviderIds = getProvidersForCountry(countryCode).map((p) => p.id as number);
        const filtered = streamingProviders.filter((id) => validProviderIds.includes(id));
        if (filtered.length !== streamingProviders.length) setStreamingProviders(filtered);
    }, [countryCode, streamingProviders]);

    const toggleProvider = (providerId: number) =>
        setStreamingProviders((prev) =>
            prev.includes(providerId) ? prev.filter((id) => id !== providerId) : [...prev, providerId]
        );
    const clearFilters = () => {
        setStreamingProviders([]);
        setCountryCode("ES");
        setFilteredResults(null);  // RESET also exits the filtered view → back to the normal feed
    };
    const handleFilterSearch = async (params: FilterSearchParams) => {
        setIsFiltering(true);
        try {
            // Merge in the rail's country + provider selection (the form only owns
            // the sliders/genres; country + providers live in shell state).
            setFilteredResults(await getFilteredFeed({
                ...params,
                countryCode,
                providers: streamingProviders,
            }));
        } finally {
            setIsFiltering(false);
        }
    };
    const clearFilterResults = () => setFilteredResults(null);

    const handleInspectorMarkWatched = async (tmdbId: number) => {
        setInspectorActionLoading("watched");
        try {
            await markWatched(tmdbId);
            scheduleFeedInvalidation(queryClient);
            setInspectedMovie(null);
        } catch (e) {
            console.error("Failed to mark as watched:", e);
        } finally {
            setInspectorActionLoading(null);
        }
    };
    const handleInspectorReject = async (tmdbId: number) => {
        setInspectorActionLoading("rejected");
        try {
            await rejectMovie(tmdbId);
            scheduleFeedInvalidation(queryClient);
            setInspectedMovie(null);
        } catch (e) {
            console.error("Failed to reject movie:", e);
        } finally {
            setInspectorActionLoading(null);
        }
    };

    if (isLoadingAuth) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-bg">
                <Loader2 className="size-8 animate-spin text-primary" />
            </div>
        );
    }
    if (!currentUserSession) return null;

    const sessionAsVectorboxUser: VectorboxUser = {
        id: currentUserSession.id,
        username: currentUserSession.username,
        has_data: currentUserSession.has_data,
        letterboxd_username: currentUserSession.letterboxd_username,
    };

    // ONBOARDING JAIL — lock sub-threshold users in the 5-step import wizard
    // unless they skipped (handoff onb flow replaces the old UploadZone card).
    const skippedOnboarding =
        typeof window !== "undefined" && localStorage.getItem("vb_skip_onboarding") === "true";
    if (!currentUserSession.has_data && !skippedOnboarding) {
        return (
            <div className="relative flex min-h-screen flex-col items-center bg-bg p-4 pt-10 text-fg lg:pt-16">
                <div className="z-10 flex w-full max-w-2xl flex-col items-center space-y-6">
                    <div className="w-full">
                        <h1 className="font-display text-3xl uppercase tracking-tight md:text-4xl">
                            <Wordmark />
                        </h1>
                        <p className="mt-2 font-mono text-xs uppercase tracking-widest text-fg-3">
                            {t("onboarding.welcome_agent")}{" "}
                            <span className="text-primary">
                                {clerkUser?.username || clerkUser?.firstName || currentUserSession.username}
                            </span>{" "}
                            · {t("onboarding.init_required")}
                        </p>
                    </div>

                    <ImportWizard
                        session={currentUserSession}
                        onComplete={() => window.location.reload()}
                        onSkip={() => {
                            localStorage.setItem("vb_skip_onboarding", "true");
                            window.location.reload();
                        }}
                    />

                    <button
                        onClick={handleLogout}
                        className="mx-auto flex items-center justify-center gap-1 text-xs text-danger transition-colors hover:opacity-80"
                    >
                        <LogOut className="size-3" /> {t("onboarding.abort")}
                    </button>
                </div>
            </div>
        );
    }

    return (
        <ShellProvider
            value={{
                session: currentUserSession,
                users,
                scope,
                countryCode,
                streamingProviders,
                setCountryCode,
                toggleProvider,
                inspect: (movie, sectionId) => setInspectedMovie({ movie, sectionId }),
                inspected: inspectedMovie,
                closeInspector: () => setInspectedMovie(null),
                filteredResults,
                isFiltering,
                clearFilterResults,
            }}
        >
            <div className="min-h-screen bg-bg text-fg">
                <Sidebar
                    collapsed={sidebarCollapsed}
                    onToggleCollapse={toggleSidebar}
                    letterboxdUsername={currentUserSession.letterboxd_username}
                />
                <div
                    className={cn(
                        "flex min-h-screen flex-col transition-[padding] duration-200 ease-out",
                        sidebarCollapsed ? "lg:pl-[56px]" : "lg:pl-[184px]",
                        showRail && "lg:pr-80"
                    )}
                >
                    <ShellTopbar />
                    <div className="container mx-auto px-4 pb-24 lg:pb-12">{children}</div>
                </div>

                {showRail && (
                    <RightConsole
                        selectedMovie={inspectedMovie?.movie ?? null}
                        selectedSectionId={inspectedMovie?.sectionId}
                        onCloseInspector={() => setInspectedMovie(null)}
                        scope={scope}
                        onScopeChange={setScope}
                        countryCode={countryCode}
                        onCountryChange={setCountryCode}
                        streamingProviders={streamingProviders}
                        onToggleProvider={toggleProvider}
                        onClearFilters={clearFilters}
                        onFilterSearch={handleFilterSearch}
                        onMarkWatched={handleInspectorMarkWatched}
                        onReject={handleInspectorReject}
                        inspectorActionLoading={inspectorActionLoading}
                        filteredCount={filteredCount}
                    />
                )}

                <MagicBoxModal />

                {/* Mobile filters FAB + sheet — feed only (watchlist owns its own sheet). */}
                {pathname === "/feed" && (
                    <FeedFilterSheet
                        countryCode={countryCode}
                        onCountryChange={setCountryCode}
                        streamingProviders={streamingProviders}
                        onToggleProvider={toggleProvider}
                        onClearFilters={clearFilters}
                        onFilterSearch={handleFilterSearch}
                        filteredCount={filteredCount}
                    />
                )}

                {/* Feed uses the inline accordion inspector (handoff 1C); the sheet stays elsewhere. */}
                {pathname !== "/feed" && (
                    <MobileInspector
                        movie={inspectedMovie?.movie ?? null}
                        sectionId={inspectedMovie?.sectionId}
                        onClose={() => setInspectedMovie(null)}
                        onMarkWatched={handleInspectorMarkWatched}
                        onReject={handleInspectorReject}
                        actionLoading={inspectorActionLoading}
                    />
                )}

                <MobileTabBar />
            </div>
        </ShellProvider>
    );
}
