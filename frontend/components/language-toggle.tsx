"use client";

import { useLanguage } from "@/components/language-provider";



export function LanguageToggle({ isCollapsed }: { isCollapsed?: boolean }) {
    const { language, setLanguage } = useLanguage();

    return (
        <div className={`flex items-center border border-border-2 bg-bg-2 p-1 ${isCollapsed ? "w-full flex-col gap-1" : "gap-1"}`}>
            {(["en", "es"] as const).map((lng) => (
                <button
                    key={lng}
                    onClick={() => setLanguage(lng)}
                    className={`
                        font-mono text-xs font-bold uppercase tracking-wider transition-colors
                        ${isCollapsed ? "w-full py-2" : "px-3 py-1"}
                        ${language === lng
                            ? "bg-primary text-primary-ink"
                            : "text-fg-3 hover:bg-bg-3 hover:text-primary"
                        }
                    `}
                >
                    {lng}
                </button>
            ))}
        </div>
    );
}
