"use client";

// Mobile SYS_CONSOLE access on /feed (handoff: filters FAB → bottom sheet).
// Same <FilterForm> as the desktop rail so the two can't drift.

import { useState } from "react";
import { FilterSearchParams } from "@/lib/api";
import { FilterFab } from "@/components/shell/filter-fab";
import { BottomSheet } from "@/components/shell/bottom-sheet";
import { FilterForm } from "@/components/filter-form";
import { useLanguage } from "@/components/language-provider";

interface FeedFilterSheetProps {
    countryCode: string;
    onCountryChange: (code: string) => void;
    streamingProviders: number[];
    onToggleProvider: (id: number) => void;
    onClearFilters: () => void;
    onFilterSearch: (params: FilterSearchParams) => void;
    filteredCount: number | null;
}

export function FeedFilterSheet({
    countryCode,
    onCountryChange,
    streamingProviders,
    onToggleProvider,
    onClearFilters,
    onFilterSearch,
    filteredCount,
}: FeedFilterSheetProps) {
    const [open, setOpen] = useState(false);
    const { t } = useLanguage();

    return (
        <>
            <FilterFab count={streamingProviders.length} onClick={() => setOpen(true)} aboveTabBar />
            <BottomSheet open={open} onClose={() => setOpen(false)} ariaLabel="Filters">
                <div className="flex items-center justify-between border-b border-border-2 px-5 pb-3">
                    <span className="text-xs font-bold uppercase tracking-widest text-primary">SYS_CONSOLE</span>
                    <div className="size-2 animate-pulse bg-primary" />
                </div>
                <div className="overflow-y-auto px-5 py-5 pb-[calc(env(safe-area-inset-bottom)+24px)] text-xs">
                    <FilterForm
                        countryCode={countryCode}
                        onCountryChange={onCountryChange}
                        streamingProviders={streamingProviders}
                        onToggleProvider={onToggleProvider}
                        onClearFilters={onClearFilters}
                        onFilterSearch={(params) => {
                            onFilterSearch(params);
                            setOpen(false); // filtered view renders behind the sheet
                        }}
                        filteredCount={filteredCount}
                        submitLabel={t("filters.apply")}
                    />
                </div>
            </BottomSheet>
        </>
    );
}
