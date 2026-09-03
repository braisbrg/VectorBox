"use client";

import Link from "next/link";
import { Wordmark } from "@/components/ui/wordmark";
import { useUser } from "@clerk/nextjs";
import { openMagicBox } from "@/components/magic-box";
import { useLanguage } from "@/components/language-provider";

/**
 * Universal topbar (handoff mobile: brand-only top bar, no hamburger):
 * wordmark · ⌘K magic-box indicator (desktop only) · avatar → /you.
 * The ⌘K listener lives in MagicBoxModal (mounted in AppShell).
 */
export function ShellTopbar() {
    const { user } = useUser();
    const { t } = useLanguage();
    const initial = (user?.username || user?.firstName || "U").charAt(0).toUpperCase();

    return (
        <header className="sticky top-0 z-30 flex h-[60px] items-center justify-between border-b border-border-2 bg-bg px-4 lg:px-6">
            {/* Mobile only. The sidebar (lg:flex) carries the mark on desktop, in a
                60px header that lines up with this one — so both were on screen at
                the same height and the brand read twice. The sidebar wins because
                it is the persistent chrome and it survives collapse as the trident;
                below lg there is no sidebar, so this is the only brand surface. */}
            <Link href="/feed" className="font-display text-xl uppercase tracking-tight lg:hidden">
                <Wordmark />
            </Link>

            {/* ml-auto, not justify-between: above lg the wordmark is display:none
                and leaves a single flex child, which justify-between packs to the
                LEFT. The margin holds the actions right in both states. */}
            <div className="ml-auto flex items-center gap-3">
                {/* Dos botones de igual peso, con su etiqueta visible. La versión
                    anterior era un botón ancho con texto y otro de un solo carácter
                    pegado al lado: no se leía como dos cosas, parecía un icono
                    decorativo del primero. */}
                <div className="hidden items-center gap-1.5 lg:flex">
                    <button
                        onClick={() => openMagicBox("vibe")}
                        title={t("mb.mode_vibe")}
                        className="group flex items-center gap-2 border border-border-2 bg-bg-2 px-3 py-2 font-mono text-xs text-fg-3 transition-colors hover:border-primary hover:text-primary"
                    >
                        <span className="text-primary">◐</span>
                        <span>{t("mb.mode_vibe")}</span>
                    </button>
                    <button
                        onClick={() => openMagicBox("title")}
                        title={t("mb.mode_title")}
                        className="group flex items-center gap-2 border border-border-2 bg-bg-2 px-3 py-2 font-mono text-xs text-fg-3 transition-colors hover:border-primary hover:text-primary"
                    >
                        <span className="text-primary">⌕</span>
                        <span>{t("mb.mode_title")}</span>
                    </button>
                    <kbd className="ml-0.5 border border-border-2 bg-bg-3 px-1.5 py-0.5 font-display text-[9px] text-fg-2">⌘K</kbd>
                </div>
                <Link
                    href="/you"
                    aria-label="Profile"
                    className="flex size-9 items-center justify-center bg-primary font-display text-sm text-primary-ink transition-opacity hover:opacity-80"
                >
                    {initial}
                </Link>
            </div>
        </header>
    );
}
