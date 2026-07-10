"use client";

import { SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";

interface FilterFabProps {
    /** Active-filter count shown as a lime badge (hidden at 0). */
    count: number;
    onClick: () => void;
    /** True on routes where the bottom tab bar is visible (feed) — lifts the FAB above it. */
    aboveTabBar?: boolean;
}

/** Mobile filters FAB (handoff ⊟, bottom-right, shown on feed + watchlist only). */
export function FilterFab({ count, onClick, aboveTabBar = false }: FilterFabProps) {
    return (
        <button
            onClick={onClick}
            aria-label="Open filters"
            className={cn(
                "fixed right-4 z-40 flex size-12 items-center justify-center border border-border-2 bg-bg-2 text-fg-2 shadow-acid transition-colors hover:border-primary hover:text-primary lg:hidden",
                aboveTabBar
                    ? "bottom-[calc(64px+env(safe-area-inset-bottom)+12px)]"
                    : "bottom-[calc(env(safe-area-inset-bottom)+16px)]"
            )}
        >
            <SlidersHorizontal className="size-5" />
            {count > 0 && (
                <span className="absolute -right-1.5 -top-1.5 flex size-5 items-center justify-center bg-primary font-display text-[10px] font-bold text-primary-ink">
                    {count}
                </span>
            )}
        </button>
    );
}
