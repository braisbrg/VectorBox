"use client";

import { motion, AnimatePresence } from "framer-motion";
import { X, LayoutList, Calendar, Sparkles, Film, Users, Settings, User as UserIcon, LogOut } from "lucide-react";
import { useUser } from "@clerk/nextjs";
import { useMobileNav } from "@/components/mobile-nav-context";
import { useLanguage } from "@/components/language-provider";
import { LanguageToggle } from "@/components/language-toggle";
import { VectorboxUser } from "@/lib/api";
import { useVectorboxLogout } from "@/hooks/useVectorboxLogout";

interface MobileNavProps {
    currentView: string;
    onViewChange: (view: string) => void;
    users?: VectorboxUser[];
    currentUserId?: number | null;
    onUserSelect?: (id: number) => void;
}

export function MobileNav({ currentView, onViewChange, users, currentUserId, onUserSelect }: MobileNavProps) {
    const { isOpen, setIsOpen } = useMobileNav();
    const { t } = useLanguage();
    const handleLogout = useVectorboxLogout();
    const { user: clerkUser } = useUser();
    const displayName = clerkUser?.fullName || clerkUser?.firstName || users?.find(u => u.id === currentUserId)?.username || "User";

    const menuItems = [
        { id: "feed", label: t("sidebar.feed"), icon: LayoutList },
        { id: "watchlist", label: t("sidebar.watchlist"), icon: Calendar },
        { id: "ai-search", label: t("sidebar.ai_search"), icon: Sparkles },
        { id: "more-like-this", label: t("sidebar.more_like_this"), icon: Film },
        { id: "compatibility", label: t("sections.group_vibe"), icon: Users },
        { id: "settings", label: t("sidebar.settings"), icon: Settings },
    ];

    const handleViewChange = (id: string) => {
        onViewChange(id);
        setIsOpen(false);
    };

    return (
        <AnimatePresence>
            {isOpen && (
                <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="fixed inset-0 z-50 bg-black/95 backdrop-blur-xl flex flex-col"
                    role="dialog"
                    aria-modal="true"
                    aria-label={t("ui.menu")}
                >
                    {/* Header */}
                    <div className="flex items-center justify-between p-4 border-b border-white/10">
                        <span className="font-mono font-bold text-xl tracking-wider text-white">{t("ui.menu")}</span>
                        <button
                            onClick={() => setIsOpen(false)}
                            className="p-2 text-white hover:text-primary transition-colors"
                            aria-label={t("aria.close_menu")}
                        >
                            <X className="w-8 h-8" />
                        </button>
                    </div>

                    {/* Menu Items */}
                    <div className="flex-1 overflow-y-auto py-8 px-6 flex flex-col gap-6 items-center justify-center">
                        {menuItems.map((item, index) => {
                            const Icon = item.icon;
                            const isActive = currentView === item.id;
                            return (
                                <motion.button
                                    key={item.id}
                                    initial={{ opacity: 0, y: 20 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: index * 0.05 }}
                                    onClick={() => handleViewChange(item.id)}
                                    className={`
                                        group flex items-center gap-4 text-2xl font-mono uppercase tracking-widest transition-all
                                        ${isActive ? "text-primary font-bold" : "text-white/60 hover:text-white"}
                                    `}
                                >
                                    <Icon className={`w-6 h-6 ${isActive ? "text-primary" : "hidden"}`} />
                                    <span>{item.label}</span>
                                    {isActive && <div className="w-2 h-2 rounded-full bg-primary shadow-[0_0_10px_hsl(var(--primary))]" />}
                                </motion.button>
                            );
                        })}
                    </div>

                    {/* Footer / User Profile */}
                    <div className="p-6 border-t border-white/10 space-y-6">
                        {users && currentUserId && (
                            <div className="space-y-4">
                                <div className="flex flex-col items-center gap-2">
                                    <div className="w-12 h-12 rounded-full bg-primary/20 flex items-center justify-center border border-primary/50">
                                        <UserIcon className="w-6 h-6 text-primary" />
                                    </div>
                                    <span className="text-lg font-mono text-white uppercase tracking-widest">
                                        {displayName}
                                    </span>
                                </div>
                                
                                <div className="grid grid-cols-2 gap-4">
                                    <button
                                        onClick={() => handleViewChange("profile")}
                                        className="flex flex-col items-center gap-2 p-4 bg-white/5 border border-white/10 hover:border-primary transition-colors group"
                                    >
                                        <UserIcon className="w-5 h-5 group-hover:text-primary" />
                                        <span className="text-[10px] font-mono uppercase tracking-widest text-zinc-400 group-hover:text-white">
                                            {t("sidebar.profile")}
                                        </span>
                                    </button>
                                    
                                    <button
                                        onClick={handleLogout}
                                        className="flex flex-col items-center gap-2 p-4 bg-white/5 border border-white/10 hover:border-red-500 transition-colors group"
                                    >
                                        <LogOut className="w-5 h-5 text-red-500 group-hover:text-red-400" />
                                        <span className="text-[10px] font-mono uppercase tracking-widest text-zinc-400 group-hover:text-white">
                                            {t("app.logout")}
                                        </span>
                                    </button>
                                </div>
                            </div>
                        )}

                        <div className="flex justify-center">
                            <LanguageToggle isCollapsed={false} />
                        </div>
                    </div>
                </motion.div>
            )}
        </AnimatePresence>
    );
}
