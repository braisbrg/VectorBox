"use client";

import { WatchlistView } from "@/components/watchlist-view";
import { useShell } from "@/components/shell/shell-context";

export default function WatchlistPage() {
    const { session, countryCode, streamingProviders, inspect } = useShell();

    return (
        <WatchlistView
            userId={session.id}
            username={session.username}
            countryCode={countryCode}
            streamingProviders={streamingProviders}
            onInspect={inspect}
        />
    );
}
