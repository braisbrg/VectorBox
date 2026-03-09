"use client";

import { useState } from "react";
import {
    LayoutList,
    Grid3x3,
    Calendar,
    Users,
    Settings,
    ChevronLeft,
    ChevronRight,
    Film,
    Sparkles,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { AppTooltip } from "@/components/info-tooltip";
import { useLanguage } from "@/components/language-provider";
import { LanguageToggle } from "@/components/language-toggle";
import { VectorboxUser } from "@/lib/api";

interface SidebarProps {
    currentView: string;
    onViewChange: (view: string) => void;
    users?: VectorboxUser[];
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
            description: t("sidebar_desc.feed")
        },
        {
            id: "grid",
            label: t("sidebar.grid"),
            icon: Grid3x3,
            description: t("sidebar_desc.grid")
        },
        {
            id: "watchlist",
            label: t("sidebar.watchlist"),
            icon: Calendar,
            description: t("sidebar_desc.watchlist")
        },
        {
            id: "ai-search",
            label: t("sidebar.ai_search"),
            icon: Sparkles,
            description: t("sidebar_desc.ai_search")
        },
        {
            id: "more-like-this",
            label: t("sidebar.more_like_this"),
            icon: Film,
            description: t("sidebar_desc.more_like_this")
        },
        {
            id: "compatibility",
            label: t("sections.group_vibe"),
            icon: Users,
            description: t("sidebar_desc.group_vibe")
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

    return (
        <motion.aside
            initial={false}
            animate={{ width: isCollapsed ? 80 : 300 }}
            className="hidden lg:flex fixed left-0 top-0 h-screen bg-black border-r border-zinc-800 flex-col z-50 shadow-2xl"
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
                    aria-label={isCollapsed ? t("aria.expand_sidebar") : t("aria.collapse_sidebar")}
                >
                    {isCollapsed ? (
                        <ChevronRight className="w-5 h-5" />
                    ) : (
                        <ChevronLeft className="w-5 h-5" />
                    )}
                </button>
            </div>

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

                            <div className="absolute inset-0 bg-primary/5 translate-x-[-100%] group-hover:translate-x-0 transition-transform duration-300 pointer-events-none" />
                        </button>
                    );
                })}
            </nav>

            {/* Bottom Section */}
            <div className="border-t border-zinc-800 bg-black p-3 space-y-4">

                {!isCollapsed && users && currentUserId && (
                    <div className="px-3 py-2 bg-zinc-900 border border-zinc-800 mb-2">
                        <div className="flex items-center gap-2">
                            <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                            <span className="text-xs font-mono text-zinc-400 uppercase tracking-wider">
                                {users.find(u => u.id === currentUserId)?.username || "User"}
                            </span>
                        </div>
                    </div>
                )}

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

                <div className={`flex items-center ${isCollapsed ? "flex-col gap-4" : "justify-between"} pt-2 border-t border-zinc-900`}>
                    <LanguageToggle isCollapsed={isCollapsed} />
                    <AppTooltip isCollapsed={isCollapsed} />
                </div>
            </div>
        </motion.aside>
    );
}