"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { m } from "framer-motion";
import {
    TagSelector,
    TagState,
    tagStateToPreferences,
    preferencesToTagState,
} from "@/components/onboarding/tag-selector";
import { api } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";

export default function OnboardingTagsPage() {
    const { push } = useRouter();
    const { t } = useLanguage();
    const [states, setStates] = useState<Record<string, TagState>>({});
    // useState (not useRef) so the post-hydration re-render escapes the
    // [LOADING] branch below. Same trap as Dashboard (commit fd9122f).
    const [hydrated, setHydrated] = useState(false);

    useEffect(() => {
        setHydrated(true);
        // Make sure the guest has an anon session so the POST /tags below
        // can authenticate via cookie. Idempotent — no-op if cookie already set.
        api.post("/api/onboarding/init-session").catch(() => { /* offline ok */ });
        const saved = localStorage.getItem("vb_guest_tags:v1");
        if (saved) {
            try {
                setStates(preferencesToTagState(JSON.parse(saved)));
            } catch { /* corrupt - start fresh */ }
        }
    }, []);

    const handleChange = (next: Record<string, TagState>) => {
        setStates(next);
        const prefs = tagStateToPreferences(next);
        localStorage.setItem("vb_guest_tags:v1", JSON.stringify(prefs));
        // Persist server-side too so claim-anonymous can copy the tags on
        // signup. Fire-and-forget; localStorage is the offline fallback.
        api.post("/api/onboarding/tags", prefs).catch(() => { /* offline ok */ });
    };

    const handleContinue = () => push("/onboarding");

    if (!hydrated) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-background">
                <span className="font-mono text-xs text-fg-3">[ LOADING ]</span>
            </div>
        );
    }

    const selectedCount = Object.values(states).filter((v) => v !== "neutral").length;

    return (
        <div className="relative flex min-h-screen flex-col items-center justify-center bg-bg p-4 text-fg">
            <m.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4 }}
                className="z-10 w-full max-w-5xl space-y-8"
            >
                <div className="space-y-2 text-center">
                    <p className="eyebrow text-primary">{t("wiz.taste_title")}</p>
                    <h1 className="font-display text-3xl uppercase tracking-[-0.02em] md:text-4xl">
                        {t("gonb.prefs_1")} <span className="text-primary">{t("gonb.prefs_2")}</span>
                    </h1>
                    <p className="font-mono text-xs uppercase tracking-widest text-fg-3">
                        {t("gonb.tap_note")}
                    </p>
                </div>

                <div className="border border-border-2 bg-bg-2 p-6 shadow-acid md:p-8">
                    <TagSelector value={states} onChange={handleChange} />
                </div>

                <div className="flex items-center justify-between">
                    <button onClick={handleContinue} className="font-mono text-xs uppercase tracking-wider text-fg-3 transition-colors hover:text-fg-2">
                        {t("wiz.skip_all")}
                    </button>
                    <div className="flex items-center gap-4">
                        {selectedCount > 0 && (
                            <span className="font-mono text-[10px] text-fg-3">{selectedCount} {t("wiz.filters_active")}</span>
                        )}
                        <button onClick={handleContinue} className="border border-primary bg-primary px-6 py-2.5 font-display text-xs font-bold uppercase tracking-wider text-primary-ink transition-colors hover:bg-transparent hover:text-primary">
                            {t("gonb.continue")}
                        </button>
                    </div>
                </div>

                <div className="text-center">
                    <button onClick={() => push("/login")} className="font-mono text-[10px] uppercase tracking-wider text-fg-3 transition-colors hover:text-fg-2">
                        {t("gonb.back_login")}
                    </button>
                </div>
            </m.div>
        </div>
    );
}
