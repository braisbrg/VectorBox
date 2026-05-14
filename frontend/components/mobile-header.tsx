"use client";

import { Menu } from "lucide-react";
import { useMobileNav } from "@/components/mobile-nav-context";
import { useLanguage } from "@/components/language-provider";

export function MobileHeader() {
    const { setIsOpen } = useMobileNav();
    const { t } = useLanguage();

    return (
        <header className="lg:hidden fixed top-0 left-0 right-0 h-[60px] bg-zinc-950/80 backdrop-blur-md border-b border-zinc-800 flex items-center justify-between px-4 z-40">
            {/* Logo */}
            <div className="flex items-center gap-2">
                <div className="size-8 bg-primary flex items-center justify-center">
                    <span className="font-mono font-bold text-black text-xl">V</span>
                </div>
                <span className="font-mono font-bold text-lg tracking-wider text-white">VECTORBOX</span>
            </div>

            {/* Hamburger Button */}
            <button
                onClick={() => setIsOpen(true)}
                className="p-2 text-white hover:text-primary transition-colors"
                aria-label={t("aria.open_menu")}
            >
                <Menu className="size-6" />
            </button>
        </header>
    );
}
