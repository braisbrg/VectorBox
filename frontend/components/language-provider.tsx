"use client";

import React, { createContext, useContext, useState, useEffect } from "react";
import { translations, Language } from "@/lib/i18n";

interface LanguageContextType {
    language: Language;
    setLanguage: (lang: Language) => void;
    t: (key: string) => string;
}

const LanguageContext = createContext<LanguageContextType | undefined>(undefined);

export function LanguageProvider({
    children,
    initialLanguage = "en",
}: {
    children: React.ReactNode;
    initialLanguage?: Language;
}) {
    // Initialised from the server-resolved locale (NEXT_LOCALE cookie set by the
    // middleware from Accept-Language), so SSR and the first client render agree.
    const [language, setLanguage] = useState<Language>(initialLanguage);

    // Reconcile with an explicit prior choice in localStorage: if the user had
    // picked a language before but the cookie was cleared/expired, that explicit
    // choice still wins over detection. Re-syncs the cookie so it sticks.
    useEffect(() => {
        const saved = localStorage.getItem("vectorbox_language") as Language | null;
        if ((saved === "en" || saved === "es") && saved !== language) {
            setLanguage(saved);
            document.cookie = `NEXT_LOCALE=${saved}; path=/; max-age=31536000; SameSite=Lax`;
        }
    }, []);

    const handleSetLanguage = (lang: Language) => {
        setLanguage(lang);
        localStorage.setItem("vectorbox_language", lang);
        // Set NEXT_LOCALE cookie for server-side persistence
        document.cookie = `NEXT_LOCALE=${lang}; path=/; max-age=31536000; SameSite=Lax`;
    };

    const t = (path: string) => {
        const keys = path.split(".");
        let current: any = translations[language];

        for (const key of keys) {
            if (current[key] === undefined) {
                console.warn(`Missing translation for key: ${path} in language: ${language}`);
                return path;
            }
            current = current[key];
        }

        return current as string;
    };

    return (
        <LanguageContext.Provider value={{ language, setLanguage: handleSetLanguage, t }}>
            {children}
        </LanguageContext.Provider>
    );
}

export function useLanguage() {
    const context = useContext(LanguageContext);
    if (context === undefined) {
        throw new Error("useLanguage must be used within a LanguageProvider");
    }
    return context;
}
