"use client";

import { useState, useEffect } from "react";
import { Info, X } from "lucide-react";
import { m, AnimatePresence } from "framer-motion";
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
                className="p-1 hover:bg-primary/10 text-fg-3 hover:text-primary transition-colors"
                aria-label={t("aria.more_info")}
            >
                <Info className="size-4" />
            </button>

            <AnimatePresence>
                {isOpen && (
                    <>
                        {/* Backdrop */}
                        <m.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            onClick={() => setIsOpen(false)}
                            className="fixed inset-0 bg-bg/40 backdrop-blur-sm z-40"
                        />

                        {/* Tooltip */}
                        <m.div
                            initial={{ opacity: 0, scale: 0.95, y: -10 }}
                            animate={{ opacity: 1, scale: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.95, y: -10 }}
                            className="absolute left-0 top-full mt-2 w-80 bg-bg-2 border border-border-2 shadow-acid p-4 z-50 origin-top-left"
                        >
                            <div className="flex items-start justify-between gap-2 mb-2">
                                <h4 className="font-mono font-bold text-sm text-primary uppercase">{title}</h4>
                                <button
                                    onClick={() => setIsOpen(false)}
                                    className="text-fg-3 hover:text-primary transition-colors"
                                >
                                    <X className="size-4" />
                                </button>
                            </div>
                            <p className="text-sm text-fg-2 leading-relaxed font-mono">{description}</p>
                        </m.div>
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
    // useState (not useRef) — mutating a ref does not trigger re-render, so
    // the `if (!mounted) return null` guard below would stay truthy forever
    // and the tooltip would never paint. Same trap as Dashboard (fd9122f).
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

    // Current sidebar nav (handoff): feed · similars · space · groups · watchlist · magic box.
    const guideRows: { icon: string; label: string; desc: string }[] = [
        { icon: "🎬", label: "sidebar.feed", desc: "guide.feed" },
        { icon: "🎞️", label: "sidebar.more_like_this", desc: "guide.similars" },
        { icon: "🌌", label: "sidebar.space", desc: "guide.space" },
        { icon: "👥", label: "sections.group_vibe", desc: "guide.groups" },
        { icon: "📋", label: "sidebar.watchlist", desc: "guide.watchlist" },
        { icon: "✨", label: "sidebar.magic_box", desc: "guide.magic_box" },
    ];

    // Portal content
    const modalContent = (
        <AnimatePresence>
            {isOpen && (
                <>
                    {/* Backdrop - z-[49] to be below Sidebar (z-[50]) but above content */}
                    <m.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={() => setIsOpen(false)}
                        className="fixed inset-0 bg-bg/60 backdrop-blur-sm z-[49]"
                    />

                    {/* Tooltip - z-[51] to be above Sidebar */}
                    <m.div
                        initial={{ opacity: 0, x: -20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: -20 }}
                        className={`fixed w-80 bg-bg-2 border border-primary shadow-acid p-6 z-[51] ${isCollapsed
                            ? "left-[80px] bottom-4"
                            : "left-[320px] bottom-4"
                            }`}
                    >
                        <div className="flex items-start justify-between gap-2 mb-3">
                            <h3 className="font-mono font-bold text-lg text-primary uppercase">{t("guide.title")}</h3>
                            <button
                                onClick={handleDismiss}
                                className="text-fg-3 hover:text-primary transition-colors"
                            >
                                <X className="size-4" />
                            </button>
                        </div>

                        <div className="space-y-4 text-xs text-fg-2 font-mono">
                            <p>
                                <strong className="text-fg">{t("guide.welcome")}</strong>
                            </p>

                            <ul className="space-y-3">
                                {guideRows.map((row) => (
                                    <li key={row.label} className="flex gap-2">
                                        <span className="text-lg">{row.icon}</span>
                                        <div>
                                            <strong className="text-fg block uppercase tracking-wider">{t(row.label)}</strong>
                                            {t(row.desc)}
                                        </div>
                                    </li>
                                ))}
                            </ul>

                            <button
                                onClick={handleDismiss}
                                className="mt-2 w-full px-4 py-2 bg-primary text-primary-ink hover:bg-primary/90 transition-colors text-xs font-bold uppercase tracking-widest"
                            >
                                {t("guide.got_it")}
                            </button>
                        </div>
                    </m.div>
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
                <Info className="flex-shrink-0 size-4" />
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
