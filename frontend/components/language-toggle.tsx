"use client";

import { useLanguage } from "@/components/language-provider";



export function LanguageToggle({ isCollapsed }: { isCollapsed?: boolean }) {
    const { language, setLanguage } = useLanguage();

    return (
        <div className={`flex items-center bg-zinc-900 border border-zinc-800 rounded-none p-1 ${isCollapsed ? "flex-col gap-1 w-full" : "gap-1"}`}>
            <button
                onClick={() => setLanguage("en")}
                className={`
                    text-xs font-bold uppercase tracking-wider rounded-none transition-all font-mono
                    ${isCollapsed ? "w-full py-2" : "px-3 py-1"}
                    ${language === "en"
                        ? "bg-primary text-black shadow-[0_0_10px_rgba(204,255,0,0.3)]"
                        : "text-zinc-500 hover:text-primary hover:bg-zinc-800"
                    }
                `}
            >
                EN
            </button>
            <button
                onClick={() => setLanguage("es")}
                className={`
                    text-xs font-bold uppercase tracking-wider rounded-none transition-all font-mono
                    ${isCollapsed ? "w-full py-2" : "px-3 py-1"}
                    ${language === "es"
                        ? "bg-primary text-black shadow-[0_0_10px_rgba(204,255,0,0.3)]"
                        : "text-zinc-500 hover:text-primary hover:bg-zinc-800"
                    }
                `}
            >
                ES
            </button>
        </div>
    );
}
