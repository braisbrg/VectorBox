"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { LayoutList, Grid3x3, Globe, Tv, Loader2, RotateCcw, Heart, User as UserIcon, LogOut } from "lucide-react";
import { STREAMING_PROVIDERS, COUNTRIES, getProvidersForCountry } from "@/lib/constants";
import { motion } from "framer-motion";
import Image from "next/image";
import { useLanguage } from "@/components/language-provider";
import { logout, VectorboxUser, UserSession, getCurrentUser, getUsers } from "@/lib/api";

import { FeedContainer } from "@/components/feed-container";
import { UploadZone } from "@/components/upload-zone";
import { RecommendationGrid } from "@/components/recommendation-grid";
import { MoodSelector } from "@/components/mood-selector";
import { Sidebar } from "@/components/sidebar";
import { MobileHeader } from "@/components/mobile-header";
import { MobileNav } from "@/components/mobile-nav";
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
    const [currentUserSession, setCurrentUserSession] = useState<UserSession | null>(null);
    const [users, setUsers] = useState<VectorboxUser[]>([]);
    const [isLoadingAuth, setIsLoadingAuth] = useState(true);

    // View State
    const [currentView, setCurrentView] = useState("feed");
    const [selectedClusterId, setSelectedClusterId] = useState<number | null>(null);
    const [scope, setScope] = useState<"watchlist" | "global">("global");
    const [countryCode, setCountryCode] = useState("ES");
    const [streamingProviders, setStreamingProviders] = useState<number[]>([]);

    const { t } = useLanguage();

    // Client-side Hydration & Auth Check
    useEffect(() => {
        // 1. OPTIMISTIC STRATEGY: access localStorage for instant UI
        const storedUser = localStorage.getItem("vectorbox_user");
        let optimisticSession: UserSession | null = null;

        if (storedUser) {
            try {
                const parsed = JSON.parse(storedUser);
                const rawId = parsed.id || parsed.user_id;
                const validId = Number(rawId);

                if (validId && !isNaN(validId)) {
                    optimisticSession = {
                        ...parsed,
                        id: validId,
                        username: parsed.username,
                        letterboxd_username: parsed.letterboxd_username
                    };
                    // Set optimistic state immediately
                    setCurrentUserSession(optimisticSession);
                }
            } catch (e) {
                console.warn("Corrupt local session", e);
                localStorage.removeItem("vectorbox_user");
            }
        }

        // 2. SECURITY CHECK: Verify with API (Source of Truth)
        getCurrentUser()
            .then((verifiedUser) => {
                const verifiedSession: UserSession = {
                    id: verifiedUser.user_id,
                    username: verifiedUser.username,
                    letterboxd_username: verifiedUser.letterboxd_username,
                    token: verifiedUser.token // or undefined, dependent on session type
                };

                // Merge with extended flags if we have them (like has_data)
                // We know backend returns `has_data` in AuthResponse
                const fullSession = { ...verifiedSession, has_data: (verifiedUser as any).has_data };

                setCurrentUserSession(fullSession as any as UserSession);

                // Update Local Storage with fresh truth
                localStorage.setItem("vectorbox_user", JSON.stringify(fullSession));
            })
            .catch(async (err) => {
                console.error("Session verification failed:", err);
                // If API fails (401), we MUST clear local state and redirect
                // But if it's just a network error (offline), we might keep optimistic state?
                // Strictest security: Fail if we can't verify.
                // Compromise: If 401, logout. If network error, maybe warn.
                // Assuming interceptor handles 401 redirect, but let's be explicit.

                if (err.response && err.response.status === 401) {
                    await logout();
                    localStorage.removeItem("vectorbox_user");
                    router.push("/login");
                }
            })
            .finally(() => {
                setIsLoadingAuth(false);
            });

        // 3. User Sync: Fetch registered users to provide context to components
        getUsers().then(setUsers).catch(err => console.error("Failed to fetch users", err));
    }, []);

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
        setSelectedClusterId(null);
    };

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

    // ONBOARDING JAIL: If user has no data, lock them here
    if (!(currentUserSession as any).has_data) {
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
                            <p>{t("onboarding.welcome_agent")}<span className="text-primary font-bold"> {currentUserSession.username}</span>.</p>
                            <p>{t("onboarding.activation_msg")}</p>
                        </div>

                        <div className="bg-background/50 rounded-lg p-4 border border-border/30">
                            <UploadZone
                                registeredUsers={[currentUserSession as any as VectorboxUser]}
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
                            onClick={async () => {
                                await logout();
                                router.push('/login');
                                setTimeout(() => window.location.reload(), 100);
                            }}
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
                users={[currentUserSession as any as VectorboxUser]}
                currentUserId={currentUserSession.id}
                onUserSelect={() => { }}
            />
            <MobileHeader />
            <MobileNav
                currentView={currentView}
                onViewChange={setCurrentView}
                users={[currentUserSession as any as VectorboxUser]}
                currentUserId={currentUserSession.id}
                onUserSelect={() => { }}
            />

            <div className="lg:pl-[80px] transition-all duration-300 min-h-screen flex flex-col pt-[60px] lg:pt-0">
                {currentView === "feed" && (
                    <section className="relative py-12 overflow-hidden border-b border-zinc-800">
                        <div className="absolute inset-0 bg-[url('/grid-pattern.svg')] opacity-10" />
                        <div className="container relative z-10 px-6 mx-auto">
                            <motion.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ duration: 0.5 }}
                            >
                                <h1 className="text-5xl md:text-7xl font-bold tracking-tighter mb-2 uppercase font-mono">
                                    VECTOR<span className="text-primary">BOX</span>
                                </h1>
                                <p className="text-xl text-muted-foreground font-mono">
                                    {t("dashboard.system_ready")} <span className="text-primary">{currentUserSession.username}</span>
                                </p>
                            </motion.div>
                        </div>
                    </section>
                )}

                <div className="container px-4 mx-auto pb-20">
                    {(currentView === "feed") && (
                        <div className="flex flex-col md:flex-row gap-6 mb-8 p-6 bg-card/50 backdrop-blur-sm border border-border/50 rounded-xl shadow-sm mt-8">
                            <div className="flex-1 flex flex-col gap-4">
                                <div className="flex flex-wrap gap-4 items-center">
                                    <div className="bg-muted p-1 rounded-lg flex items-center gap-2">
                                        <div className="flex">
                                            <button
                                                onClick={() => setScope("global")}
                                                className={`px-3 py-1.5 text-sm font-medium rounded-md transition-all ${scope === "global" ? "bg-background shadow-sm text-primary" : "text-muted-foreground hover:text-foreground"}`}
                                            >
                                                {t("feed.global")}
                                            </button>
                                            <button
                                                onClick={() => setScope("watchlist")}
                                                className={`px-3 py-1.5 text-sm font-medium rounded-md transition-all ${scope === "watchlist" ? "bg-background shadow-sm text-primary" : "text-muted-foreground hover:text-foreground"}`}
                                            >
                                                {t("feed.my_watchlist")}
                                            </button>
                                        </div>
                                        <InfoTooltip
                                            id="scope-toggle-info"
                                            title={t("feed.global") + " / " + t("feed.my_watchlist")}
                                            description={t("feed.scope_tooltip")}
                                        />
                                    </div>

                                    <div className="w-px h-6 bg-border hidden sm:block" />

                                    <div className="relative">
                                        <select
                                            value={countryCode}
                                            onChange={(e) => setCountryCode(e.target.value)}
                                            className="appearance-none bg-card border border-border text-foreground text-sm font-medium rounded-md pl-9 pr-8 py-2 focus:outline-none focus:ring-2 focus:ring-primary/50 cursor-pointer hover:bg-muted/50 transition-colors"
                                        >
                                            {COUNTRIES.map(c => (
                                                <option key={c.code} value={c.code} className="bg-card text-foreground">
                                                    {c.name}
                                                </option>
                                            ))}
                                        </select>
                                        <Globe className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                                    </div>

                                    {(streamingProviders.length > 0 || countryCode !== "ES" || selectedClusterId !== null) && (
                                        <button
                                            onClick={clearFilters}
                                            className="text-xs text-muted-foreground hover:text-primary flex items-center gap-1"
                                        >
                                            <RotateCcw className="w-3 h-3" /> {t("dashboard.reset")}
                                        </button>
                                    )}
                                </div>

                                <div className="flex flex-wrap gap-2">
                                    {getProvidersForCountry(countryCode).map(provider => (
                                        <button
                                            key={provider.id}
                                            onClick={() => toggleProvider(provider.id)}
                                            className={`
                                            relative flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium transition-all border overflow-hidden
                                            ${streamingProviders.includes(provider.id)
                                                    ? "bg-primary/10 border-primary text-primary"
                                                    : "bg-background border-border text-muted-foreground hover:border-primary/50"}`}
                                        >
                                            <div className="relative w-4 h-4 rounded-sm overflow-hidden">
                                                <Image
                                                    src={`https://image.tmdb.org/t/p/w92${provider.logo_path}`}
                                                    alt={provider.name}
                                                    fill
                                                    sizes="16px"
                                                    className="object-cover"
                                                />
                                            </div>
                                            {provider.name}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        </div>
                    )}

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
                        />
                    ) : currentView === "grid" ? (
                        <div className="space-y-8">
                            <MoodSelector
                                userId={currentUserSession.id}
                                onSelectCluster={setSelectedClusterId}
                                selectedClusterId={selectedClusterId}
                            />
                            <RecommendationGrid
                                userId={currentUserSession.id}
                                countryCode={countryCode}
                                streamingProviders={streamingProviders}
                                onStreamingProvidersChange={setStreamingProviders}
                                clusterId={selectedClusterId}
                                mode={selectedClusterId ? "cluster" : "general"}
                                scope={scope}
                                onScopeChange={setScope}
                            />
                        </div>
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
                        />
                    )}
                </div>
            </div>
        </div>
    );
}
