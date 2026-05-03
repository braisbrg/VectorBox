"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth, SignIn } from "@clerk/nextjs";
import { motion, AnimatePresence } from "framer-motion";
import { api } from "@/lib/api";
import { Loader2 } from "lucide-react";

export default function LoginPage() {
    const { isLoaded, isSignedIn } = useAuth();
    const router = useRouter();
    const searchParams = useSearchParams();
    const isMigrate = searchParams.get("migrate") === "true";
    const redirectUrl = isMigrate ? "/login?migrate=true" : "/";
    const [mode, setMode] = useState<"choose" | "letterboxd">(isMigrate ? "letterboxd" : "choose");
    const [migrating, setMigrating] = useState(false);
    const migrationAttempted = useRef(false);

    // After sign-in: migrate guest data (if ?migrate=true) or redirect to home
    useEffect(() => {
        if (!isLoaded || !isSignedIn) return;

        if (!isMigrate) {
            router.push("/");
            return;
        }

        if (migrationAttempted.current) return;
        migrationAttempted.current = true;

        const migrateGuestData = async () => {
            setMigrating(true);
            try {
                const ratingsRaw = localStorage.getItem("vb_guest_ratings");
                const tagsRaw = localStorage.getItem("vb_guest_tags");
                if (!ratingsRaw) {
                    router.push("/");
                    return;
                }

                await api.post("/api/onboarding/migrate-guest", {
                    ratings: JSON.parse(ratingsRaw),
                    tags: tagsRaw ? JSON.parse(tagsRaw) : { avoided: [] },
                });

                [
                    "vb_guest_ratings",
                    "vb_guest_tags",
                    "vb_onboarding_progress",
                    "vb_onboarding_movies",
                ].forEach((k) => localStorage.removeItem(k));
                router.push("/?onboarding_complete=true");
            } catch (err) {
                console.error("Migration failed:", err);
                router.push("/");
            }
        };

        migrateGuestData();
    }, [isLoaded, isSignedIn, searchParams, router]);

    if (!isLoaded) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-background">
                <span className="font-mono text-xs text-zinc-600">[ LOADING ]</span>
            </div>
        );
    }

    // Show migration spinner
    if (migrating) {
        return (
            <div className="min-h-screen flex flex-col items-center justify-center bg-background gap-4">
                <Loader2 className="w-8 h-8 text-primary animate-spin" />
                <p className="font-mono text-xs text-zinc-500 uppercase tracking-widest">
                    Migrating your ratings...
                </p>
            </div>
        );
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-background relative overflow-hidden">
            <div className="absolute inset-0 bg-[url('/grid-pattern.svg')] opacity-10 pointer-events-none" />

            <div className="z-10 w-full max-w-md px-4">
                <AnimatePresence mode="wait">
                    {mode === "choose" ? (
                        <motion.div
                            key="choose"
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20 }}
                            transition={{ duration: 0.3 }}
                            className="space-y-8"
                        >
                            {/* Logo */}
                            <div className="text-center space-y-2">
                                <h1 className="text-4xl md:text-5xl font-black tracking-tighter font-mono">
                                    VECTOR<span className="text-primary">BOX</span>
                                </h1>
                                <p className="text-zinc-500 font-mono text-[10px] uppercase tracking-[0.3em]">
                                    AI Movie Recommendations
                                </p>
                            </div>

                            {/* Chooser buttons */}
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
                                    onClick={() => router.push("/onboarding/tags")}
                                    className="w-full py-3.5 bg-primary text-black font-bold font-mono text-xs uppercase tracking-wider hover:bg-primary/90 transition-colors glow-primary-hover"
                                >
                                    RATE FILMS TO GET STARTED
                                </button>
                            </div>

                            <p className="text-center text-[10px] font-mono text-zinc-700">
                                No account needed to start rating
                            </p>
                        </motion.div>
                    ) : (
                        <motion.div
                            key="letterboxd"
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20 }}
                            transition={{ duration: 0.3 }}
                            className="space-y-4"
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
                        </motion.div>
                    )}
                </AnimatePresence>
            </div>
        </div>
    );
}
