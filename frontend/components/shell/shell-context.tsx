"use client";

import { createContext, useContext } from "react";
import type { FeedItem, FeedResponse, UserSession, VectorboxUser } from "@/lib/api";

/**
 * Shared shell state, formerly local to dashboard.tsx. The App Router shell
 * (AppShell) owns the filter/inspector state and the right-console; route
 * pages read what they need through this context instead of prop-drilling.
 */
export interface ShellContextValue {
    session: UserSession;
    users: VectorboxUser[];
    scope: "watchlist" | "global";
    countryCode: string;
    streamingProviders: number[];
    /** Setters shared by the rail AND settings→providers (single source of truth). */
    setCountryCode: (code: string) => void;
    toggleProvider: (id: number) => void;
    /** Open the right-console / mobile inspector for a movie. */
    inspect: (movie: FeedItem, sectionId?: string) => void;
    /** Currently inspected movie (drives the feed's mobile inline inspector). */
    inspected: { movie: FeedItem; sectionId?: string } | null;
    closeInspector: () => void;
    /** F8: rail EXECUTE_QUERY result — a sectioned filtered feed (null = none active). */
    filteredResults: FeedResponse | null;
    isFiltering: boolean;
    clearFilterResults: () => void;
}

const ShellContext = createContext<ShellContextValue | null>(null);

export function useShell(): ShellContextValue {
    const ctx = useContext(ShellContext);
    if (!ctx) throw new Error("useShell must be used within <AppShell>");
    return ctx;
}

export const ShellProvider = ShellContext.Provider;
