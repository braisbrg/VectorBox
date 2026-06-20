"use client";

import { Suspense, useState, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth, SignIn } from "@clerk/nextjs";
import { m, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";
import { Loader2 } from "lucide-react";

function LoginContent() {
    const { isLoaded, isSignedIn } = useAuth();
    const { push } = useRouter();
    const searchParams = useSearchParams();
    const isMigrate = searchParams.get("migrate") === "true";
    const redirectUrl = isMigrate ? "/login?migrate=true" : "/";
    const [mode, setMode] = useState<"choose" | "letterboxd">(
        isMigrate ? "letterboxd" : "choose"
    );
    const [migrating, setMigrating] = useState(false);
    const migrationAttempted = useRef(false);
    const newUserCheckAttempted = useRef(false);

    // Fix 1 removed: no longer use local storage for guest rating checks.

    // After sign-in: migrate guest data or show onboarding chooser for new users
    useEffect(() => {
        if (!isLoaded || !isSignedIn) return;

        // Clear stale guest localStorage. Tags now persist server-side (the
        // guest /onboarding/tags page POSTs to the API, and claim-anonymous
        // copies tag_preferences atomically with ratings) so this is pure
        // cleanup — no HTTP calls, no race window. Runs on BOTH paths
        // (migrate + plain signin) so a stale vb_skip_onboarding from an
        // earlier guest session doesn't strand a fresh signup.
        const flushGuestState = () => {
            [
                "vb_guest_ratings",
                "vb_guest_tags",
                "vb_guest_tags:v1",
                "vb_onboarding_progress",
                "vb_onboarding_movies",
                "vb_skip_onboarding",
            ].forEach((k) => localStorage.removeItem(k));
        };

        if (!isMigrate) {
            // After a plain sign-in: clear stale guest state and go to the
            // dashboard. The dashboard is the SINGLE source of onboarding
            // routing — it redirects 0-rating users to /onboarding. The old
            // in-page onboarding-chooser was unreachable anyway (the <SignIn>
            // forceRedirectUrl="/" navigates away before it could render).
            if (newUserCheckAttempted.current) return;
            newUserCheckAttempted.current = true;
            flushGuestState();
            push("/");
            return;
        }

        if (migrationAttempted.current) return;
        migrationAttempted.current = true;

        const migrateGuestData = async () => {
            setMigrating(true);
            try {
                // Promote anonymous session to registered user (transfers ratings + tag_preferences, deletes cookie)
                await api.post("/api/auth/claim-anonymous");
                flushGuestState();
                push("/?onboarding_complete=true");
            } catch (err) {
                console.error("Migration failed:", err);
                push("/");
            }
        };

        migrateGuestData();
    }, [isLoaded, isSignedIn, isMigrate, push]);

    if (!isLoaded) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-background">
                <span className="font-mono text-xs text-zinc-600">[ LOADING ]</span>
            </div>
        );
    }

    if (migrating) {
        return (
            <div className="min-h-screen flex flex-col items-center justify-center bg-background gap-4">
                <Loader2 className="size-8 text-primary animate-spin" />
                <p className="font-mono text-xs text-zinc-500 uppercase tracking-widest">
                    Migrating your ratings…
                </p>
            </div>
        );
    }

    // Already signed in on the plain-login path: the useEffect above is
    // pushing to "/". Render a redirect spinner rather than the now-dead
    // <SignIn/> (Clerk renders it blank once a session exists) so there's
    // never a blank flash while navigation completes.
    if (isSignedIn && !isMigrate) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-background">
                <Loader2 className="size-8 text-primary animate-spin" />
            </div>
        );
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-background relative overflow-hidden">
            <div className="absolute inset-0 bg-[url('/grid-pattern.svg')] opacity-10 pointer-events-none" />

            <div className="z-10 w-full max-w-2xl px-4">
                <AnimatePresence mode="wait">
                    {mode === "choose" ? (
                        <m.div
                            key="choose"
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20 }}
                            transition={{ duration: 0.3 }}
                            className="space-y-8 max-w-md mx-auto"
                        >
                            <div className="text-center space-y-2">
                                <h1 className="text-4xl md:text-5xl font-black tracking-tighter font-mono">
                                    VECTOR<span className="text-primary">BOX</span>
                                </h1>
                                <p className="text-zinc-500 font-mono text-[10px] uppercase tracking-[0.3em]">
                                    AI Movie Recommendations
                                </p>
                            </div>

                            <div className="space-y-3">
                                <button
                                    onClick={() => setMode("letterboxd")}
                                    className="w-full py-3.5 border border-border font-mono text-xs uppercase tracking-wider hover:border-primary hover:text-primary transition-all group"
                                >
                                    <span className="flex items-center justify-center gap-2">
                                        <span className="text-[10px] text-zinc-600 group-hover:text-primary transition-colors">●</span>
                                        I HAVE A LETTERBOXD ACCOUNT
                                    </span>
                                </button>

                                <button
                                    onClick={() => push("/onboarding/tags")}
                                    className="w-full py-3.5 bg-primary text-black font-bold font-mono text-xs uppercase tracking-wider hover:bg-primary/90 transition-colors glow-primary-hover"
                                >
                                    RATE FILMS TO GET STARTED
                                </button>
                            </div>

                            <p className="text-center text-[10px] font-mono text-zinc-700">
                                No account needed to start rating
                            </p>
                        </m.div>
                    ) : (
                        <m.div
                            key="letterboxd"
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20 }}
                            transition={{ duration: 0.3 }}
                            className="space-y-4 max-w-md mx-auto"
                        >
                            <button
                                onClick={() => setMode("choose")}
                                className="text-[10px] font-mono text-zinc-600 hover:text-zinc-400 transition-colors uppercase tracking-wider"
                            >
                                ← BACK
                            </button>

                            <SignIn
                                // Hash routing keeps every sub-step (email-code
                                // verification, OAuth/SSO callback, MFA) on THIS
                                // page via the URL hash. Path routing — Clerk's
                                // default — navigates to /login/factor-one etc.,
                                // which 404s here because this is NOT a catch-all
                                // route, producing a blank page stuck on /login.
                                routing="hash"
                                appearance={{
                                    elements: {
                                        rootBox: "font-mono",
                                        card: "bg-background border border-border",
                                        headerTitle: "text-primary font-mono",
                                        formButtonPrimary:
                                            "bg-primary text-background font-mono rounded-none",
                                    },
                                }}
                                fallbackRedirectUrl={redirectUrl}
                                forceRedirectUrl={redirectUrl}
                                signUpFallbackRedirectUrl={redirectUrl}
                                signUpForceRedirectUrl={redirectUrl}
                            />
                        </m.div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
}
export default function LoginPage() {
    return (
        <Suspense fallback={<div className="min-h-screen bg-zinc-950" />}>
            <LoginContent />
        </Suspense>
    );
}
