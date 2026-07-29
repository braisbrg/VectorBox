"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Sparkles } from "lucide-react";
import { MOBILE_TABS, MOBILE_SUBSCREENS, navLabel, isActive, type NavItem } from "@/components/shell/nav";
import { openMagicBox } from "@/components/magic-box";
import { useLanguage } from "@/components/language-provider";
import { cn } from "@/lib/utils";

// Handoff-locked variant: the prototype ships the center magic slot as an INLINE
// tab (lime-square icon), while the decisions doc drew a raised FAB. User chose
// "tab" (2026-07-04) — flip this literal if that ever reverses.
const MAGIC_CENTER: "tab" | "fab" = "tab";

/** Mobile bottom nav (handoff): feed · similars · [magic] · groups · you. */
export function MobileTabBar() {
    const pathname = usePathname();
    const { t } = useLanguage();
    const [feed, similars, groups, you] = MOBILE_TABS;

    // Sub-screens render their own back-rows; the tab bar disappears (handoff).
    if (MOBILE_SUBSCREENS.some((p) => isActive(pathname, p))) return null;

    return (
        <nav className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-5 items-stretch border-t border-border-2 bg-bg-2 pb-[env(safe-area-inset-bottom)] lg:hidden">
            <Tab item={feed} pathname={pathname} label={navLabel(t, feed)} />
            <Tab item={similars} pathname={pathname} label={navLabel(t, similars)} />
            <MagicCenter t={t} />
            <Tab item={groups} pathname={pathname} label={navLabel(t, groups)} />
            {/* `you` stays lit on its sub-screens (handoff) — harmless while the bar is hidden there */}
            <Tab
                item={you}
                pathname={pathname}
                label={navLabel(t, you)}
                forceActive={isActive(pathname, "/watch") || isActive(pathname, "/set")}
            />
        </nav>
    );
}

function MagicCenter({ t }: { t: (k: string) => string }) {
    const label = t("mobile.magic") === "mobile.magic" ? "magic" : t("mobile.magic");
    if (MAGIC_CENTER === "fab") {
        return (
            <button onClick={openMagicBox} aria-label={label} className="flex flex-col items-center justify-center">
                <span className="-mt-6 flex size-12 items-center justify-center border border-primary bg-primary text-primary-ink shadow-acid-fg">
                    <Sparkles className="size-6" />
                </span>
            </button>
        );
    }
    return (
        <button
            onClick={openMagicBox}
            aria-label={label}
            className="flex min-h-[54px] flex-col items-center justify-center gap-1 text-[9px] uppercase tracking-[0.08em] text-primary"
        >
            <span className="flex size-6 items-center justify-center bg-primary">
                <Sparkles className="size-3.5 text-primary-ink" />
            </span>
            <span>{label}</span>
        </button>
    );
}

function Tab({ item, pathname, label, forceActive = false }: { item: NavItem; pathname: string; label: string; forceActive?: boolean }) {
    const Icon = item.icon;
    const active = forceActive || isActive(pathname, item.href);
    return (
        <Link
            href={item.href}
            className={cn(
                "flex min-h-[54px] flex-col items-center justify-center gap-1 text-[9px] uppercase tracking-[0.08em] transition-colors",
                active ? "text-primary" : "text-fg-3"
            )}
        >
            <Icon className="size-[17px]" />
            <span>{label}</span>
        </Link>
    );
}
