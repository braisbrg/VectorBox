"use client";

import { useState, useCallback, useEffect } from "react";
import { Upload, FileText, Loader2, FileArchive, Plus, User as UserIcon, RefreshCw, Check, AlertCircle, Link as LinkIcon, Save, ArrowRight } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { uploadExportZIP, syncRSS, linkLetterboxd, VectorboxUser } from "@/lib/api";
import { motion } from "framer-motion";
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
    const [stats, setStats] = useState<any>(null);
    const { t } = useLanguage();

    const handleSync = async () => {
        if (!username) return;
        setIsLoading(true);
        setStatus("idle");
        try {
            const result = await syncRSS(username);
            setStats(result.stats);
            setStatus("success");
            onSyncSuccess();
        } catch (error) {
            console.error(error);
            setStatus("error");
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <div className="flex flex-col items-center gap-4 w-full max-w-md mx-auto">
            <div className="flex items-center gap-2">
                <button
                    onClick={handleSync}
                    disabled={isLoading}
                    className="px-4 py-2 bg-secondary text-secondary-foreground rounded-md text-sm font-medium hover:bg-secondary/80 disabled:opacity-50 flex items-center gap-2 transition-colors"
                >
                    {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                    {t("rss.sync_btn")} {username}
                </button>
            </div>

            {/* Info Box */}
            <div className="text-xs text-muted-foreground bg-muted/50 p-3 rounded border w-full text-center">
                <p>
                    {t("rss.info")}
                </p>
            </div>

            {status === "success" && stats && (
                <div className="flex flex-col gap-1">
                    <div className="text-xs text-green-500 flex items-center gap-1.5 bg-green-500/10 p-2 rounded animate-in fade-in slide-in-from-top-1">
                        <Check className="w-3 h-3" />
                        <span>
                            {t("rss.synced")} {stats.rss_new_movies || 0} {t("rss.new")}, {stats.rss_new_ratings || 0} {t("rss.watched")}, {stats.rss_updated_ratings || 0} {t("rss.updated")}, {stats.watchlist_added || 0} {t("rss.watchlist")}
                        </span>
                    </div>
                    {stats.rss_errors > 0 && (
                        <div className="text-xs text-orange-500 flex items-center gap-1.5 bg-orange-500/10 p-2 rounded animate-in fade-in slide-in-from-top-1">
                            <AlertCircle className="w-3 h-3" />
                            <span>{stats.rss_errors} errors occurred (check console)</span>
                        </div>
                    )}
                </div>
            )}

            {status === "error" && (
                <div className="text-xs text-destructive flex items-center gap-1.5 bg-destructive/10 p-2 rounded animate-in fade-in slide-in-from-top-1">
                    <AlertCircle className="w-3 h-3" />
                    <span>{t("rss.error")}</span>
                </div>
            )}
        </div>
    );
}

export function UploadZone({ onUploadSuccess, registeredUsers, onUserCreated, activeSessionUserId, onSessionUserSelect }: UploadZoneProps) {
    const [isDragging, setIsDragging] = useState(false);
    const [file, setFile] = useState<File | null>(null);

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
                const stored = localStorage.getItem("vectorbox_user");
                if (stored) {
                    try {
                        const user = JSON.parse(stored);
                        user.letterboxd_username = data.letterboxd_username;
                        localStorage.setItem("vectorbox_user", JSON.stringify(user));
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
            } else {
                if (activeSessionUserId) onUploadSuccess(activeSessionUserId);
            }
        },
    });

    const handleProgressComplete = () => {
        setTaskId(null);
        if (activeSessionUserId) onUploadSuccess(activeSessionUserId);
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
                onError={(error) => console.error("Upload error:", error)}
            />

            <div className="space-y-6">

                {/* STEP 1: IDENTITY LINK */}
                {/* STEP 1: IDENTITY LINK */}
                {!isLinked && (
                    <motion.div
                        initial={{ opacity: 0, scale: 0.95 }}
                        animate={{ opacity: 1, scale: 1 }}
                        className="bg-zinc-900 border border-yellow-500/30 rounded-xl p-8 text-center space-y-6 shadow-[0_0_20px_rgba(234,179,8,0.1)] relative overflow-hidden"
                    >
                        {/* Acid Design Background Element */}
                        <div className="absolute top-0 right-0 w-32 h-32 bg-yellow-500/5 rounded-full blur-3xl -translate-y-1/2 translate-x-1/2 pointer-events-none" />

                        {linkStep === 'input' ? (
                            <>
                                <div className="w-16 h-16 bg-yellow-500/20 text-yellow-500 rounded-full flex items-center justify-center mx-auto mb-4 border border-yellow-500/20">
                                    <LinkIcon size={32} />
                                </div>

                                <div className="space-y-2">
                                    <h3 className="text-xl font-bold text-white tracking-tight">{t("onboarding.link_title")}</h3>
                                    <p className="text-zinc-400 text-sm max-w-sm mx-auto">
                                        {t("onboarding.link_desc")}
                                    </p>
                                </div>

                                <div className="flex flex-col gap-3 max-w-xs mx-auto">
                                    <div className="relative group">
                                        <UserIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500 group-focus-within:text-yellow-500 transition-colors" />
                                        <input
                                            type="text"
                                            value={linkUsername}
                                            onChange={(e) => setLinkUsername(e.target.value)}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter' && linkUsername.trim()) setLinkStep('confirm');
                                            }}
                                            placeholder="Letterboxd Username"
                                            className="w-full bg-black/50 border border-zinc-700 rounded-md pl-10 pr-4 py-3 text-white placeholder-zinc-600 focus:outline-none focus:border-yellow-500 focus:ring-1 focus:ring-yellow-500 transition-all font-mono"
                                            autoFocus
                                        />
                                    </div>

                                    <button
                                        onClick={() => setLinkStep('confirm')}
                                        disabled={!linkUsername.trim()}
                                        className="w-full bg-yellow-600 text-white font-bold py-3 rounded-md hover:bg-yellow-500 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                                    >
                                        {t("onboarding.btn_link")} <ArrowRight className="w-4 h-4" />
                                    </button>
                                </div>
                            </>
                        ) : (
                            <div className="space-y-6 animate-in zoom-in-95 duration-200">
                                <div className="space-y-2">
                                    <h3 className="text-lg font-bold text-zinc-100 uppercase tracking-widest text-xs">{t("onboarding.confirm_title")}</h3>
                                </div>

                                <div className="py-6 bg-black/40 rounded-lg border border-yellow-500/20">
                                    <p className="text-4xl md:text-5xl font-black text-transparent bg-clip-text bg-gradient-to-r from-yellow-400 to-yellow-200 font-mono tracking-tighter break-all px-4">
                                        {linkUsername}
                                    </p>
                                </div>

                                <div className="bg-yellow-500/10 border border-yellow-500/20 p-4 rounded-md text-sm text-yellow-200/80 flex items-start gap-3 text-left">
                                    <AlertCircle className="w-5 h-5 shrink-0 text-yellow-500 mt-0.5" />
                                    <p>
                                        {t("onboarding.confirm_warning")}
                                        <br />
                                        <span className="text-xs opacity-70 mt-1 block">{t("onboarding.confirm_warning_hint")}</span>
                                    </p>
                                </div>

                                <div className="flex flex-col gap-3 max-w-xs mx-auto pt-2">
                                    <button
                                        onClick={handleLink}
                                        disabled={linkMutation.isPending}
                                        className="w-full bg-yellow-500 text-black font-black uppercase tracking-wide py-3 rounded-md hover:bg-yellow-400 hover:scale-[1.02] transition-all disabled:opacity-50 flex items-center justify-center gap-2 shadow-lg shadow-yellow-500/20"
                                    >
                                        {linkMutation.isPending ? (
                                            <Loader2 className="w-4 h-4 animate-spin" />
                                        ) : (
                                            <>
                                                {t("onboarding.confirm_yes")}
                                            </>
                                        )}
                                    </button>

                                    <button
                                        onClick={() => setLinkStep('input')}
                                        disabled={linkMutation.isPending}
                                        className="w-full text-zinc-500 hover:text-white text-sm font-medium py-2 transition-colors"
                                    >
                                        {t("onboarding.confirm_no")}
                                    </button>
                                </div>
                            </div>
                        )}

                        {linkMutation.isError && (
                            <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: "auto" }}
                                className="bg-red-500/10 border border-red-500/20 text-red-500 p-3 rounded-md text-sm flex items-center gap-2 mt-4"
                            >
                                <AlertCircle className="w-4 h-4 shrink-0" />
                                <span>
                                    {linkMutation.error instanceof Error ? linkMutation.error.message : "Failed to link account"}
                                </span>
                            </motion.div>
                        )}
                    </motion.div>
                )}


                {/* STEP 2: DATA INGESTION (Only if Linked) */}
                {isLinked && (
                    <motion.div
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
                                relative border-2 border-dashed rounded-xl p-12 text-center transition-all cursor-pointer group
                                ${isDragging ? "border-primary bg-primary/10 scale-[1.02]" : "border-zinc-800 bg-zinc-900/50 hover:border-zinc-700"}
                                ${uploadMutation.isPending || taskId ? "opacity-50 pointer-events-none" : ""}
                            `}
                        >
                            <input
                                type="file"
                                accept=".zip"
                                onChange={handleFileInput}
                                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                            />

                            <div className="pointer-events-none space-y-4">
                                <div className="w-16 h-16 mx-auto bg-zinc-800 rounded-full flex items-center justify-center group-hover:bg-zinc-700 transition-colors">
                                    {file ? <FileArchive className="w-8 h-8 text-primary" /> : <Upload className="w-8 h-8 text-zinc-500 group-hover:text-zinc-300" />}
                                </div>

                                <div className="space-y-1">
                                    {file ? (
                                        <>
                                            <p className="text-lg font-bold text-white break-all">{file.name}</p>
                                            <p className="text-sm text-primary">Ready to upload</p>
                                        </>
                                    ) : (
                                        <>
                                            <p className="text-lg font-bold text-zinc-300 group-hover:text-white transition-colors">{t("onboarding.upload_title")}</p>
                                            <p className="text-sm text-zinc-500">{t("onboarding.upload_desc")}</p>
                                        </>
                                    )}
                                </div>
                            </div>
                        </div>

                        {file && (
                            <div className="flex justify-center animate-in slide-in-from-bottom-2">
                                <button
                                    onClick={handleUpload}
                                    disabled={uploadMutation.isPending}
                                    className="bg-primary text-black font-bold uppercase tracking-wide px-8 py-3 rounded-full hover:bg-primary/90 transition-transform hover:scale-105 shadow-lg shadow-primary/20 flex items-center gap-2"
                                >
                                    {uploadMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                                    {t("onboarding.btn_start")}
                                </button>
                            </div>
                        )}

                        {/* RSS Fallback / Sync */}
                        <div className="pt-8 border-t border-zinc-900/50">
                            <RSSSyncButton
                                username={activeUserProfile?.letterboxd_username || localLinkedUser || ""}
                                onSyncSuccess={() => activeSessionUserId && onUploadSuccess(activeSessionUserId)}
                            />
                        </div>
                    </motion.div>
                )}

                <div className="mt-8 text-center text-xs text-zinc-600">
                    <p>
                        {t("onboarding.footer_export")}{" "}
                        <a
                            href="https://letterboxd.com/settings/data/"
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-zinc-400 hover:text-primary underline transition-colors"
                        >
                            Letterboxd Settings → Data
                        </a>
                    </p>
                </div>
            </div>
        </>
    );
}
