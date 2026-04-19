"use client";

import { useAuth } from "@clerk/nextjs";
import { useEffect } from "react";
import { api } from "@/lib/api";

/**
 * Attaches the Clerk session JWT as an Authorization Bearer header on every
 * axios request. Must be mounted inside <ClerkProvider>.
 */
export function AuthBridge() {
    const { getToken } = useAuth();

    useEffect(() => {
        const id = api.interceptors.request.use(async (config) => {
            try {
                const token = await getToken();
                if (token) {
                    config.headers.Authorization = `Bearer ${token}`;
                }
            } catch {
                // No active Clerk session — fall back to legacy cookie auth.
            }
            return config;
        });

        return () => api.interceptors.request.eject(id);
    }, [getToken]);

    return null;
}
