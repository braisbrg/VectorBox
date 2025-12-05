"use client";

import { useState } from "react";
import {
    LayoutList,
    Grid3x3,
    Calendar,
    Heart,
    Users,
    Settings,
    ChevronLeft,
    ChevronRight,
    Film,
    Sparkles,
    User as UserIcon,
    LogOut
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { AppTooltip } from "@/components/info-tooltip";
import { useLanguage } from "@/components/language-provider";
import { LanguageToggle } from "@/components/language-toggle";
import { User } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

interface SidebarProps {
    currentView: string;
    onViewChange: (view: string) => void;
    users?: User[];
    currentUserId?: number | null;
    onUserSelect?: (id: number) => void;
}

export function Sidebar({ currentView, onViewChange, users, currentUserId, onUserSelect }: SidebarProps) {
    const [isCollapsed, setIsCollapsed] = useState(false);
    const { t } = useLanguage();

    const menuItems = [
        {
            id: "feed",
            label: t("sidebar.feed"),
            icon: LayoutList,
            description: "Personalized recommendations"
        },
        {
            id: "grid",
            label: t("sidebar.grid"),
            icon: Grid3x3,
            description: "Browse by mood & filters"
        },
        {
            id: "watchlist",
            label: t("sidebar.watchlist"),
            icon: Calendar,
            description: "Your saved movies"
        },
        {
            id: "ai-search",
            label: t("sidebar.ai_search"),
            icon: Sparkles,
            description: "Natural language search"
        },
        {
            id: "more-like-this",
            label: t("sidebar.more_like_this"),
            icon: Film,
            description: "Find similar movies"
        },
        {
            id: "compatibility",
            label: "Group Vibe",
            icon: Users,
            description: "Find shared favorites"
        },
    ];

    const bottomItems = [
        {
            id: "users",
            label: t("sidebar.users"),
            icon: Users,
        },
        {
            id: "settings",
            label: t("sidebar.settings"),
            icon: Settings,
        }
    ];

    // Upload Progress Polling
    const { data: uploadStatus } = useQuery({
        queryKey: ["uploadStatus", currentUserId],
        queryFn: async () => {
            if (!currentUserId) return null;
            const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
            const res = await fetch(`${apiUrl}/api/upload/status/${currentUserId}`);
            if (!res.ok) return null;
            return res.json();
        },
        refetchInterval: (query: any) => {
            // Poll every 1s if processing, otherwise stop (or slow down)
            return query.state.data?.status === "processing" ? 1000 : false;
        },
        enabled: !!currentUserId
    });

    return (
        <motion.aside
            initial={false}
            animate={{ width: isCollapsed ? 80 : 300 }}
            className="fixed left-0 top-0 h-screen bg-black border-r border-zinc-800 flex flex-col z-50 shadow-2xl"
        >
            {/* Header */}
            <div className="p-4 border-b border-zinc-800 flex items-center justify-between h-[70px]">
                <AnimatePresence mode="wait">
                    {!isCollapsed && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            className="flex items-center gap-2 overflow-hidden whitespace-nowrap"
                        >
                            <div className="w-8 h-8 bg-primary flex items-center justify-center">
                                <span className="font-mono font-bold text-black text-xl">V</span>
                            </div>
                            <span className="font-mono font-bold text-lg tracking-wider text-white">VECTORBOX</span>
                        </motion.div>
                    )}
                </AnimatePresence>
                <button
                    onClick={() => setIsCollapsed(!isCollapsed)}
                    className="p-2 rounded-none hover:bg-primary hover:text-black transition-colors border border-transparent hover:border-primary"
                    aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
                >
                    {isCollapsed ? (
                        <ChevronRight className="w-5 h-5" />
                    ) : (
                        <ChevronLeft className="w-5 h-5" />
                    )}
                </button>
            </div>

            {/* Upload Progress Indicator */}
            <AnimatePresence>
                {uploadStatus?.status === "processing" && (
                    <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="bg-zinc-900 border-b border-zinc-800 overflow-hidden"
                    >
                        <div className={`p-4 ${isCollapsed ? "flex justify-center" : ""}`}>
                            {isCollapsed ? (
                                <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                            ) : (
                                <div className="space-y-2">
                                    <div className="flex justify-between text-xs font-mono text-zinc-400">
                                        <span>UPLOADING...</span>
                                        <span>{uploadStatus.progress}%</span>
                                    </div>
                                    <div className="h-1 bg-zinc-800 w-full overflow-hidden">
                                        <motion.div
                                            className="h-full bg-primary"
                                            initial={{ width: 0 }}
                                            animate={{ width: `${uploadStatus.progress}%` }}
                                            transition={{ duration: 0.5 }}
                                        />
                                    </div>
                                    <p className="text-[10px] text-zinc-500 truncate">{uploadStatus.message}</p>
                                </div>
                            )}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Main Menu */}
            <nav className="flex-1 overflow-y-auto py-6 px-3 space-y-1 custom-scrollbar">
                {menuItems.map((item) => {
                    const Icon = item.icon;
                    const isActive = currentView === item.id;

                    return (
                        <button
                            key={item.id}
                            onClick={() => onViewChange(item.id)}
                            className={`
                                w-full flex items-center gap-4 px-3 py-3 transition-all group relative overflow-hidden font-mono uppercase tracking-wider
                                ${isActive
                                    ? "bg-primary text-black font-bold shadow-[4px_4px_0px_0px_rgba(255,255,255,0.2)]"
                                    : "text-zinc-400 hover:text-primary hover:bg-zinc-900/50"
                                }
                                ${isCollapsed ? "justify-center px-0" : ""}
                            `}
                            title={isCollapsed ? item.label : ""}
                        >
                            <Icon className={`flex-shrink-0 ${isCollapsed ? "w-6 h-6" : "w-5 h-5"} transition-colors group-hover:text-primary`} />

                            {!isCollapsed && (
                                <span className="text-sm font-mono uppercase tracking-wider truncate">
                                    {item.label}
                                </span>
                            )}

                            {/* Glitch effect overlay on hover */}
                            <div className="absolute inset-0 bg-primary/5 translate-x-[-100%] group-hover:translate-x-0 transition-transform duration-300 pointer-events-none" />
                        </button>
                    );
                })}
            </nav>

            {/* Bottom Section */}
            <div className="border-t border-zinc-800 bg-black p-3 space-y-4">

                {/* User Selector (Only visible when expanded) */}
                {!isCollapsed && users && currentUserId && onUserSelect && (
                    <div className="space-y-2">
                        <label className="text-[10px] uppercase text-zinc-500 font-mono tracking-widest pl-1">
                            {t("sidebar.current_user")}
                        </label>
                        <div className="relative">
                            <select
                                value={currentUserId}
                                onChange={(e) => onUserSelect(Number(e.target.value))}
                                className="w-full appearance-none bg-zinc-900 border border-zinc-800 text-zinc-300 text-xs font-mono rounded-none pl-9 pr-8 py-2 focus:outline-none focus:border-primary focus:text-primary cursor-pointer hover:bg-zinc-800 transition-colors"
                            >
                                {users.map(u => (
                                    <option key={u.id} value={u.id}>
                                        {u.username}
                                    </option>
                                ))}
                            </select>
                            <UserIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-3 h-3 text-zinc-500 pointer-events-none" />
                        </div>
                    </div>
                )}

                {/* Bottom Menu Items */}
                <div className="space-y-1">
                    {bottomItems.map((item) => {
                        const Icon = item.icon;
                        return (
                            <button
                                key={item.id}
                                onClick={() => onViewChange(item.id)}
                                className={`
                                    w-full flex items-center gap-4 px-3 py-2 transition-all group
                                    text-zinc-500 hover:text-white hover:bg-zinc-900/50
                                    ${isCollapsed ? "justify-center" : ""}
                                `}
                                title={isCollapsed ? item.label : ""}
                            >
                                <Icon className={`flex-shrink-0 ${isCollapsed ? "w-5 h-5" : "w-4 h-4"}`} />
                                {!isCollapsed && (
                                    <span className="text-xs font-mono uppercase tracking-wider">
                                        {item.label}
                                    </span>
                                )}
                            </button>
                        );
                    })}
                </div>

                {/* Footer Controls */}
                <div className={`flex items-center ${isCollapsed ? "flex-col gap-4" : "justify-between"} pt-2 border-t border-zinc-900`}>
                    <LanguageToggle isCollapsed={isCollapsed} />
                    <AppTooltip isCollapsed={isCollapsed} />
                </div>
            </div>
        </motion.aside>
    );
}
