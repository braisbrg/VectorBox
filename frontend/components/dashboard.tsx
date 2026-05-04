"use client";

import { useState, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { LayoutList, Globe, Tv, Loader2, RotateCcw, Heart, User as UserIcon, LogOut } from "lucide-react";
import { STREAMING_PROVIDERS, COUNTRIES, getProvidersForCountry } from "@/lib/constants";
import { motion } from "framer-motion";
import Image from "next/image";
import { useUser } from "@clerk/nextjs";
import { useLanguage } from "@/components/language-provider";
import { api, VectorboxUser, UserSession, getCurrentUser, getUsers, FeedItem, FilterSearchParams, searchWithFilters, markWatched, rejectMovie } from "@/lib/api";
import { useVectorboxLogout } from "@/hooks/useVectorboxLogout";

import { FeedContainer } from "@/components/feed-container";
import { UploadZone } from "@/components/upload-zone";
import { Sidebar } from "@/components/sidebar";
import { RightConsole } from "@/components/right-console";
import { MobileHeader } from "@/components/mobile-header";
import { MobileNav } from "@/components/mobile-nav";
import { getUserClusters, ClusterInfo } from "@/lib/api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence } from "framer-motion";
import { X, Sparkles } from "lucide-react";
import { InfoTooltip } from "@/components/info-tooltip";
import { MoreLikeThis } from "@/components/more-like-this";
import { WatchlistView } from "@/components/watchlist-view";
import { SettingsView } from "@/components/settings-view";
import { AISearchView } from "@/components/ai-search-view";
import { GroupVibePicker } from "@/components/group-vibe-picker";
import { UsersView } from "@/components/users-view";
import { FeedResponse } from "@/lib/api";

interface DashboardProps {
    initialFeedData?: FeedResponse | null;
}

export function Dashboard({ initialFeedData }: DashboardProps) {
    const router = useRouter();
    const searchParams = useSearchParams();
    const { user: clerkUser, isLoaded: isClerkLoaded } = useUser();
    const handleLogout = useVectorboxLogout();
    const [currentUserSession, setCurrentUserSession] = useState<UserSession | null>(null);
    const [users, setUsers] = useState<VectorboxUser[]>([]);
    const [isLoadingAuth, setIsLoadingAuth] = useState(true);
    const [showOnboardingBanner, setShowOnboardingBanner] = useState(false);
    const [showImprovementBanner, setShowImprovementBanner] = useState(false);
    const [ratingsCount, setRatingsCount] = useState(0);

    // View State
    const [currentView, setCurrentView] = useState("feed");
    const [scope, setScope] = useState<"watchlist" | "global">("global");
    const [countryCode, setCountryCode] = useState("ES");
    const [streamingProviders, setStreamingProviders] = useState<number[]>([]);
    const [inspectedMovie, setInspectedMovie] = useState<{ movie: FeedItem; sectionId?: string } | null>(null);
    const [filteredResults, setFilteredResults] = useState<FeedItem[] | null>(null);
    const [isFiltering, setIsFiltering] = useState(false);
    const [inspectorActionLoading, setInspectorActionLoading] = useState<"watched" | "rejected" | null>(null);

    const queryClient = useQueryClient();

    // Handle ?onboarding_complete=true query param
    useEffect(() => {
        if (searchParams.get("onboarding_complete") === "true") {
            setShowOnboardingBanner(true);
            // Strip param from URL without reload
            const url = new URL(window.location.href);
            url.searchParams.delete("onboarding_complete");
            window.history.replaceState({}, "", url.pathname);
        }
    }, [searchParams]);

    const handleInspectorMarkWatched = async (tmdbId: number) => {
        setInspectorActionLoading("watched");
        try {
            await markWatched(tmdbId);
            queryClient.invalidateQueries({ queryKey: ["feed"] });
            setInspectedMovie(null);
        } catch (error) {
            console.error("Failed to mark as watched:", error);
        } finally {
            setInspectorActionLoading(null);
        }
    };

    const handleInspectorReject = async (tmdbId: number) => {
        setInspectorActionLoading("rejected");
        try {
            await rejectMovie(tmdbId);
            queryClient.invalidateQueries({ queryKey: ["feed"] });
            setInspectedMovie(null);
        } catch (error) {
            console.error("Failed to reject movie:", error);
        } finally {
            setInspectorActionLoading(null);
        }
    };

    const { t } = useLanguage();

    // Client-side Hydration & Auth Check
    useEffect(() => {
        if (!isClerkLoaded) return;

        if (!clerkUser) {
            // FIX 1: Guest with 15+ ratings → explore instead of login
            try {
                const raw = localStorage.getItem("vb_guest_ratings");
                if (raw && Object.keys(JSON.parse(raw)).length >= 15) {
                    router.replace("/explore?guest=true");
                    return;
                }
            } catch { /* ignore corrupt data */ }
            localStorage.removeItem("vectorbox_user");
            router.push("/login");
            return;
        }

        // Optimistic paint from cached session (Clerk JWT attached by AuthBridge)
        const storedUser = localStorage.getItem("vectorbox_user");
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
                localStorage.removeItem("vectorbox_user");
            }
        }

        // Hydrate from backend /auth/me — source of truth for has_data + user_id.
        getCurrentUser()
            .then((verifiedUser) => {
                const fullSession: UserSession = {
                    id: verifiedUser.user_id,
                    username: verifiedUser.username,
                    letterboxd_username: verifiedUser.letterboxd_username,
                    has_data: verifiedUser.has_data,
                };
                setCurrentUserSession(fullSession);
                localStorage.setItem("vectorbox_user", JSON.stringify(fullSession));
            })
            .catch(async (err) => {
                console.error("Session verification failed:", err);
                if (err.response && err.response.status === 401) {
                    await handleLogout();
                }
            })
            .finally(() => {
                setIsLoadingAuth(false);
            });

        getUsers().then(setUsers).catch(err => console.error("Failed to fetch users", err));
    }, [isClerkLoaded, clerkUser, router]);

    // FIX 3: Onboarding status check — redirect or show improvement banner
    useEffect(() => {
        if (!currentUserSession?.has_data) return;

        api.get("/api/onboarding/status")
            .then(({ data }) => {
                const { ratings_count, completed } = data;
                setRatingsCount(ratings_count);

                if (!completed && ratings_count > 0 && ratings_count < 15) {
                    router.replace("/onboarding");
                    return;
                }
                if (ratings_count >= 15 && ratings_count < 35) {
                    setShowImprovementBanner(true);
                }
            })
            .catch(() => { /* non-critical, ignore */ });
    }, [currentUserSession?.has_data, router]);

    // Clear invalid providers when country changes
    useEffect(() => {
        const validProviderIds = getProvidersForCountry(countryCode).map(p => p.id as number);
        const filteredProviders = streamingProviders.filter(id => validProviderIds.includes(id));
        if (filteredProviders.length !== streamingProviders.length) {
            setStreamingProviders(filteredProviders);
        }
    }, [countryCode, streamingProviders]);

    const toggleProvider = (providerId: number) => {
        setStreamingProviders(prev =>
            prev.includes(providerId)
                ? prev.filter(id => id !== providerId)
                : [...prev, providerId]
        );
    };

    const clearFilters = () => {
        setStreamingProviders([]);
        setCountryCode("ES");
    };

    const handleFilterSearch = async (params: FilterSearchParams) => {
        setIsFiltering(true);
        try {
            const results = await searchWithFilters(params);
            setFilteredResults(results);
        } finally {
            setIsFiltering(false);
        }
    };

    const clearFilterResults = () => setFilteredResults(null);

    // Show loading briefly during hydration
    if (isLoadingAuth) {
        return (
            <div className="min-h-screen bg-black flex items-center justify-center">
                <Loader2 className="w-8 h-8 text-primary animate-spin" />
            </div>
        );
    }

    // Fallback if no user found
    if (!currentUserSession) {
        return null;
    }

    // Typed VectorboxUser derived from session (avoids double-cast)
    const sessionAsVectorboxUser: VectorboxUser = {
        id: currentUserSession.id,
        username: currentUserSession.username,
        has_data: currentUserSession.has_data,
        letterboxd_username: currentUserSession.letterboxd_username,
    };

    // ONBOARDING JAIL: If user has no data, lock them here
    if (!currentUserSession.has_data) {
        return (
            <div className="min-h-screen bg-black text-foreground flex flex-col items-center justify-center p-4 relative overflow-hidden">
                <div className="absolute inset-0 bg-[url('/grid-pattern.svg')] opacity-10 pointer-events-none" />

                <div className="z-10 w-full max-w-2xl space-y-8 animate-in fade-in zoom-in duration-500">
                    <div className="text-center">
                        <h1 className="text-4xl md:text-5xl font-black tracking-tighter mb-2 font-mono">
                            VECTOR<span className="text-primary">BOX</span>
                        </h1>
                        <p className="text-zinc-500 font-mono text-sm uppercase tracking-widest">
                            {t("onboarding.init_required")}
                        </p>
                    </div>

                    <div className="bg-card border border-border/50 rounded-xl shadow-2xl overflow-hidden p-8 backdrop-blur-sm">
                        <div className="mb-6 space-y-2 text-center text-muted-foreground text-sm">
                            <p>{t("onboarding.welcome_agent")}<span className="text-primary font-bold"> {clerkUser?.username || clerkUser?.firstName || clerkUser?.fullName || currentUserSession.username}</span>.</p>
                            <p>{t("onboarding.activation_msg")}</p>
                        </div>

                        <div className="bg-background/50 rounded-lg p-4 border border-border/30">
                            <UploadZone
                                registeredUsers={[sessionAsVectorboxUser]}
                                activeSessionUserId={currentUserSession.id}
                                onUploadSuccess={(userId) => {
                                    window.location.reload();
                                }}
                                onUserCreated={() => { }}
                            />
                        </div>
                    </div>

                    <div className="text-center">
                        <button
                            onClick={handleLogout}
                            className="text-xs text-red-500 hover:text-red-400 flex items-center justify-center gap-1 mx-auto transition-colors font-mono"
                        >
                            <LogOut className="w-3 h-3" /> {t("onboarding.abort")}
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    // MAIN DASHBOARD CONTENT
    return (
        <div className="min-h-screen bg-background text-foreground">
            <Sidebar
                currentView={currentView}
                onViewChange={setCurrentView}
                users={[sessionAsVectorboxUser]}
                currentUserId={currentUserSession.id}
                onUserSelect={() => { }}
            />
            <MobileHeader />
            <MobileNav
                currentView={currentView}
                onViewChange={setCurrentView}
                users={[sessionAsVectorboxUser]}
                currentUserId={currentUserSession.id}
                onUserSelect={() => { }}
            />

            <div className="lg:pl-[80px] lg:pr-80 transition-all duration-300 min-h-screen flex flex-col pt-[60px] lg:pt-0">
                {currentView === "feed" && (
                    <section className="relative py-4 overflow-hidden border-b border-zinc-800">
                        <div className="absolute inset-0 bg-[url('/grid-pattern.svg')] opacity-10" />
                        <div className="container relative z-10 px-6 mx-auto">
                            <motion.div
                                initial={{ opacity: 0, y: 10 }}
                                animate={{ opacity: 1, y: 0 }}
                                className="flex flex-col md:flex-row md:items-baseline md:gap-4"
                            >
                                <h1 className="text-3xl md:text-4xl font-black tracking-tighter uppercase font-mono leading-none">
                                    VECTOR<span className="text-primary">BOX</span>
                                </h1>
                                <p className="text-[10px] text-zinc-500 font-mono uppercase tracking-[0.2em]">
                                    {t("dashboard.system_ready")} <span className="text-primary">{clerkUser?.username || clerkUser?.firstName || clerkUser?.fullName || currentUserSession.username}</span>
                                </p>
                            </motion.div>
                        </div>
                    </section>
                )}

                <div className="container px-4 mx-auto pb-20">
                    {/* Onboarding complete welcome banner */}
                    <AnimatePresence>
                        {showOnboardingBanner && (
                            <motion.div
                                initial={{ opacity: 0, y: -10 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, height: 0 }}
                                className="mb-4 border border-primary/30 bg-primary/5 p-4 flex items-center justify-between"
                            >
                                <div className="flex items-center gap-3">
                                    <Sparkles className="w-4 h-4 text-primary shrink-0" />
                                    <div>
                                        <p className="text-xs font-mono text-primary uppercase tracking-wider font-bold">
                                            Welcome to VectorBox
                                        </p>
                                        <p className="text-[10px] font-mono text-zinc-500">
                                            Your taste profile is being built. Recommendations will improve as you rate more films.
                                        </p>
                                    </div>
                                </div>
                                <button
                                    onClick={() => setShowOnboardingBanner(false)}
                                    className="p-1 text-zinc-600 hover:text-zinc-400 transition-colors shrink-0"
                                >
                                    <X className="w-3 h-3" />
                                </button>
                            </motion.div>
                        )}
                    </AnimatePresence>

                    {/* FIX 3: Improvement banner for users with 15–34 ratings */}
                    {showImprovementBanner && (
                        <div className="border border-zinc-700 px-4 py-2 font-mono text-xs
                                        flex items-center justify-between mb-4">
                            <span className="text-zinc-500">
                                RATE MORE FILMS TO IMPROVE YOUR RECOMMENDATIONS
                                · {Math.max(0, 35 - ratingsCount)} MORE FOR BEST RESULTS
                            </span>
                            <button
                                onClick={() => router.push("/onboarding")}
                                className="text-primary hover:underline text-xs font-mono ml-4 shrink-0"
                            >
                                [ RATE FILMS ]
                            </button>
                        </div>
                    )}

                    {/* Removed Filter UI - Moved to RightConsole */}

                    {currentView === "ai-search" ? (
                        <AISearchView userId={currentUserSession.id} />
                    ) : currentView === "more-like-this" ? (
                        <MoreLikeThis userId={currentUserSession.id} />
                    ) : currentView === "watchlist" ? (
                        <WatchlistView
                            userId={currentUserSession.id}
                            username={currentUserSession.username}
                            countryCode={countryCode}
                            streamingProviders={streamingProviders}
                            onInspect={(movie: FeedItem, sectionId?: string) => setInspectedMovie({ movie, sectionId })}
                        />
                    ) : currentView === "settings" ? (
                        <SettingsView />
                    ) : currentView === "profile" ? (
                        <div className="py-12 flex flex-col items-center justify-center text-center space-y-6">
                            <div className="w-24 h-24 bg-zinc-900 border border-zinc-800 rounded-full flex items-center justify-center text-primary">
                                <UserIcon size={48} />
                            </div>
                            <div className="space-y-2">
                                <h2 className="text-3xl font-bold tracking-tighter uppercase font-mono italic">User Profile</h2>
                                <p className="text-zinc-500 font-mono text-sm uppercase tracking-widest">// Profile module coming soon //</p>
                            </div>
                            <button 
                                onClick={() => setCurrentView("feed")}
                                className="px-6 py-2 bg-primary text-black font-bold uppercase tracking-wider text-xs hover:bg-primary/90 transition-colors"
                            >
                                Back to Feed
                            </button>
                        </div>
                    ) : currentView === "compatibility" ? (
                        <GroupVibePicker />
                    ) : (
                        <FeedContainer
                            userId={currentUserSession.id}
                            scope={scope}
                            countryCode={countryCode}
                            streamingProviders={streamingProviders}
                            initialData={initialFeedData}
                            registeredUsers={users}
                            onInspect={(movie: FeedItem, sectionId?: string) => setInspectedMovie({ movie, sectionId })}
                            filteredResults={filteredResults}
                            isFiltering={isFiltering}
                            onClearFilterResults={clearFilterResults}
                        />
                    )}
                </div>
            </div>

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
            />

            {/* Mobile Bottom Sheet for Inspector */}
            <AnimatePresence>
                {inspectedMovie && (
                    <motion.div
                        initial={{ y: "100%" }}
                        animate={{ y: 0 }}
                        exit={{ y: "100%" }}
                        transition={{ type: "spring", damping: 25, stiffness: 200 }}
                        className="lg:hidden fixed inset-x-0 bottom-0 z-50 bg-[#0a0a0a] border-t border-zinc-800 rounded-t-2xl shadow-[0_-8px_30px_rgb(0,0,0,0.5)] max-h-[90vh] overflow-hidden flex flex-col"
                    >
                        <div className="w-12 h-1.5 bg-zinc-800 rounded-full mx-auto my-4 shrink-0" onClick={() => setInspectedMovie(null)} />
                        <div className="overflow-y-auto px-6 pb-12">
                            {/* Reusing Inspector logic or simplified version for mobile */}
                            <div className="flex justify-between items-start mb-6">
                                <h2 className="text-xl font-bold uppercase font-mono tracking-tighter">DATA_INSPECTOR_MOB</h2>
                                <button onClick={() => setInspectedMovie(null)} className="p-2 text-zinc-500"><X /></button>
                            </div>
                            <div className="text-zinc-500 font-mono text-xs uppercase italic tracking-widest text-center py-20 border border-dashed border-zinc-800">
                                [ INSPECTOR_VIEW_PORTED_FROM_CONSOLE ]
                                <p className="mt-4 text-[10px] normal-case tracking-normal">Please use Desktop for full data analysis suite.</p>
                            </div>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
