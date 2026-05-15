"use client";
import { useClerk } from "@clerk/nextjs";
import { useCallback } from "react";
import { USER_SESSION_KEY } from "@/lib/api";

const CLIENT_STATE_KEYS = [
    USER_SESSION_KEY,
    "vectorbox_upload_task_id",
    "vectorbox_upload_user_id",
];

export function useVectorboxLogout() {
    const { signOut } = useClerk();
    return useCallback(async () => {
        try {
            await signOut();
        } catch {
            // signOut failures shouldn't strand the user; we still redirect.
        }
        for (const key of CLIENT_STATE_KEYS) {
            localStorage.removeItem(key);
        }
        window.location.href = "/login";
    }, [signOut]);
}
