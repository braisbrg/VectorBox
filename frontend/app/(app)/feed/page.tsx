"use client";

import { FeedContainer } from "@/components/feed-container";
import { useShell } from "@/components/shell/shell-context";

export default function FeedPage() {
    const {
        session,
        users,
        scope,
        countryCode,
        streamingProviders,
        inspect,
        inspected,
        closeInspector,
        filteredResults,
        isFiltering,
        clearFilterResults,
    } = useShell();

    return (
        <FeedContainer
            userId={session.id}
            scope={scope}
            countryCode={countryCode}
            streamingProviders={streamingProviders}
            initialData={null}
            registeredUsers={users}
            onInspect={inspect}
            inspected={inspected}
            onCloseInspect={closeInspector}
            filteredResults={filteredResults}
            isFiltering={isFiltering}
            onClearFilterResults={clearFilterResults}
        />
    );
}
