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

export default function OnboardingTagsPage() {
    const { push } = useRouter();
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
                <span className="font-mono text-xs text-zinc-600">[ LOADING ]</span>
            </div>
        );
    }

    const selectedCount = Object.values(states).filter((v) => v !== "neutral").length;

    return (
        <div className="min-h-screen bg-background text-foreground flex flex-col items-center justify-center p-4 relative overflow-hidden">
            <div className="absolute inset-0 bg-[url('/grid-pattern.svg')] opacity-10 pointer-events-none" />
            <m.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4 }}
                className="z-10 w-full max-w-3xl space-y-8"
            >
                <div className="text-center space-y-2">
                    <h1 className="text-3xl md:text-4xl font-semibold tracking-tighter font-mono uppercase">
                        CONTENT <span className="text-primary">PREFERENCES</span>
                    </h1>
                    <p className="text-zinc-500 font-mono text-xs uppercase tracking-widest">
                        // TAP TO TOGGLE: NEUTRAL ↔ AVOID //
                    </p>
                </div>

                <div className="bg-card border border-border/50 p-6 md:p-8">
                    <TagSelector value={states} onChange={handleChange} />
                </div>

                <div className="flex items-center justify-between">
                    <button onClick={handleContinue} className="text-xs font-mono text-zinc-600 hover:text-zinc-400 transition-colors uppercase tracking-wider">
                        [ SKIP ]
                    </button>
                    <div className="flex items-center gap-4">
                        {selectedCount > 0 && (
                            <span className="text-[10px] font-mono text-zinc-600">{selectedCount} selected</span>
                        )}
                        <button onClick={handleContinue} className="px-6 py-2.5 bg-primary text-black font-bold font-mono uppercase tracking-wider text-xs hover:bg-primary/90 transition-colors glow-primary-hover">
                            CONTINUE →
                        </button>
                    </div>
                </div>

                <div className="text-center">
                    <button onClick={() => push("/login")} className="text-[10px] font-mono text-zinc-700 hover:text-zinc-500 transition-colors uppercase tracking-wider">
                        ← BACK TO LOGIN
                    </button>
                </div>
            </m.div>
        </div>
    );
}
