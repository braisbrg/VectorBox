"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { Loader2, ArrowRight, AlertCircle, Check, Globe } from "lucide-react";
import { register, login } from "@/lib/api";
import { COUNTRIES } from "@/lib/constants";
import { useLanguage } from "@/components/language-provider";
import { LanguageToggle } from "@/components/language-toggle";
import { ShimmerButton } from "@/components/tweak/shimmer-button";
import { GridPattern } from "@/components/tweak/grid-pattern";

export default function RegisterPage() {
    const router = useRouter();
    const { t } = useLanguage();
    const [username, setUsername] = useState("");
    const [pin, setPin] = useState("");
    const [confirmPin, setConfirmPin] = useState("");
    const [country, setCountry] = useState("ES");

    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState(false);

    const pinMatch = pin.length === 4 && pin === confirmPin;

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);

        if (!pinMatch) {
            setError("PINs do not match");
            return;
        }

        setLoading(true);

        try {
            // 1. Register
            await register(username, pin, country);
            setSuccess(true);

            // 2. Auto-login (Optional, but good UX)
            // Fix: Wrap in try/catch to prevent hang if rate limited
            try {
                const user = await login(username, pin);
                localStorage.setItem("vectorbox_user", JSON.stringify(user));
                document.cookie = `vectorbox_token=${user.token || "valid"}; path=/; max-age=86400; SameSite=Lax`;

                setTimeout(() => {
                    router.push("/");
                }, 1500);
            } catch (loginErr: any) {
                // If auto-login fails (e.g. Rate Limit 429), just redirect to login
                console.warn("Auto-login failed:", loginErr);
                setTimeout(() => {
                    // We can rely on the "Success" UI bubble to tell them Profile Created
                    // But we push them to login
                    router.push("/login");
                }, 2000);
            }

        } catch (err: any) {
            const msg = err.response?.data?.detail || "Registration failed. Username might be taken.";
            setError(msg);
            setLoading(false);
        }
    };

    return (
        <main className="min-h-screen bg-black text-white flex flex-col items-center justify-center p-4 relative overflow-hidden">
            {/* Background Effects with GridPattern */}
            <GridPattern className="opacity-15" strokeColor="rgba(128, 0, 255, 0.08)" />
            <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-purple-600/20 blur-[120px] rounded-full pointer-events-none" />

            {/* Language Toggle */}
            <div className="absolute top-4 right-4 z-50">
                <LanguageToggle />
            </div>

            <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="w-full max-w-md z-10"
            >
                {/* Header */}
                <div className="text-center mb-8">
                    <h1 className="text-4xl font-black tracking-tighter mb-2 font-mono">
                        INITIALIZE<span className="text-primary">_PROFILE</span>
                    </h1>
                    <p className="text-zinc-500 font-mono text-xs uppercase tracking-widest">
                        {t("auth.register_title")}
                    </p>
                </div>

                {success ? (
                    <motion.div
                        initial={{ scale: 0.9, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        className="bg-zinc-900 border border-green-500/50 p-8 rounded-xl text-center space-y-4"
                    >
                        <div className="w-16 h-16 bg-green-500/20 text-green-500 rounded-full flex items-center justify-center mx-auto mb-4">
                            <Check size={32} />
                        </div>
                        <h2 className="text-2xl font-bold text-white">Profile Created!</h2>
                        <p className="text-zinc-400">Redirecting to system...</p>
                        <Loader2 className="w-6 h-6 animate-spin mx-auto text-primary mt-4" />
                    </motion.div>
                ) : (
                    <div className="bg-zinc-900/50 backdrop-blur-md border border-zinc-800 p-8 rounded-xl shadow-2xl">
                        <form onSubmit={handleSubmit} className="space-y-6">
                            {error && (
                                <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-lg flex items-center gap-2 text-sm">
                                    <AlertCircle size={16} />
                                    {error}
                                </div>
                            )}

                            {/* Username */}
                            <div className="space-y-2">
                                <label className="text-xs uppercase tracking-wider text-zinc-500 font-bold">{t("auth.username")}</label>
                                <input
                                    type="text"
                                    value={username}
                                    onChange={(e) => setUsername(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
                                    placeholder={t("auth.placeholders.username")}
                                    className="w-full bg-black/50 border border-zinc-700 rounded-lg px-4 py-3 text-white placeholder-zinc-700 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all font-mono"
                                    required
                                    minLength={3}
                                    maxLength={20}
                                    pattern="^[a-zA-Z0-9_-]+$"
                                    inputMode="text"
                                    data-testid="register-username"
                                />
                                <p className="text-[10px] text-zinc-600">Lowercase, numbers, dashes only.</p>
                            </div>

                            {/* Country */}
                            <div className="space-y-2">
                                <label className="text-xs uppercase tracking-wider text-zinc-500 font-bold flex items-center gap-2">
                                    <Globe size={12} />
                                    Region
                                </label>
                                <select
                                    value={country}
                                    onChange={(e) => setCountry(e.target.value)}
                                    className="w-full bg-black/50 border border-zinc-700 rounded-lg px-4 py-3 text-white focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all font-mono appearance-none cursor-pointer"
                                >
                                    {COUNTRIES.map((c) => (
                                        <option key={c.code} value={c.code} className="bg-black">
                                            {c.name}
                                        </option>
                                    ))}
                                </select>
                            </div>

                            {/* PINs */}
                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <label className="text-xs uppercase tracking-wider text-zinc-500 font-bold">{t("auth.pin")}</label>
                                    <input
                                        type="password"
                                        value={pin}
                                        onChange={(e) => setPin(e.target.value.replace(/[^0-9]/g, "").slice(0, 4))}
                                        placeholder={t("auth.placeholders.pin")}
                                        className="w-full bg-black/50 border border-zinc-700 rounded-lg px-4 py-3 text-white text-center tracking-[0.3em] focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
                                        required
                                        inputMode="numeric"
                                        pattern="[0-9]{4}"
                                        maxLength={4}
                                        autoComplete="new-password"
                                        data-testid="register-pin"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-xs uppercase tracking-wider text-zinc-500 font-bold">Confirm</label>
                                    <input
                                        type="password"
                                        value={confirmPin}
                                        onChange={(e) => setConfirmPin(e.target.value.replace(/[^0-9]/g, "").slice(0, 4))}
                                        placeholder={t("auth.placeholders.pin")}
                                        className={`w-full bg-black/50 border rounded-lg px-4 py-3 text-white text-center tracking-[0.3em] focus:outline-none focus:ring-1 transition-all ${confirmPin.length === 4
                                            ? (pinMatch ? "border-green-500 focus:border-green-500 focus:ring-green-500" : "border-red-500 focus:border-red-500 focus:ring-red-500")
                                            : "border-zinc-700 focus:border-primary focus:ring-primary"
                                            }`}
                                        required
                                        inputMode="numeric"
                                        pattern="[0-9]{4}"
                                        maxLength={4}
                                        autoComplete="new-password"
                                        data-testid="register-confirm-pin"
                                    />
                                </div>
                            </div>

                            <ShimmerButton
                                type="submit"
                                disabled={loading || !username || !pinMatch}
                                className="w-full py-4 rounded-lg mt-4 !bg-purple-600 !border-purple-600 !text-white hover:!bg-purple-500"
                                data-testid="register-submit"
                            >
                                {loading ? (
                                    <Loader2 className="animate-spin" />
                                ) : (
                                    <>
                                        {t("auth.create_btn")}
                                        <ArrowRight className="w-4 h-4" />
                                    </>
                                )}
                            </ShimmerButton>
                        </form>
                    </div>
                )}

                {/* Footer Link */}
                <div className="text-center mt-8">
                    <p className="text-zinc-500 text-sm">
                        {t("app.welcome").replace(",", "")} ?{" "}
                        <Link href="/login" className="text-primary hover:underline font-bold">
                            {t("auth.back_to_login")}
                        </Link>
                    </p>
                </div>

                {/* Legal Links */}
                <div className="mt-8 text-center">
                    <p className="text-zinc-600 text-xs font-mono">
                        <Link href="/privacy" className="hover:text-[#CCFF00] transition-colors">
                            Privacy Policy
                        </Link>
                        <span className="mx-2">·</span>
                        <Link href="/terms" className="hover:text-[#CCFF00] transition-colors">
                            Terms of Service
                        </Link>
                    </p>
                </div>

            </motion.div>
        </main>
    );
}
