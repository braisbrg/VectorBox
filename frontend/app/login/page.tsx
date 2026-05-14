"use client";

import { Suspense, useState, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth, SignIn } from "@clerk/nextjs";
import { m, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";
import { Loader2 } from "lucide-react";

const ONBOARDING_PATHS = [
    {
        id: "letterboxd",
        title: "SYNC LETTERBOXD",
        description: "Upload your full watch history. Best for existing Letterboxd users with years of ratings.",
        cta: "[ UPLOAD ZIP ]",
        href: "/?upload=true",
    },
    {
        id: "rate",
        title: "RATE FILMS",
        description: "Rate films one by one to build your taste profile. Best if you're new to tracking.",
        cta: "[ START RATING ]",
        href: "/onboarding",
    },
    {
        id: "rss",
        title: "CONNECT RSS",
        description: "Auto-sync your Letterboxd diary. Requires a Letterboxd account with public RSS.",
        cta: "[ CONNECT RSS ]",
        href: "/?rss=true",
    },
];

function LoginContent() {
    const { isLoaded, isSignedIn } = useAuth();
    const { push } = useRouter();
    const searchParams = useSearchParams();
    const isMigrate = searchParams.get("migrate") === "true";
    const redirectUrl = isMigrate ? "/login?migrate=true" : "/";
    const [mode, setMode] = useState<"choose" | "letterboxd" | "onboarding-chooser">(
        isMigrate ? "letterboxd" : "choose"
    );
    const [migrating, setMigrating] = useState(false);
    const migrationAttempted = useRef(false);
    const newUserCheckAttempted = useRef(false);

    // Fix 1 removed: no longer use local storage for guest rating checks.

    // After sign-in: migrate guest data or show onboarding chooser for new users
    useEffect(() => {
        if (!isLoaded || !isSignedIn) return;

        if (!isMigrate) {
            // FIX 4: Check if new user (0 ratings) → show onboarding chooser
            if (newUserCheckAttempted.current) return;
            newUserCheckAttempted.current = true;

            api.get("/api/onboarding/status")
                .then(({ data }) => {
                    if (data.ratings_count === 0) {
                        setMode("onboarding-chooser");
                    } else {
                        push("/");
                    }
                })
                .catch(() => push("/")); // TODO: handle 401 fallback more gracefully
            return;
        }

        if (migrationAttempted.current) return;
        migrationAttempted.current = true;

        const migrateGuestData = async () => {
            setMigrating(true);
            try {
                // Promote anonymous session to registered user (transfers ratings, deletes cookie)
                await api.post("/api/auth/claim-anonymous");

                // Clean up legacy localStorage if any exists
                [
                    "vb_guest_ratings",
                    "vb_guest_tags",
                    "vb_onboarding_progress",
                    "vb_onboarding_movies",
                ].forEach((k) => localStorage.removeItem(k));
                
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

    return (
        <div className="min-h-screen flex items-center justify-center bg-background relative overflow-hidden">
            <div className="absolute inset-0 bg-[url('/grid-pattern.svg')] opacity-10 pointer-events-none" />

            <div className="z-10 w-full max-w-2xl px-4">
                <AnimatePresence mode="wait">
                    {mode === "onboarding-chooser" ? (
                        <m.div
                            key="onboarding-chooser"
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20 }}
                            transition={{ duration: 0.3 }}
                            className="space-y-8"
                        >
                            <div className="text-center space-y-2">
                                <h1 className="text-4xl md:text-5xl font-black tracking-tighter font-mono">
                                    VECTOR<span className="text-primary">BOX</span>
                                </h1>
                                <p className="text-zinc-500 font-mono text-[10px] uppercase tracking-[0.3em]">
                                    How do you want to get started?
                                </p>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                {ONBOARDING_PATHS.map((path) => (
                                    <button
                                        key={path.id}
                                        onClick={() => push(path.href)}
                                        className="flex flex-col gap-4 p-5 border border-border text-left
                                                   hover:border-primary hover:bg-primary/5 transition-all group"
                                    >
                                        <div className="space-y-2">
                                            <p className="font-mono text-xs font-bold uppercase tracking-wider
                                                          text-foreground group-hover:text-primary transition-colors">
                                                {path.title}
                                            </p>
                                            <p className="font-mono text-[10px] text-zinc-500 leading-relaxed">
                                                {path.description}
                                            </p>
                                        </div>
                                        <span className="font-mono text-xs text-primary mt-auto">
                                            {path.cta}
                                        </span>
                                    </button>
                                ))}
                            </div>
                        </m.div>
                    ) : mode === "choose" ? (
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
