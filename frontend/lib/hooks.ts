import { useState, useEffect } from "react";

export interface Settings {
    includeLowQuality: boolean;
}

const DEFAULT_SETTINGS: Settings = {
    includeLowQuality: false,
};

export function useSettings() {
    const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
        const stored = localStorage.getItem("cinematch_settings");
        if (stored) {
            try {
                setSettings({ ...DEFAULT_SETTINGS, ...JSON.parse(stored) });
            } catch (e) {
                console.error("Failed to parse settings", e);
            }
        }
    }, []);

    const updateSettings = (newSettings: Partial<Settings>) => {
        const updated = { ...settings, ...newSettings };
        setSettings(updated);
        localStorage.setItem("cinematch_settings", JSON.stringify(updated));
    };

    return {
        settings,
        updateSettings,
        mounted
    };
}
