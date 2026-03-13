"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { useLanguage } from "@/components/language-provider";
import { useSettings } from "@/lib/hooks";
import { Switch } from "@/components/ui/switch";
import { syncRSS } from "@/lib/api";

export function SettingsView() {
    const { t } = useLanguage();
    const { settings, updateSettings, mounted } = useSettings();
    const [letterboxdUsername, setLetterboxdUsername] = useState<string | null>(null);
    const [syncMessage, setSyncMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

    useEffect(() => {
        try {
            const user = JSON.parse(localStorage.getItem("vectorbox_user") || "{}");
            setLetterboxdUsername(user?.letterboxd_username ?? null);
        } catch {
            setLetterboxdUsername(null);
        }
    }, []);

    const syncMutation = useMutation({
        mutationFn: (username: string) => syncRSS(username),
        onSuccess: () => {
            setSyncMessage({ type: "success", text: "Sync started — your feed will update shortly" });
        },
        onError: (error: Error) => {
            setSyncMessage({ type: "error", text: error.message || "Sync failed. Please try again." });
        },
    });

    if (!mounted) {
        return null;
    }

    return (
        <div className="max-w-2xl mx-auto p-6 bg-card border rounded-xl">
            <h2 className="text-2xl font-bold mb-4">{t("settings.title")}</h2>
            <p className="text-muted-foreground mb-6">{t("settings.subtitle")}</p>

            <div className="space-y-6">
                {/* Quality Settings */}
                <div className="p-4 border rounded-lg space-y-4">
                    <div className="flex items-center justify-between">
                        <div className="space-y-0.5">
                            <h3 className="font-medium text-zinc-200">{t("settings.show_low_quality")}</h3>
                            <p className="text-xs text-zinc-500">{t("settings.show_low_quality_desc")}</p>
                        </div>
                        <Switch
                            checked={settings.includeLowQuality}
                            onCheckedChange={(checked) => updateSettings({ includeLowQuality: checked })}
                        />
                    </div>
                </div>

                {/* Letterboxd Sync */}
                {letterboxdUsername && (
                    <div className="p-4 border border-primary/30 rounded-lg space-y-3">
                        <div className="flex items-center justify-between">
                            <div className="space-y-0.5">
                                <h3 className="font-medium text-zinc-200">Letterboxd Sync</h3>
                                <p className="text-xs text-zinc-500">
                                    Sync your latest ratings and watchlist from <span className="text-primary">{letterboxdUsername}</span>
                                </p>
                            </div>
                            <button
                                onClick={() => syncMutation.mutate(letterboxdUsername)}
                                disabled={syncMutation.isPending}
                                className="flex items-center gap-2 px-4 py-2 rounded-lg border border-primary/30 text-sm font-medium text-primary hover:bg-primary/10 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                {syncMutation.isPending ? (
                                    <Loader2 className="w-4 h-4 animate-spin" />
                                ) : (
                                    <RefreshCw className="w-4 h-4" />
                                )}
                                Sync Letterboxd Now
                            </button>
                        </div>

                        {syncMessage && (
                            <p className={`text-sm ${syncMessage.type === "success" ? "text-primary" : "text-red-500"}`}>
                                {syncMessage.text}
                            </p>
                        )}
                    </div>
                )}

                {/* About Section */}
                <div className="p-4 border rounded-lg bg-muted/30">
                    <h3 className="font-medium mb-2">{t("settings.about.title")}</h3>
                    <p className="text-sm text-muted-foreground">
                        {t("settings.about.desc")}
                    </p>
                    <p className="text-xs text-muted-foreground mt-2">
                        {t("settings.about.version")}
                    </p>
                </div>

                {/* Legal Links */}
                <div className="flex items-center justify-center gap-4 pt-2">
                    <Link
                        href="/privacy"
                        className="text-xs font-mono text-zinc-600 hover:text-[#CCFF00] transition-colors uppercase tracking-wider"
                    >
                        Privacy Policy
                    </Link>
                    <span className="text-zinc-700">·</span>
                    <Link
                        href="/terms"
                        className="text-xs font-mono text-zinc-600 hover:text-[#CCFF00] transition-colors uppercase tracking-wider"
                    >
                        Terms of Service
                    </Link>
                </div>
            </div>
        </div>
    );
}
