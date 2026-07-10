"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";

/**
 * Contextual back: return to wherever the user came from (feed, watchlist,
 * mlt, …) — falls back to /feed on a cold deep-link where there is no
 * in-app history entry.
 */
export function useContextualBack(fallback: string = "/feed") {
    const router = useRouter();
    return useCallback(() => {
        // idx > 0 means the SPA has navigated at least once in this tab.
        if (typeof window !== "undefined" && window.history.length > 1 && window.history.state?.idx !== 0) {
            router.back();
        } else {
            router.push(fallback);
        }
    }, [router, fallback]);
}
