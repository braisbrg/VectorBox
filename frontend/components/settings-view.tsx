"use client";

import Link from "next/link";
import { useTheme } from "next-themes";
import { Moon, Sun, Monitor } from "lucide-react";
import { useEffect, useState } from "react";
import { useLanguage } from "@/components/language-provider";
import { useSettings } from "@/lib/hooks";
import { Switch } from "@/components/ui/switch";

export function SettingsView() {
    const { theme: currentTheme, setTheme } = useTheme();
    const { t } = useLanguage();
    const { settings, updateSettings, mounted } = useSettings();

    if (!mounted) {
        return null;
    }

    return (
        <div className="max-w-2xl mx-auto p-6 bg-card border rounded-xl">
            <h2 className="text-2xl font-bold mb-4">{t("settings.title")}</h2>
            <p className="text-muted-foreground mb-6">{t("settings.subtitle")}</p>

            <div className="space-y-6">
                {/* Theme Selection */}
                <div className="p-4 border rounded-lg">
                    <div className="flex items-center justify-between mb-4">
                        <div>
                            <h3 className="font-medium">{t("settings.theme.title")}</h3>
                            <p className="text-sm text-muted-foreground">{t("settings.theme.subtitle")}</p>
                        </div>
                    </div>

                    <div className="grid grid-cols-3 gap-3">
                        <button
                            onClick={() => setTheme("light")}
                            className={`flex flex-col items-center gap-2 p-4 border-2 rounded-lg transition-all ${currentTheme === "light"
                                ? "border-primary bg-primary/10"
                                : "border-border hover:border-primary/50"
                                }`}
                        >
                            <Sun className="w-6 h-6" />
                            <span className="text-sm font-medium">{t("settings.theme.light")}</span>
                        </button>

                        <button
                            onClick={() => setTheme("dark")}
                            className={`flex flex-col items-center gap-2 p-4 border-2 rounded-lg transition-all ${currentTheme === "dark"
                                ? "border-primary bg-primary/10"
                                : "border-border hover:border-primary/50"
                                }`}
                        >
                            <Moon className="w-6 h-6" />
                            <span className="text-sm font-medium">{t("settings.theme.dark")}</span>
                        </button>

                        <button
                            onClick={() => setTheme("system")}
                            className={`flex flex-col items-center gap-2 p-4 border-2 rounded-lg transition-all ${currentTheme === "system"
                                ? "border-primary bg-primary/10"
                                : "border-border hover:border-primary/50"
                                }`}
                        >
                            <Monitor className="w-6 h-6" />
                            <span className="text-sm font-medium">{t("settings.theme.system")}</span>
                        </button>
                    </div>

                    <p className="text-xs text-muted-foreground mt-3">
                        {currentTheme === "system"
                            ? t("settings.theme.system_desc")
                            : t("settings.theme.current").replace("{theme}", currentTheme || "system")}
                    </p>
                </div>

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
