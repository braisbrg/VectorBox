"use client";

import { motion, AnimatePresence } from "framer-motion";
import { X, LayoutList, Grid3x3, Calendar, Sparkles, Film, Users, Settings, User as UserIcon } from "lucide-react";
import { useMobileNav } from "@/components/mobile-nav-context";
import { useLanguage } from "@/components/language-provider";
import { LanguageToggle } from "@/components/language-toggle";
import { VectorboxUser } from "@/lib/api";

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

    const menuItems = [
        { id: "feed", label: t("sidebar.feed"), icon: LayoutList },
        { id: "grid", label: t("sidebar.grid"), icon: Grid3x3 },
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

                    {/* Footer / User Select */}
                    <div className="p-6 border-t border-white/10 space-y-6">
                        {users && users.length > 0 && currentUserId && onUserSelect && (
                            <div className="space-y-2">
                                <label className="text-xs font-mono uppercase text-white/40 block text-center">
                                    {t("sidebar.current_user")}
                                </label>
                                <div className="flex justify-center">
                                    <div className="relative w-full max-w-[200px]">
                                        <select
                                            value={currentUserId}
                                            onChange={(e) => onUserSelect(Number(e.target.value))}
                                            className="w-full appearance-none bg-white/5 border border-white/20 text-white text-sm font-mono rounded-none py-3 pl-10 pr-4 focus:outline-none focus:border-primary text-center"
                                        >
                                            {users.map(u => (
                                                <option key={u.id} value={u.id} className="bg-black text-white">
                                                    {u.username}
                                                </option>
                                            ))}
                                        </select>
                                        <UserIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/50 pointer-events-none" />
                                    </div>
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
