"use client";

import { useState } from "react";
import Link from "next/link";
import {
    LayoutList,
    Calendar,
    Users,
    Settings,
    ChevronLeft,
    ChevronRight,
    Film,
    Sparkles,
    User,
    LogOut,
    UserCircle,
} from "lucide-react";
import { m, AnimatePresence } from "framer-motion";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useUser } from "@clerk/nextjs";
import { AppTooltip } from "@/components/info-tooltip";
import { useLanguage } from "@/components/language-provider";
import { LanguageToggle } from "@/components/language-toggle";
import { VectorboxUser } from "@/lib/api";
import { useVectorboxLogout } from "@/hooks/useVectorboxLogout";

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
    const handleLogout = useVectorboxLogout();
    const { user: clerkUser } = useUser();
    const displayName = clerkUser?.username || clerkUser?.firstName || clerkUser?.fullName || users?.find(u => u.id === currentUserId)?.username || "User";

    const menuItems = [
        {
            id: "feed",
            label: t("sidebar.feed"),
            icon: LayoutList,
            description: t("sidebar_desc.feed")
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
            id: "settings",
            label: t("sidebar.settings"),
            icon: Settings,
        }
    ];

    return (
        <m.aside
            initial={false}
            animate={{ width: isCollapsed ? 80 : 300 }}
            className="hidden lg:flex fixed left-0 top-0 h-screen bg-zinc-950 border-r border-zinc-800 flex-col z-50 shadow-2xl"
        >
            {/* Header */}
            <div className="p-4 border-b border-zinc-800 flex items-center justify-between h-[70px]">
                <AnimatePresence mode="wait">
                    {!isCollapsed && (
                        <m.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            className="flex items-center gap-2 overflow-hidden whitespace-nowrap">
                            <div className="size-8 bg-primary flex items-center justify-center">
                                <span className="font-mono font-bold text-black text-xl">V</span>
                            </div>
                            <span className="font-mono font-bold text-lg tracking-wider text-white">VECTORBOX</span>
                        </m.div>
                    )}
                </AnimatePresence>
                <button
                    onClick={() => setIsCollapsed(!isCollapsed)}
                    className="p-2 rounded-none hover:bg-primary hover:text-black transition-colors border border-transparent hover:border-primary"
                    aria-label={isCollapsed ? t("aria.expand_sidebar") : t("aria.collapse_sidebar")}
                >
                    {isCollapsed ? (
                        <ChevronRight className="size-5" />
                    ) : (
                        <ChevronLeft className="size-5" />
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
                            <Icon className={`flex-shrink-0 ${isCollapsed ? "size-6" : "size-5"} transition-colors group-hover:text-primary`} />

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
            <div className="border-t border-zinc-800 bg-zinc-950 p-3 space-y-4">

                {!isCollapsed && users && currentUserId && (
                    <div className="px-3 mb-2">
                        <DropdownMenu.Root>
                            <DropdownMenu.Trigger asChild>
                                <button className="w-full flex items-center gap-2 px-3 py-2 bg-zinc-900 border border-zinc-800 hover:border-primary/50 transition-colors group cursor-pointer outline-none">
                                    <div className="size-2 rounded-full bg-green-500 animate-pulse" />
                                    <span className="text-xs font-mono text-zinc-400 group-hover:text-primary uppercase tracking-wider truncate">
                                        {displayName}
                                    </span>
                                </button>
                            </DropdownMenu.Trigger>

                            <DropdownMenu.Portal>
                                <DropdownMenu.Content
                                    className="z-[100] min-w-[160px] bg-zinc-950 border border-zinc-800 p-1 shadow-2xl animate-in fade-in zoom-in-95 duration-200"
                                    side="right"
                                    align="end"
                                    sideOffset={10}
                                >
                                    <DropdownMenu.Item
                                        className="flex items-center gap-2 px-3 py-2 text-xs font-mono text-zinc-400 hover:text-black hover:bg-primary outline-none cursor-pointer transition-colors uppercase tracking-wider"
                                        onClick={() => onViewChange("profile")}
                                    >
                                        <UserCircle className="size-4" />
                                        {t("sidebar.profile") || "Profile"}
                                    </DropdownMenu.Item>
                                    
                                    <DropdownMenu.Separator className="h-px bg-zinc-800 my-1" />
                                    
                                    <DropdownMenu.Item
                                        className="flex items-center gap-2 px-3 py-2 text-xs font-mono text-red-500 hover:text-black hover:bg-red-500 outline-none cursor-pointer transition-colors uppercase tracking-wider"
                                        onClick={handleLogout}
                                    >
                                        <LogOut className="size-4" />
                                        {t("app.logout")}
                                    </DropdownMenu.Item>
                                </DropdownMenu.Content>
                            </DropdownMenu.Portal>
                        </DropdownMenu.Root>
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
                                <Icon className={`flex-shrink-0 ${isCollapsed ? "size-5" : "size-4"}`} />
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

                {!isCollapsed && (
                    <div className="flex items-center justify-center gap-3 pt-2">
                        <Link
                            href="/privacy"
                            className="text-[10px] font-mono text-zinc-700 hover:text-[#CCFF00] transition-colors uppercase tracking-wider"
                        >
                            Privacy
                        </Link>
                        <span className="text-zinc-800 text-[10px]">·</span>
                        <Link
                            href="/terms"
                            className="text-[10px] font-mono text-zinc-700 hover:text-[#CCFF00] transition-colors uppercase tracking-wider"
                        >
                            Terms
                        </Link>
                    </div>
                )}
            </div>
        </m.aside>
    );
}