import type { QueryClient } from "@tanstack/react-query";

/**
 * Module-level debounce for feed invalidation. Watched/reject clicks fire
 * across dashboard (inspector), movie-carousel (per-card buttons), and any
 * future caller — all routes go through this single timer so rapid-fire
 * clicks coalesce into ONE refetch 3s after the LAST click.
 *
 * Why a singleton instead of a per-component useRef + useCallback:
 * - Multiple callers (dashboard + carousel + onboarding-complete) shared
 *   nothing under the old design — each had its OWN timer, so a click in
 *   the carousel didn't reset the dashboard's debounce.
 * - movie-carousel.tsx was calling queryClient.invalidateQueries directly
 *   (no debounce at all), which is what caused the 429-rate-limit spiral
 *   the user reported in round 6.
 * - One singleton enforces "at most one feed refetch per 3s window" no
 *   matter who clicks what.
 */
let timer: ReturnType<typeof setTimeout> | null = null;

export function scheduleFeedInvalidation(queryClient: QueryClient, delayMs = 3000) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ["feed"] });
        timer = null;
    }, delayMs);
}

export function cancelPendingFeedInvalidation() {
    if (timer) {
        clearTimeout(timer);
        timer = null;
    }
}
