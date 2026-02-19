"use client";

import { useState, useEffect } from "react";
import { Info, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useLanguage } from "@/components/language-provider";

interface InfoTooltipProps {
    id: string;
    title: string;
    description: string;
    className?: string;
}

export function InfoTooltip({ id, title, description, className = "" }: InfoTooltipProps) {
    const [isOpen, setIsOpen] = useState(false);
    const { t } = useLanguage();

    return (
        <div className={`relative inline-block ${className}`}>
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="p-1 rounded-full hover:bg-primary/10 text-muted-foreground hover:text-primary transition-colors"
                aria-label={t("aria.more_info")}
            >
                <Info className="w-4 h-4" />
            </button>

            <AnimatePresence>
                {isOpen && (
                    <>
                        {/* Backdrop */}
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setIsOpen(false)}
                            className="fixed inset-0 bg-black/20 backdrop-blur-sm z-40"
                        />

                        {/* Tooltip */}
                        <motion.div
                            initial={{ opacity: 0, scale: 0.95, y: -10 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.95, y: -10 }}
                            className="absolute left-0 top-full mt-2 w-80 bg-card border rounded-lg shadow-2xl p-4 z-50 origin-top-left"
                        >
                            <div className="flex items-start justify-between gap-2 mb-2">
                                <h4 className="font-bold text-sm">{title}</h4>
                                <button
                                    onClick={() => setIsOpen(false)}
                                    className="text-muted-foreground hover:text-foreground transition-colors"
                                >
                                    <X className="w-4 h-4" />
                                </button>
                            </div>
                            <p className="text-sm text-muted-foreground leading-relaxed">{description}</p>
                        </motion.div>
                    </>
                )}
            </AnimatePresence>
        </div>
    );
}

// Global app tooltip for sidebar
export function AppTooltip({ isCollapsed }: { isCollapsed?: boolean }) {
    const [isOpen, setIsOpen] = useState(false);
    const [dismissed, setDismissed] = useState(false);
    const [mounted, setMounted] = useState(false);
    const { t } = useLanguage();

    useEffect(() => {
        setMounted(true);
        const seen = localStorage.getItem("app_tooltip_seen");
        if (!seen) {
            // Auto-show on first visit after a delay
            const timeout = setTimeout(() => setIsOpen(true), 2000);
            return () => clearTimeout(timeout);
        } else {
            setDismissed(true);
        }
    }, []);

    const handleDismiss = () => {
        localStorage.setItem("app_tooltip_seen", "true");
        setDismissed(true);
        setIsOpen(false);
    };

    const toggleOpen = () => {
        if (dismissed) setDismissed(false);
        setIsOpen(!isOpen);
    };

    // Portal content
    const modalContent = (
        <AnimatePresence>
            {isOpen && (
                <>
                    {/* Backdrop - z-[49] to be below Sidebar (z-[50]) but above content */}
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={() => setIsOpen(false)}
                        className="fixed inset-0 bg-black/50 backdrop-blur-sm z-[49]"
                    />

                    {/* Tooltip - z-[51] to be above Sidebar */}
                    <motion.div
                        initial={{ opacity: 0, x: -20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: -20 }}
                        className={`fixed w-80 bg-black border border-primary shadow-[0_0_30px_rgba(204,255,0,0.1)] p-6 z-[51] ${isCollapsed
                            ? "left-[80px] bottom-4"
                            : "left-[320px] bottom-4"
                            }`}
                    >
                        <div className="flex items-start justify-between gap-2 mb-3">
                            <h3 className="font-mono font-bold text-lg text-primary uppercase">{t("guide.title")}</h3>
                            <button
                                onClick={handleDismiss}
                                className="text-zinc-500 hover:text-primary transition-colors"
                            >
                                <X className="w-4 h-4" />
                            </button>
                        </div>

                        <div className="space-y-4 text-xs text-zinc-400 font-mono">
                            <p>
                                <strong className="text-white">{t("guide.welcome")}</strong>
                            </p>

                            <ul className="space-y-3">
                                <li className="flex gap-2">
                                    <span className="text-lg">🎬</span>
                                    <div>
                                        <strong className="text-white block uppercase tracking-wider">{t("sidebar.feed")}</strong>
                                        {t("guide.feed")}
                                    </div>
                                </li>
                                <li className="flex gap-2">
                                    <span className="text-lg">🎨</span>
                                    <div>
                                        <strong className="text-white block uppercase tracking-wider">{t("sidebar.grid")}</strong>
                                        {t("guide.grid")}
                                    </div>
                                </li>
                                <li className="flex gap-2">
                                    <span className="text-lg">📋</span>
                                    <div>
                                        <strong className="text-white block uppercase tracking-wider">{t("sidebar.watchlist")}</strong>
                                        {t("guide.watchlist")}
                                    </div>
                                </li>
                                <li className="flex gap-2">
                                    <span className="text-lg">✨</span>
                                    <div>
                                        <strong className="text-white block uppercase tracking-wider">{t("sidebar.ai_search")}</strong>
                                        {t("guide.ai_search")}
                                    </div>
                                </li>
                                <li className="flex gap-2">
                                    <span className="text-lg">🎯</span>
                                    <div>
                                        <strong className="text-white block uppercase tracking-wider">{t("sidebar.more_like_this")}</strong>
                                        {t("guide.more_like_this")}
                                    </div>
                                </li>
                                <li className="flex gap-2">
                                    <span className="text-lg">👥</span>
                                    <div>
                                        <strong className="text-white block uppercase tracking-wider">{t("group_vibe.title")}</strong>
                                        {t("group_vibe.desc")}
                                    </div>
                                </li>
                            </ul>

                            <button
                                onClick={handleDismiss}
                                className="mt-2 w-full px-4 py-2 bg-primary text-black rounded-none hover:bg-primary/90 transition-colors text-xs font-bold uppercase tracking-widest"
                            >
                                {t("guide.got_it")}
                            </button>
                        </div>
                    </motion.div>
                </>
            )}
        </AnimatePresence>
    );

    if (!mounted) return null;

    // If we are rendering the button
    return (
        <>
            <button
                onClick={toggleOpen}
                className={`
                    w-full flex items-center gap-4 px-3 py-2 transition-all group
                    text-zinc-500 hover:text-white hover:bg-zinc-900/50
                    ${isCollapsed ? "justify-center" : ""}
                `}
                title={t("app.guide")}
            >
                <Info className="flex-shrink-0 w-4 h-4" />
                {!isCollapsed && (
                    <span className="text-xs font-mono uppercase tracking-wider truncate">
                        {t("app.guide")}
                    </span>
                )}
            </button>

            {/* Render modal via Portal to ensure correct z-index stacking */}
            {typeof document !== 'undefined' &&
                require('react-dom').createPortal(modalContent, document.body)
            }
        </>
    );
}
