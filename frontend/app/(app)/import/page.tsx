"use client";

// Standalone import screen — mounts the same 5-step wizard as the onboarding
// jail (handoff: /import is the settings-reachable import surface). Settings
// "re-link" enters at identify, "re-upload" at import via ?step=.

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ImportWizard } from "@/components/onboarding/import-wizard";
import { SubScreenHeader } from "@/components/shell/sub-screen-header";
import { useShell } from "@/components/shell/shell-context";

function ImportContent() {
    const { session } = useShell();
    const queryClient = useQueryClient();
    const router = useRouter();
    const searchParams = useSearchParams();
    const stepParam = searchParams.get("step");
    const initialStep = stepParam === "identify" || stepParam === "import" ? stepParam : undefined;

    return (
        <div className="flex flex-col items-center pt-6">
            <div className="w-full max-w-2xl">
                {/* /import is a mobile sub-screen (tab bar hidden) — back row first */}
                <SubScreenHeader crumb="crumbs.import" fallback="/you" />
                <h1 className="mb-1 font-display text-2xl uppercase tracking-[-0.02em] text-fg">import</h1>
                <p className="tiny mb-5">letterboxd zip export · account link · rolling rss sync</p>
            </div>
            <ImportWizard
                session={session}
                initialStep={initialStep}
                onComplete={() => {
                    queryClient.invalidateQueries({ queryKey: ["feed"] });
                    router.push("/feed?onboarding_complete=true");
                }}
            />
        </div>
    );
}

export default function ImportPage() {
    return (
        <Suspense fallback={null}>
            <ImportContent />
        </Suspense>
    );
}
