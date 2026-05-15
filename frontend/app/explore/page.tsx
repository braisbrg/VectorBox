"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Sparkles } from "lucide-react";
import { FeedContainer } from "@/components/feed-container";
import { api } from "@/lib/api";

export default function ExplorePage() {
    const { push } = useRouter();
    const [userId, setUserId] = useState<number | null>(null);
    const [isReady, setIsReady] = useState(false);

    useEffect(() => {
        const initSession = async () => {
            try {
                const { data } = await api.post("/api/onboarding/init-session");
                setUserId(data.user_id);
            } catch (e) {
                console.error("Failed to init anonymous session:", e);
            } finally {
                setIsReady(true);
            }
        };
        initSession();
    }, []);

    return (
        <div className="min-h-screen bg-background text-foreground flex flex-col">
            {/* Top bar */}
            <header className="border-b border-border/50 px-4 py-3 shrink-0">
                <div className="max-w-[1600px] mx-auto flex items-center justify-between">
                    <Link href="/explore" className="text-lg font-black tracking-tighter font-mono uppercase">
                        VECTOR<span className="text-primary">BOX</span>
                    </Link>
                    <div className="flex gap-2">
                        <Link
                            href="/login"
                            className="border border-border text-zinc-400 px-3 py-1.5 font-mono text-xs uppercase hover:border-zinc-500 hover:text-zinc-300 transition-colors"
                        >
                            [ LOG IN ]
                        </Link>
                        <Link
                            href="/register"
                            className="border border-primary text-primary px-3 py-1.5 font-mono text-xs uppercase hover:bg-primary hover:text-background transition-colors"
                        >
                            [ SIGN UP ]
                        </Link>
                    </div>
                </div>
            </header>

            {/* Guest banner */}
            <div className="border-b border-border/50 bg-zinc-900/30 px-4 py-3 shrink-0">
                <div className="max-w-[1600px] mx-auto flex items-center justify-between gap-4 flex-wrap">
                    <div className="font-mono text-xs text-zinc-400">
                        <span className="text-primary mr-2">[ GUEST FEED ]</span>
                        Sign up to save your profile and unlock personalized recommendations.
                    </div>
                    <div className="flex gap-2 flex-wrap">
                        <button
                            onClick={() => push("/onboarding")}
                            className="border border-border text-zinc-400 px-3 py-1.5 font-mono text-xs
                                       hover:border-zinc-500 transition-colors"
                        >
                            [ RATE MORE FILMS ]
                        </button>
                        <Link
                            href="/login?migrate=true"
                            className="border border-primary text-primary px-3 py-1.5 font-mono text-xs uppercase hover:bg-primary hover:text-background transition-colors"
                        >
                            [ SAVE PROFILE ]
                        </Link>
                    </div>
                </div>
            </div>

            <main className="flex-1 max-w-[1600px] w-full mx-auto px-4 py-8">
                {!isReady ? (
                    <div className="flex items-center justify-center py-20">
                        <div className="text-center space-y-4">
                            <div className="size-8 border-2 border-primary border-t-transparent animate-spin mx-auto" />
                            <p className="font-mono text-xs text-zinc-600 uppercase tracking-widest">Initializing Session...</p>
                        </div>
                    </div>
                ) : userId ? (
                    <FeedContainer userId={userId} scope="global" />
                ) : (
                    <div className="text-center py-20 space-y-4">
                        <Sparkles className="size-12 text-primary mx-auto opacity-60" />
                        <p className="font-mono text-sm text-zinc-500">Could not initialize session.</p>
                    </div>
                )}
            </main>
        </div>
    );
}
