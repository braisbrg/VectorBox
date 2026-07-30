import { useState, useEffect } from "react";

// eslint-disable-next-line @typescript-eslint/no-empty-object-type
export interface Settings {}

const DEFAULT_SETTINGS: Settings = {};

// Versioned key so a future Settings schema change can ignore old data
// instead of crashing on it. Bump the suffix when the shape changes.
const SETTINGS_STORAGE_KEY = "cinematch_settings:v1";

export function useSettings() {
    const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
        const stored = localStorage.getItem(SETTINGS_STORAGE_KEY);
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
        localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(updated));
    };

    return {
        settings,
        updateSettings,
        mounted
    };
}
