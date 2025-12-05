"use client";

import { useState, useEffect } from "react";
import { getUsers, User } from "@/lib/api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LayoutList, Grid3x3, Globe, Tv, Loader2, RotateCcw, Heart, User as UserIcon, LogOut } from "lucide-react";
import { STREAMING_PROVIDERS, COUNTRIES, getProvidersForCountry } from "@/lib/constants";
import { motion, AnimatePresence } from "framer-motion";
import Image from "next/image";
import { useTheme } from "next-themes";
import { useLanguage } from "@/components/language-provider";

import { UsersView } from "@/components/users-view";
import { FeedContainer } from "@/components/feed-container";
import { RecommendationGrid } from "@/components/recommendation-grid";
import { MoodSelector } from "@/components/mood-selector";
import { Sidebar } from "@/components/sidebar";
import { AppTooltip, InfoTooltip } from "@/components/info-tooltip";
import { MoreLikeThis } from "@/components/more-like-this";
import { WatchlistView } from "@/components/watchlist-view";
import { SettingsView } from "@/components/settings-view";
import { AISearchView } from "@/components/ai-search-view";
import { GroupVibePicker } from "@/components/group-vibe-picker";

function TypewriterTitle({ text }: { text: string }) {
    const [displayText, setDisplayText] = useState("");

    useEffect(() => {
        let i = 0;
        const timer = setInterval(() => {
            if (i < text.length) {
                setDisplayText((prev) => prev + text.charAt(i));
                i++;
            } else {
                clearInterval(timer);
            }
        }, 100);

        return () => clearInterval(timer);
    }, [text]);

    return (
        <h1 className="text-4xl md:text-6xl font-black tracking-tighter mb-2 text-acid-outline" data-text={displayText}>
            {displayText}
            <span className="animate-pulse text-primary">_</span>
        </h1>
    );
}

export default function HomePage() {
    const [userId, setUserId] = useState<number | null>(null);
    const [currentView, setCurrentView] = useState("feed");
    const [selectedClusterId, setSelectedClusterId] = useState<number | null>(null);
    const [scope, setScope] = useState<"watchlist" | "global">("global"); // Default to global
    const [countryCode, setCountryCode] = useState("ES");
    const [streamingProviders, setStreamingProviders] = useState<number[]>([]);

    const queryClient = useQueryClient();

    const { t } = useLanguage();

    // Fetch users on mount
    const { data: users, isLoading: isLoadingUsers } = useQuery({
        queryKey: ["users"],
        queryFn: getUsers,
    });

    // Auto-select user from localStorage or first available
    useEffect(() => {
        if (users && users.length > 0) {
            // If we already have a userId, save it (unless it's null)
            if (userId) {
                localStorage.setItem("cinematch_user_id", userId.toString());
            } else {
                // Try to load from localStorage
                const savedId = localStorage.getItem("cinematch_user_id");
                if (savedId) {
                    const foundUser = users.find(u => u.id === parseInt(savedId));
                    if (foundUser) {
                        setUserId(foundUser.id);
                        if (foundUser.has_data) {
                            setCurrentView("feed");
                        }
                        return;
                    }
                }

                // Fallback to first user
                setUserId(users[0].id);
                if (users[0].has_data) {
                    setCurrentView("feed");
                }
            }
        }
    }, [users, userId]);

    // Save to localStorage whenever userId changes
    useEffect(() => {
        if (userId) {
            localStorage.setItem("cinematch_user_id", userId.toString());
        }
    }, [userId]);

    // Clear invalid providers when country changes
    useEffect(() => {
        const validProviderIds = getProvidersForCountry(countryCode).map(p => p.id as number);
        const filteredProviders = streamingProviders.filter(id => validProviderIds.includes(id));
        if (filteredProviders.length !== streamingProviders.length) {
            setStreamingProviders(filteredProviders);
        }
    }, [countryCode, streamingProviders]);

    const handleUploadSuccess = (id: number) => {
        console.log("Upload success, setting user ID:", id);
        setUserId(id);
        queryClient.invalidateQueries({ queryKey: ["users"] });
        queryClient.invalidateQueries({ queryKey: ["feed"] }); // Force feed refresh
    };
    const toggleProvider = (providerId: number) => {
        setStreamingProviders(prev =>
            prev.includes(providerId)
                ? prev.filter(id => id !== providerId)
                : [...prev, providerId]
        );
    };

    // Determine if current user has data
    const currentUser = users?.find(u => u.id === userId);
    const hasData = currentUser?.has_data;

    // Force "users" view if no user selected or user has no data
    useEffect(() => {
        if (!isLoadingUsers) {
            if (!userId || (userId && !hasData)) {
                if (currentView !== "users") {
                    setCurrentView("users");
                }
            }
        }
    }, [userId, hasData, currentView, isLoadingUsers]);

    // Auto-switch to feed when user changes and has data
    useEffect(() => {
        if (userId && hasData) {
            setCurrentView("feed");
        }
    }, [userId, hasData]);

    const clearFilters = () => {
        setStreamingProviders([]);
        setCountryCode("ES");
        setSelectedClusterId(null);
    };

    return (
        <main className="min-h-screen bg-background text-foreground">
            {/* Sidebar Navigation - Only show if user has data */}
            {userId && hasData && (
                <Sidebar
                    currentView={currentView}
                    onViewChange={setCurrentView}
                    users={users || []}
                    currentUserId={userId}
                    onUserSelect={setUserId}
                />
            )}

            {/* Main Content Area */}
            <div className={`${userId && hasData ? "pl-[80px] md:pl-[80px]" : ""} transition-all duration-300 min-h-screen flex flex-col pt-24`}>

                {/* Hero Section - Integrated Welcome */}
                {currentView === "feed" && userId && (
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
                                    SYSTEM_READY // USER: <span className="text-primary">{users?.find(u => u.id === userId)?.username}</span>
                                </p>
                            </motion.div>
                        </div>
                    </section>
                )}

                {/* Main Content */}
                {/* Special Case: Users View (Always accessible) */}
                {currentView === "users" ? (
                    <div
                        className="container px-4 mx-auto pb-20"
                    >
                        <UsersView
                            users={users || []}
                            currentUserId={userId}
                            onUserSelect={(id) => {
                                setUserId(id);
                                const user = users?.find(u => u.id === id);
                                if (user?.has_data) {
                                    setCurrentView("feed");
                                }
                            }}
                            onUserCreated={(user) => {
                                queryClient.invalidateQueries({ queryKey: ["users"] });
                                setUserId(user.id);
                            }}
                            onUploadSuccess={handleUploadSuccess}
                        />
                    </div>
                ) : userId ? (
                    <div
                        className="container px-4 mx-auto pb-20">

                        {/* Controls Bar - ONLY show for feed */}
                        {(currentView === "feed") && (
                            <div className="flex flex-col md:flex-row gap-6 mb-8 p-6 bg-card/50 backdrop-blur-sm border border-border/50 rounded-xl shadow-sm">
                                {/* Filters */}
                                <div className="flex-1 flex flex-col gap-4">
                                    <div className="flex flex-wrap gap-4 items-center">
                                        {/* Scope Toggle */}
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

                                        {/* Country Selector */}
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

                                        {/* Clear Filters */}
                                        {(streamingProviders.length > 0 || countryCode !== "ES" || selectedClusterId !== null) && (
                                            <button
                                                onClick={clearFilters}
                                                className="text-xs text-muted-foreground hover:text-primary flex items-center gap-1"
                                            >
                                                <RotateCcw className="w-3 h-3" /> Reset
                                            </button>
                                        )}
                                    </div>

                                    {/* Streaming Providers */}
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

                        {/* Content Switcher */}
                        {currentView === "ai-search" ? (
                            <AISearchView userId={userId} />
                        ) : currentView === "more-like-this" ? (
                            <MoreLikeThis userId={userId} />
                        ) : currentView === "watchlist" ? (
                            <WatchlistView
                                userId={userId}
                                username={users?.find(u => u.id === userId)?.username || ""}
                                countryCode={countryCode}
                                streamingProviders={streamingProviders}
                            />
                        ) : currentView === "grid" ? (
                            <div className="space-y-8">
                                <MoodSelector
                                    userId={userId}
                                    onSelectCluster={setSelectedClusterId}
                                    selectedClusterId={selectedClusterId}
                                />
                                <RecommendationGrid
                                    userId={userId}
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
                        ) : currentView === "compatibility" ? (
                            <GroupVibePicker />
                        ) : (
                            /* Default to Feed */
                            <FeedContainer
                                userId={userId}
                                scope={scope}
                                countryCode={countryCode}
                                streamingProviders={streamingProviders}
                            />
                        )}
                    </div>
                ) : (
                    <div className="container px-4 mx-auto pb-20 text-center text-muted-foreground pt-20">
                        {isLoadingUsers ? (
                            <div className="flex items-center justify-center gap-2 text-lg">
                                <Loader2 className="w-5 h-5 animate-spin" /> Loading users...
                            </div>
                        ) : (
                            <div className="space-y-4">
                                <p>No users found.</p>
                                <button
                                    onClick={() => setCurrentView("users")}
                                    className="text-primary hover:underline"
                                >
                                    Create a user to get started
                                </button>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </main >
    );
}
