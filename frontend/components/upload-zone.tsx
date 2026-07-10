"use client";

import { useState, useCallback, useEffect } from "react";
import { Upload, FileText, Loader2, FileArchive, Plus, User as UserIcon, RefreshCw, Check, AlertCircle, Link as LinkIcon, Save, ArrowRight } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { useAuth } from "@clerk/nextjs";
import { uploadExportZIP, syncRSS, linkLetterboxd, VectorboxUser, USER_SESSION_KEY } from "@/lib/api";
import { m } from "framer-motion";
import { ProgressModal } from "./progress-modal";
import { useLanguage } from "@/components/language-provider";

interface UploadZoneProps {
    onUploadSuccess: (userId: number) => void;
    registeredUsers: VectorboxUser[];
    onUserCreated: (user: VectorboxUser) => void;
    activeSessionUserId: number | null;
    onSessionUserSelect?: (userId: number) => void;
}

function RSSSyncButton({ username, onSyncSuccess }: { username: string, onSyncSuccess: () => void }) {
    const [isLoading, setIsLoading] = useState(false);
    const [status, setStatus] = useState<"idle" | "success" | "error">("idle");
    const [syncTaskId, setSyncTaskId] = useState<string | null>(null);
    const { t } = useLanguage();

    const handleSync = async () => {
        if (!username) return;
        setIsLoading(true);
        setStatus("idle");
        try {
            const result = await syncRSS(username);
            if (result.task_id) {
                setSyncTaskId(result.task_id);
            } else {
                setStatus("success");
                setIsLoading(false);
                onSyncSuccess();
            }
        } catch (error) {
            console.error(error);
            setStatus("error");
            setIsLoading(false);
        }
    };

    const handleSyncComplete = () => {
        setSyncTaskId(null);
        setIsLoading(false);
        setStatus("success");
        onSyncSuccess();
    };

    const handleSyncError = () => {
        setSyncTaskId(null);
        setIsLoading(false);
        setStatus("error");
    };

    return (
        <>
            <ProgressModal
                taskId={syncTaskId}
                onComplete={handleSyncComplete}
                onError={handleSyncError}
            />
            <div className="flex flex-col items-center gap-4 w-full max-w-md mx-auto">
                <div className="flex items-center gap-2">
                    <button
                        onClick={handleSync}
                        disabled={isLoading}
                        className="px-4 py-2 bg-secondary text-secondary-foreground rounded-md text-sm font-medium hover:bg-secondary/80 disabled:opacity-50 flex items-center gap-2 transition-colors"
                    >
                        {isLoading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                        {t("rss.sync_btn")} {username}
                    </button>
                </div>

                {/* Info Box */}
                <div className="text-xs text-muted-foreground bg-muted/50 p-3 rounded border w-full text-center">
                    <p>
                        {t("rss.info")}
                    </p>
                </div>

                {status === "success" && (
                    <div className="text-xs text-green-500 flex items-center gap-1.5 bg-green-500/10 p-2 rounded animate-in fade-in slide-in-from-top-1">
                        <Check className="size-3" />
                        <span>{t("rss.synced")}</span>
                    </div>
                )}

                {status === "error" && (
                    <div className="text-xs text-destructive flex items-center gap-1.5 bg-destructive/10 p-2 rounded animate-in fade-in slide-in-from-top-1">
                        <AlertCircle className="size-3" />
                        <span>{t("rss.error")}</span>
                    </div>
                )}
            </div>
        </>
    );
}

export function UploadZone({ onUploadSuccess, registeredUsers, onUserCreated, activeSessionUserId, onSessionUserSelect }: UploadZoneProps) {
    const [isDragging, setIsDragging] = useState(false);
    const [file, setFile] = useState<File | null>(null);
    // Used by the "Skip for now" button to route correctly: signed-in users
    // go to dashboard `/`, guests go to `/explore` (the only public landing
    // for unauthenticated users — middleware redirects `/` to /login).
    const { isSignedIn } = useAuth();

    // Flattened State Logic
    // STRICT TYPE SAFETY: Use strict equality for ID lookup
    const activeUserProfile = registeredUsers.find(u => u.id === activeSessionUserId);

    // Explicit 2-Step Logic:
    // Step 1: Link (if no letterboxd_username)
    // Step 2: Upload (if linked)

    // We maintain a local check for isLinked to update UI optimistically
    const [localLinkedUser, setLocalLinkedUser] = useState<string | null>(null);
    const isLinked = !!(activeUserProfile?.letterboxd_username || localLinkedUser);

    const [linkUsername, setLinkUsername] = useState("");
    const [linkStep, setLinkStep] = useState<'input' | 'confirm'>('input');
    const [taskId, setTaskId] = useState<string | null>(null);
    const { t } = useLanguage();

    const linkMutation = useMutation({
        mutationFn: async ({ id, username }: { id: number, username: string }) => {
            return linkLetterboxd(id, username);
        },
        onSuccess: (data) => {
            // Optimistic Update
            setLocalLinkedUser(data.letterboxd_username);

            // Persist to LocalStorage
            if (typeof window !== "undefined") {
                const stored = localStorage.getItem(USER_SESSION_KEY);
                if (stored) {
                    try {
                        const user = JSON.parse(stored);
                        user.letterboxd_username = data.letterboxd_username;
                        localStorage.setItem(USER_SESSION_KEY, JSON.stringify(user));
                    } catch (e) {
                        console.error("LS Error", e);
                    }
                }
            }

            // UX: Slight delay before reload to let user see success
            setTimeout(() => {
                window.location.reload();
            }, 800);
        }
    });

    const uploadMutation = useMutation({
        mutationFn: (file: File) => {
            if (!activeSessionUserId) throw new Error("No user selected");
            return uploadExportZIP(file);
        },
        onSuccess: (data) => {
            if (data.task_id) {
                setTaskId(data.task_id);
                if (typeof window !== "undefined" && activeSessionUserId) {
                    localStorage.setItem("vectorbox_upload_task_id", data.task_id);
                    localStorage.setItem("vectorbox_upload_user_id", String(activeSessionUserId));
                }
            } else {
                if (activeSessionUserId) onUploadSuccess(activeSessionUserId);
            }
        },
    });

    // Recover in-flight upload after tab reload
    useEffect(() => {
        if (typeof window === "undefined" || !activeSessionUserId) return;
        const savedTaskId = localStorage.getItem("vectorbox_upload_task_id");
        const savedUserId = localStorage.getItem("vectorbox_upload_user_id");
        if (savedTaskId && savedUserId && Number(savedUserId) === activeSessionUserId) {
            setTaskId(savedTaskId);
        }
    }, [activeSessionUserId]);

    const clearUploadTaskStorage = () => {
        if (typeof window !== "undefined") {
            localStorage.removeItem("vectorbox_upload_task_id");
            localStorage.removeItem("vectorbox_upload_user_id");
        }
    };

    const handleProgressComplete = () => {
        setTaskId(null);
        clearUploadTaskStorage();
        if (activeSessionUserId) onUploadSuccess(activeSessionUserId);
    };

    const handleProgressError = (error: string) => {
        console.error("Upload error:", error);
        setTaskId(null);
        clearUploadTaskStorage();
    };

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        const droppedFile = e.dataTransfer.files[0];
        if (droppedFile && droppedFile.name.endsWith(".zip")) {
            setFile(droppedFile);
        }
    }, []);

    const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
        const selectedFile = e.target.files?.[0];
        if (selectedFile) {
            setFile(selectedFile);
        }
    };

    const handleUpload = () => {
        if (file) {
            uploadMutation.mutate(file);
        }
    };

    const handleLink = () => {
        if (activeSessionUserId && linkUsername) {
            linkMutation.mutate({ id: activeSessionUserId, username: linkUsername });
        }
    };

    return (
        <>
            <ProgressModal
                taskId={taskId}
                onComplete={handleProgressComplete}
                onError={handleProgressError}
            />

            <div className="space-y-6">

                {/* STEP 1: IDENTITY LINK */}
                {/* STEP 1: IDENTITY LINK */}
                {!isLinked && (
                    <m.div
                        initial={{ opacity: 0, scale: 0.98 }}
                        animate={{ opacity: 1, scale: 1 }}
                        className="relative space-y-6 border border-border-2 bg-bg-2 p-8 text-center shadow-acid"
                    >
                        {linkStep === 'input' ? (
                            <>
                                <div className="mx-auto mb-4 flex size-14 items-center justify-center border border-primary/40 text-primary">
                                    <LinkIcon size={26} />
                                </div>

                                <div className="space-y-2">
                                    <h3 className="font-display text-xl uppercase tracking-tight text-fg">{t("onboarding.link_title")}</h3>
                                    <p className="mx-auto max-w-sm font-mono text-sm text-fg-2">
                                        {t("onboarding.link_desc")}
                                    </p>
                                </div>

                                <div className="mx-auto flex max-w-xs flex-col gap-3">
                                    <div className="group relative">
                                        <UserIcon className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-3 transition-colors group-focus-within:text-primary" />
                                        <input
                                            type="text"
                                            value={linkUsername}
                                            onChange={(e) => setLinkUsername(e.target.value)}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter' && linkUsername.trim()) setLinkStep('confirm');
                                            }}
                                            placeholder="Letterboxd Username"
                                            className="w-full border border-border-2 bg-bg-3 py-3 pl-10 pr-4 font-mono text-fg placeholder:text-fg-3 focus:border-primary focus:outline-none"
                                        />
                                    </div>

                                    <button
                                        onClick={() => setLinkStep('confirm')}
                                        disabled={!linkUsername.trim()}
                                        className="flex w-full items-center justify-center gap-2 bg-primary py-3 font-display text-xs font-bold uppercase tracking-[0.08em] text-primary-ink disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                        {t("onboarding.btn_link")} <ArrowRight className="size-4" />
                                    </button>
                                </div>
                            </>
                        ) : (
                            <div className="space-y-6">
                                <div className="space-y-2">
                                    <h3 className="eyebrow text-fg-2">{t("onboarding.confirm_title")}</h3>
                                </div>

                                <div className="border border-primary/40 bg-bg py-6">
                                    <p className="break-all px-4 font-display text-4xl tracking-tight text-primary md:text-5xl">
                                        {linkUsername}
                                    </p>
                                </div>

                                <div className="flex items-start gap-3 border border-warn/40 bg-bg-3 p-4 text-left font-mono text-sm text-fg-2">
                                    <AlertCircle className="mt-0.5 size-5 shrink-0 text-warn" />
                                    <p>
                                        {t("onboarding.confirm_warning")}
                                        <br />
                                        <span className="mt-1 block text-xs opacity-70">{t("onboarding.confirm_warning_hint")}</span>
                                    </p>
                                </div>

                                <div className="mx-auto flex max-w-xs flex-col gap-3 pt-2">
                                    <button
                                        onClick={handleLink}
                                        disabled={linkMutation.isPending}
                                        className="flex w-full items-center justify-center gap-2 bg-primary py-3 font-display text-xs font-bold uppercase tracking-[0.08em] text-primary-ink shadow-acid-fg disabled:opacity-50"
                                    >
                                        {linkMutation.isPending ? (
                                            <Loader2 className="size-4 animate-spin" />
                                        ) : (
                                            <>
                                                {t("onboarding.confirm_yes")}
                                            </>
                                        )}
                                    </button>

                                    <button
                                        onClick={() => setLinkStep('input')}
                                        disabled={linkMutation.isPending}
                                        className="w-full py-2 font-mono text-sm text-fg-3 transition-colors hover:text-fg"
                                    >
                                        {t("onboarding.confirm_no")}
                                    </button>
                                </div>
                            </div>
                        )}

                        {linkMutation.isError && (
                            <m.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: "auto" }}
                                className="mt-4 flex items-center gap-2 border border-danger/40 bg-bg-3 p-3 font-mono text-sm text-danger"
                            >
                                <AlertCircle className="size-4 shrink-0" />
                                <span>
                                    {linkMutation.error instanceof Error ? linkMutation.error.message : "Failed to link account"}
                                </span>
                            </m.div>
                        )}
                    </m.div>
                )}


                {/* STEP 2: DATA INGESTION (Only if Linked) */}
                {isLinked && (
                    <m.div
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="space-y-6"
                    >
                        {/* Header for Step 2 */}
                        <div className="text-center space-y-2">
                            <h2 className="text-lg font-medium text-zinc-300">
                                Import History for <span className="text-primary font-bold">{activeUserProfile?.letterboxd_username || localLinkedUser}</span>
                            </h2>
                        </div>

                        <div
                            onDragOver={(e) => {
                                e.preventDefault();
                                setIsDragging(true);
                            }}
                            onDragLeave={() => setIsDragging(false)}
                            onDrop={handleDrop}
                            className={`
                                relative border-2 border-dashed p-12 text-center transition-colors cursor-pointer group
                                ${isDragging ? "border-primary bg-primary/5" : "border-border-2 bg-bg-2 hover:border-fg-3"}
                                ${uploadMutation.isPending || taskId ? "opacity-50 pointer-events-none" : ""}
                            `}
                        >
                            <input
                                type="file"
                                accept=".zip"
                                onChange={handleFileInput}
                                className="absolute inset-0 size-full opacity-0 cursor-pointer"
                            />

                            <div className="pointer-events-none space-y-4">
                                {file ? <FileArchive className="mx-auto size-10 text-primary" /> : <Upload className="mx-auto size-10 text-fg-3" />}

                                <div className="space-y-1">
                                    {file ? (
                                        <>
                                            <p className="break-all font-mono text-lg font-bold text-fg">{file.name}</p>
                                            <p className="font-mono text-sm text-primary">Ready to upload</p>
                                        </>
                                    ) : (
                                        <>
                                            <p className="font-mono text-lg font-bold text-fg-2 transition-colors group-hover:text-fg">{t("onboarding.upload_title")}</p>
                                            <p className="font-mono text-sm text-fg-3">{t("onboarding.upload_desc")}</p>
                                        </>
                                    )}
                                </div>
                            </div>
                        </div>

                        {file && (
                            <div className="flex justify-center">
                                <button
                                    onClick={handleUpload}
                                    disabled={uploadMutation.isPending}
                                    className="flex items-center gap-2 bg-primary px-8 py-3 font-display text-xs font-bold uppercase tracking-[0.08em] text-primary-ink shadow-acid-fg transition-transform hover:-translate-x-px hover:-translate-y-px"
                                >
                                    {uploadMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : null}
                                    {t("onboarding.btn_start")}
                                </button>
                            </div>
                        )}

                        {/* RSS Fallback / Sync */}
                        <div className="border-t border-border pt-8">
                            <RSSSyncButton
                                username={activeUserProfile?.letterboxd_username || localLinkedUser || ""}
                                onSyncSuccess={() => activeSessionUserId && onUploadSuccess(activeSessionUserId)}
                            />
                        </div>
                    </m.div>
                )}

                <div className="mt-8 text-center font-mono text-xs text-fg-3">
                    <p>
                        {t("onboarding.footer_export")}{" "}
                        <a
                            href="https://letterboxd.com/settings/data/"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-fg-2 underline transition-colors hover:text-primary"
                        >
                            Letterboxd Settings → Data
                        </a>
                    </p>
                </div>

                {/* Skip escape — visible only when user has no data (i.e. UploadZone
                    is rendered inside the ONBOARDING JAIL on dashboard.tsx). In
                    Settings / feed-empty-state callsites the user already has
                    ratings, so this button doesn't apply. Mirrors the Skip
                    in /onboarding header — same flag (vb_skip_onboarding) so
                    Dashboard suppresses both the JAIL and the sub-15 redirect. */}
                {!activeUserProfile?.has_data && (
                    <div className="mt-6 text-center">
                        <button
                            onClick={() => {
                                localStorage.setItem("vb_skip_onboarding", "true");
                                // Guests land on /explore (the only public-route
                                // home equivalent). Signed-in users land on the
                                // dashboard. Sending a guest to `/` here would
                                // bounce them through middleware → /login.
                                window.location.href = isSignedIn ? "/" : "/explore";
                            }}
                            className="font-mono text-xs uppercase tracking-wider text-fg-3 underline decoration-border-2 underline-offset-4 transition-colors hover:text-fg-2"
                        >
                            Skip for now — browse without imports
                        </button>
                    </div>
                )}
            </div>
        </>
    );
}
