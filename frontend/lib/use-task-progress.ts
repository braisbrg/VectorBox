"use client";

// Task-progress polling — extracted from progress-modal.tsx so the import
// wizard's enrich step and the modal share one implementation.

import { useState, useEffect, useCallback, useRef } from "react";
import { getTaskStatus, TaskStatus } from "@/lib/api";

interface UseTaskProgressOpts {
    onComplete?: () => void;
    onError?: (error: string) => void;
}

export function useTaskProgress(taskId: string | null, { onComplete, onError }: UseTaskProgressOpts = {}) {
    const [status, setStatus] = useState<TaskStatus | null>(null);
    const [error, setError] = useState<string | null>(null);
    // Refs so the poll interval never re-binds on callback identity changes.
    const cbRef = useRef({ onComplete, onError });
    useEffect(() => {
        cbRef.current = { onComplete, onError };
    });

    const poll = useCallback(async () => {
        if (!taskId) return "stop" as const;
        try {
            const result = await getTaskStatus(taskId);
            setStatus(result);
            if (result.status === "completed") {
                // Slight delay for visual feedback before the completion callback.
                setTimeout(() => cbRef.current.onComplete?.(), 1000);
                return "stop" as const;
            }
            if (result.status === "failed") {
                const msg = result.step || "Task failed";
                setError(msg);
                cbRef.current.onError?.(msg);
                return "stop" as const;
            }
        } catch (err) {
            console.error("Error polling task status:", err);
            setError("Failed to get task status");
            return "stop" as const;
        }
        return "continue" as const;
    }, [taskId]);

    useEffect(() => {
        if (!taskId) {
            setStatus(null);
            setError(null);
            return;
        }
        let stopped = false;
        const tick = async () => {
            const verdict = await poll();
            if (verdict === "stop") stopped = true;
        };
        tick();
        const interval = setInterval(() => {
            if (stopped) {
                clearInterval(interval);
                return;
            }
            tick();
        }, 500);
        return () => clearInterval(interval);
    }, [taskId, poll]);

    return { status, error };
}
