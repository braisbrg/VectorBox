"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { Loader2, ArrowRight, AlertCircle } from "lucide-react";
import { login } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";
import { LanguageToggle } from "@/components/language-toggle";
import { ShimmerButton } from "@/components/tweak/shimmer-button";
import { GridPattern } from "@/components/tweak/grid-pattern";

export default function LoginPage() {
    const router = useRouter();
    const { t } = useLanguage();
    const [username, setUsername] = useState("");
    const [pin, setPin] = useState("");
    const [cooldown, setCooldown] = useState(0);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);
        setLoading(true);

        try {
            const user = await login(username, pin);

            // 1. Save user to localStorage for Client Components
            localStorage.setItem("vectorbox_user", JSON.stringify(user));

            // 2. Set Cookie for Server Components (Gatekeeper)
            document.cookie = `vectorbox_token=${user.token || "valid"}; path=/; max-age=86400; SameSite=Lax`;

            // Redirect to home
            router.push("/");
        } catch (err: any) {
            const status = err.response?.status;
            const data = err.response?.data;

            // PRIORITY 1: Rate Limit (429)
            // Handle specific slowapi error or standard 429
            const isRateLimit = status === 429 ||
                (data?.error && typeof data.error === "string" && data.error.includes("Rate limit"));

            if (isRateLimit) {
                // Extract wait time
                const retryAfter = err.response?.headers?.["retry-after"]
                    ? parseInt(err.response.headers["retry-after"])
                    : 60;

                const msg = t("auth.errors.rate_limit").replace("{seconds}", retryAfter.toString());
                setError(msg);
                setCooldown(retryAfter);

                // Start Countdown
                const interval = setInterval(() => {
                    setCooldown((prev) => {
                        if (prev <= 1) {
                            clearInterval(interval);
                            return 0;
                        }
                        return prev - 1;
                    });
                }, 1000);
                return;
            }

            // PRIORITY 2: Validation (422)
            if (status === 422) {
                let msg = t("auth.errors.invalid_input");
                let rawMsg = "";

                if (Array.isArray(data?.errors)) {
                    // Custom Backend Format: { errors: [{ field: "username", message: "too short" }] }
                    rawMsg = data.errors.map((e: any) => `${e.field}: ${e.message}`).join(" | ");
                } else if (Array.isArray(data?.detail)) {
                    // Standard FastAPI: { detail: [{ loc: ["body", "username"], msg: "..." }] }
                    rawMsg = data.detail.map((e: any) => e.msg).join(" | ");
                } else if (typeof data?.detail === "string") {
                    rawMsg = data.detail;
                }

                // Translate specific validation errors
                // "String should have at least 3 characters" -> "El usuario debe tener al menos 3 caracteres"
                if (rawMsg.includes("String should have at least 3 characters")) {
                    msg = t("auth.errors.username_short");
                } else if (rawMsg) {
                    msg = rawMsg;
                }

                setError(msg);
                setPin("");
                return;
            }

            // PRIORITY 3: Auth Failure (401)
            if (status === 401) {
                setError(t("auth.errors.invalid_credentials"));
                setPin("");
                return;
            }

            // Fallback
            setError(data?.detail || data?.error || t("auth.errors.generic"));
            setPin("");
        } finally {
            setLoading(false);
        }
    };

    return (
        <main className="min-h-screen bg-black text-white flex flex-col items-center justify-center p-4 relative overflow-hidden">
            {/* Ambient Background with GridPattern */}
            <GridPattern className="opacity-10" strokeColor="rgba(204, 255, 0, 0.08)" />
            <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-primary/20 blur-[150px] rounded-full pointer-events-none" />

            {/* Language Toggle */}
            <div className="absolute top-4 right-4 z-50">
                <LanguageToggle />
            </div>

            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                className="w-full max-w-md z-10"
            >
                {/* Logo */}
                <div className="text-center mb-8">
                    <h1 className="text-5xl font-black tracking-tighter mb-2 font-mono">
                        VECTOR<span className="text-primary">BOX</span>
                    </h1>
                    <p className="text-zinc-500 font-mono text-sm uppercase tracking-widest">
                        v1.2 {t("auth.welcome")}
                    </p>
                </div>

                {/* Login Form card */}
                <div className="bg-zinc-900/50 backdrop-blur-md border border-zinc-800 p-8 rounded-xl shadow-2xl">
                    <form onSubmit={handleSubmit} className="space-y-6">
                        {error && (
                            <motion.div
                                initial={{ x: 0 }}
                                animate={{ x: [-10, 10, -10, 10, 0] }}
                                transition={{ duration: 0.4 }}
                                className="bg-red-900/40 border border-red-500/50 text-red-200 p-4 rounded-lg flex items-center gap-3 text-sm font-medium shadow-[0_0_15px_rgba(239,68,68,0.2)]"
                            >
                                <AlertCircle size={20} className="text-red-400 shrink-0" />
                                <span>{error}</span>
                            </motion.div>
                        )}

                        <div className="space-y-2">
                            <label className="text-xs uppercase tracking-wider text-zinc-500 font-bold">{t("auth.username")}</label>
                            <input
                                type="text"
                                value={username}
                                onChange={(e) => setUsername(e.target.value.toLowerCase())}
                                placeholder={t("auth.placeholders.username")}
                                className="w-full bg-black/50 border border-zinc-700 rounded-lg px-4 py-3 text-white placeholder-zinc-700 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all font-mono disabled:opacity-50 disabled:cursor-not-allowed"
                                required
                                autoFocus
                                disabled={cooldown > 0}
                            />
                        </div>

                        <div className="space-y-2">
                            <label className="text-xs uppercase tracking-wider text-zinc-500 font-bold">{t("auth.pin")}</label>
                            <input
                                type="password"
                                value={pin}
                                onChange={(e) => {
                                    // Only allow numbers, max 4 chars
                                    const val = e.target.value.replace(/[^0-9]/g, "").slice(0, 4);
                                    setPin(val);
                                }}
                                placeholder={t("auth.placeholders.pin")}
                                className="w-full bg-black/50 border border-zinc-700 rounded-lg px-4 py-3 text-white placeholder-zinc-700 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all font-mono tracking-[0.5em] text-center text-lg disabled:opacity-50 disabled:cursor-not-allowed"
                                required
                                inputMode="numeric"
                                pattern="[0-9]{4}"
                                maxLength={4}
                                autoComplete="current-password"
                                disabled={cooldown > 0}
                            />
                        </div>

                        <ShimmerButton
                            type="submit"
                            disabled={loading || !username || pin.length < 4 || cooldown > 0}
                            className={`w-full py-4 rounded-lg flex items-center justify-center gap-2 group shadow-[0_0_30px_-5px_rgba(204,255,0,0.4)] ${cooldown > 0 ? "!bg-red-900/50 !text-red-200 !border-red-500 !shadow-[0_0_20px_-5px_rgba(239,68,68,0.4)]" : ""
                                }`}
                        >
                            {loading ? (
                                <Loader2 className="animate-spin" />
                            ) : cooldown > 0 ? (
                                <span>{t("auth.try_again_in").replace("{seconds}", cooldown.toString())}</span>
                            ) : (
                                <>
                                    {t("auth.login_btn")}
                                    <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
                                </>
                            )}
                        </ShimmerButton>
                    </form>
                </div>

                {/* Footer Link */}
                <div className="text-center mt-8">
                    <p className="text-zinc-500 text-sm">
                        {t("auth.new_user")}{" "}
                        <Link href="/register" className="text-primary hover:underline font-bold">
                            {t("auth.register_link")}
                        </Link>
                    </p>
                </div>
            </motion.div>
        </main>
    );
}
