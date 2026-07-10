"use client";

// Mobile sub-screen back row (handoff .wl-back/.set-back): the tab bar is
// hidden on sub-screens, so this full-width bar is the way back. One shared
// component so every sub-screen navigates the same way.

import { useContextualBack } from "@/lib/use-back";
import { useLanguage } from "@/components/language-provider";

interface SubScreenHeaderProps {
    /** i18n key (e.g. "crumbs.watchlist") or literal breadcrumb text. */
    crumb: string;
    /** Where to go on a cold deep-link with no in-app history. */
    fallback?: string;
}

export function SubScreenHeader({ crumb, fallback = "/you" }: SubScreenHeaderProps) {
    const goBack = useContextualBack(fallback);
    const { t } = useLanguage();
    const resolved = t(crumb);
    const label = resolved === crumb ? crumb : resolved;
    return (
        <button
            onClick={goBack}
            className="mb-3 flex min-h-[48px] w-full items-center gap-3 border border-border-2 bg-bg-2 px-4 text-left font-mono text-[11px] uppercase tracking-wider text-fg-2 transition-colors active:bg-bg-3 lg:hidden"
        >
            <span className="font-display text-lg leading-none text-primary">←</span>
            <span>{label}</span>
        </button>
    );
}
