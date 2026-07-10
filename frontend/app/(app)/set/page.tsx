"use client";

import { SettingsView } from "@/components/settings-view";
import { SubScreenHeader } from "@/components/shell/sub-screen-header";

export default function SettingsPage() {
    return (
        <>
            {/* /set is a mobile sub-screen (tab bar hidden) — this row is the way back */}
            <div className="pt-4 lg:pt-0">
                <SubScreenHeader crumb="crumbs.settings" fallback="/you" />
            </div>
            <SettingsView />
        </>
    );
}
