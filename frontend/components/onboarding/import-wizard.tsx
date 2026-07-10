"use client";

// Authed 5-step import wizard — handoff ONB_STEPS: identify → import → taste →
// enrich → ready. Replaces the old "jail" UploadZone card and powers /import.
// Reuses the exact mutations/LS behavior of upload-zone (linkLetterboxd,
// uploadExportZIP + vectorbox_upload_task_id recovery, syncRSS) and the shared
// useTaskProgress polling. UploadZone itself remains for settings re-upload.

import { useState, useEffect, useMemo } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { m } from "framer-motion";
import { Loader2, Upload, FileArchive } from "lucide-react";
import {
    api,
    linkLetterboxd,
    uploadExportZIP,
    syncRSS,
    getSpace,
    USER_SESSION_KEY,
    UserSession,
} from "@/lib/api";
import { useTaskProgress } from "@/lib/use-task-progress";
import { BracketToggle } from "@/components/ui/bracket-toggle";
import {
    TagSelector,
    TagState,
    tagStateToPreferences,
    preferencesToTagState,
} from "@/components/onboarding/tag-selector";
import { cn } from "@/lib/utils";
import { useLanguage } from "@/components/language-provider";

const STEPS = ["identify", "import", "taste", "enrich", "ready"] as const;
type Step = (typeof STEPS)[number];

interface ImportWizardProps {
    session: UserSession;
    /** Where to enter: settings "re-link" → "identify", "re-upload" → "import". */
    initialStep?: Step;
    onComplete: () => void;
    /** Jail mount shows the skip escape; /import doesn't need it. */
    onSkip?: () => void;
}

function Stepper({ current }: { current: Step }) {
    const { t } = useLanguage();
    const idx = STEPS.indexOf(current);
    return (
        <div className="flex flex-wrap items-center gap-1.5">
            {STEPS.map((s, i) => (
                <div
                    key={s}
                    className={cn(
                        "flex items-center gap-1.5 border px-2.5 py-1 font-display text-[10px] uppercase tracking-[0.1em]",
                        i === idx
                            ? "border-primary bg-primary text-primary-ink"
                            : i < idx
                              ? "border-border-2 bg-bg-2 text-primary"
                              : "border-border-2 text-fg-3"
                    )}
                >
                    <span>{i < idx ? "✓" : String(i + 1).padStart(2, "0")}</span>
                    <span className="hidden sm:inline">{t(`wiz.step_${s}`)}</span>
                </div>
            ))}
        </div>
    );
}

export function ImportWizard({ session, initialStep, onComplete, onSkip }: ImportWizardProps) {
    const { t } = useLanguage();
    const alreadyLinked = !!session.letterboxd_username;
    const [step, setStep] = useState<Step>(initialStep ?? (alreadyLinked ? "import" : "identify"));

    // ---- step 1 · identify ----
    const [handle, setHandle] = useState("");
    const [confirming, setConfirming] = useState(false);
    const [linkedHandle, setLinkedHandle] = useState<string | null>(session.letterboxd_username ?? null);

    const linkMutation = useMutation({
        mutationFn: ({ id, username }: { id: number; username: string }) => linkLetterboxd(id, username),
        onSuccess: (data) => {
            setLinkedHandle(data.letterboxd_username);
            // Same LS persistence as upload-zone so the shell session stays coherent.
            try {
                const stored = localStorage.getItem(USER_SESSION_KEY);
                if (stored) {
                    const user = JSON.parse(stored);
                    user.letterboxd_username = data.letterboxd_username;
                    localStorage.setItem(USER_SESSION_KEY, JSON.stringify(user));
                }
            } catch (e) {
                console.error("LS Error", e);
            }
            setStep("import");
        },
    });

    // ---- step 2 · import ----
    const [isDragging, setIsDragging] = useState(false);
    const [file, setFile] = useState<File | null>(null);
    const [rssAutoSync, setRssAutoSync] = useState(true);
    const [taskId, setTaskId] = useState<string | null>(null);
    const [importKind, setImportKind] = useState<"zip" | "rss" | null>(null);

    const uploadMutation = useMutation({
        mutationFn: (f: File) => uploadExportZIP(f),
        onSuccess: (data) => {
            setImportKind("zip");
            if (data.task_id) {
                setTaskId(data.task_id);
                localStorage.setItem("vectorbox_upload_task_id", data.task_id);
                localStorage.setItem("vectorbox_upload_user_id", String(session.id));
            }
            setStep("taste"); // pick tags while the import runs in the background
        },
    });

    const rssMutation = useMutation({
        mutationFn: (username: string) => syncRSS(username),
        onSuccess: (data) => {
            setImportKind("rss");
            if (data.task_id) setTaskId(data.task_id);
            setStep("taste");
        },
    });

    // Recover an in-flight upload after a reload (same keys as upload-zone).
    useEffect(() => {
        const savedTaskId = localStorage.getItem("vectorbox_upload_task_id");
        const savedUserId = localStorage.getItem("vectorbox_upload_user_id");
        if (savedTaskId && savedUserId && Number(savedUserId) === session.id) {
            setTaskId(savedTaskId);
            setImportKind("zip");
            setStep("enrich");
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // ---- step 3 · taste ----
    const [tagStates, setTagStates] = useState<Record<string, TagState>>({});
    useEffect(() => {
        api.get("/api/onboarding/status")
            .then((res) => setTagStates(preferencesToTagState(res.data?.tag_preferences || null)))
            .catch(() => {});
    }, []);
    const handleTagChange = (next: Record<string, TagState>) => {
        setTagStates(next);
        api.post("/api/onboarding/tags", tagStateToPreferences(next)).catch(() => {});
    };
    const activeTagCount = useMemo(() => Object.values(tagStates).filter((v) => v !== "neutral").length, [tagStates]);

    // ---- step 4 · enrich (real progress) ----
    const { status: taskStatus, error: taskError } = useTaskProgress(step === "enrich" ? taskId : null, {
        onComplete: () => {
            localStorage.removeItem("vectorbox_upload_task_id");
            localStorage.removeItem("vectorbox_upload_user_id");
            setStep("ready");
        },
        onError: () => {
            localStorage.removeItem("vectorbox_upload_task_id");
            localStorage.removeItem("vectorbox_upload_user_id");
        },
    });

    // ---- step 5 · ready ----
    const { data: space } = useQuery({
        queryKey: ["space"],
        queryFn: getSpace,
        enabled: step === "ready",
        staleTime: 60 * 1000,
    });

    const handleZipDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragging(false);
        const dropped = e.dataTransfer.files[0];
        if (dropped && dropped.name.endsWith(".zip")) setFile(dropped);
    };

    const startImport = () => {
        if (file) {
            uploadMutation.mutate(file);
        } else if (rssAutoSync && linkedHandle) {
            rssMutation.mutate(linkedHandle);
        }
    };

    return (
        <div className="w-full max-w-2xl space-y-6">
            <Stepper current={step} />

            {/* ============ 1 · IDENTIFY ============ */}
            {step === "identify" && (
                <m.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="border border-border-2 bg-bg-2 p-6">
                    {!confirming ? (
                        <>
                            <h2 className="font-display text-2xl uppercase tracking-[-0.01em] text-fg">{t("wiz.identify_title")}</h2>
                            <p className="mt-2 font-mono text-[11px] leading-relaxed text-fg-3">
                                letterboxd is a data source, never an identity provider — we read your public diary,
                                nothing is posted.
                            </p>
                            <div className="mt-5 flex gap-2">
                                <input
                                    type="text"
                                    value={handle}
                                    onChange={(e) => setHandle(e.target.value.trim().replace(/^@/, ""))}
                                    onKeyDown={(e) => {
                                        if (e.key === "Enter" && handle.trim()) setConfirming(true);
                                    }}
                                    placeholder={t("wiz.handle_ph")}
                                    className="flex-1 border border-border-2 bg-bg-3 px-3 py-2.5 font-mono text-sm text-fg placeholder:text-fg-3 focus:border-primary focus:outline-none"
                                />
                                <button
                                    onClick={() => setConfirming(true)}
                                    disabled={!handle.trim()}
                                    className="bg-primary px-4 py-2.5 font-display text-xs font-bold uppercase tracking-[0.08em] text-primary-ink disabled:cursor-not-allowed disabled:opacity-40"
                                >
                                    link →
                                </button>
                            </div>
                        </>
                    ) : (
                        <>
                            <p className="eyebrow mb-2">{t("wiz.confirm")}</p>
                            <div className="border border-primary/40 bg-bg py-5 text-center">
                                <p className="break-all px-4 font-display text-3xl tracking-tight text-primary">@{handle}</p>
                            </div>
                            <p className="mt-3 font-mono text-[11px] leading-relaxed text-fg-3">
                                this account's public diary becomes your taste source. make sure it's yours —
                                imports replace your current data.
                            </p>
                            <div className="mt-4 flex gap-2">
                                <button
                                    onClick={() => session.id && linkMutation.mutate({ id: session.id, username: handle })}
                                    disabled={linkMutation.isPending}
                                    className="flex items-center gap-2 bg-primary px-5 py-2.5 font-display text-xs font-bold uppercase tracking-[0.08em] text-primary-ink disabled:opacity-50"
                                >
                                    {linkMutation.isPending && <Loader2 className="size-3.5 animate-spin" />}
                                    yes, that&apos;s me
                                </button>
                                <button
                                    onClick={() => setConfirming(false)}
                                    disabled={linkMutation.isPending}
                                    className="border border-border-2 px-4 py-2.5 font-mono text-xs uppercase text-fg-2 transition-colors hover:border-fg-3"
                                >
                                    go back
                                </button>
                            </div>
                        </>
                    )}
                    {linkMutation.isError && (
                        <p className="mt-3 font-mono text-[11px] text-danger">
                            {linkMutation.error instanceof Error ? linkMutation.error.message : "Failed to link account"}
                        </p>
                    )}
                </m.div>
            )}

            {/* ============ 2 · IMPORT ============ */}
            {step === "import" && (
                <m.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
                    <div className="border border-border-2 bg-bg-2 p-6">
                        <h2 className="font-display text-2xl uppercase tracking-[-0.01em] text-fg">{t("wiz.import_title")}</h2>
                        <p className="mt-1 font-mono text-[11px] text-fg-3">
                            importing for <span className="text-primary">@{linkedHandle}</span>
                        </p>

                        {/* ZIP drop */}
                        <div
                            onDragOver={(e) => {
                                e.preventDefault();
                                setIsDragging(true);
                            }}
                            onDragLeave={() => setIsDragging(false)}
                            onDrop={handleZipDrop}
                            className={cn(
                                "relative mt-4 cursor-pointer border-2 border-dashed p-10 text-center transition-colors",
                                isDragging ? "border-primary bg-primary/5" : "border-border-2 bg-bg hover:border-fg-3",
                                uploadMutation.isPending && "pointer-events-none opacity-50"
                            )}
                        >
                            <input
                                type="file"
                                accept=".zip"
                                onChange={(e) => e.target.files?.[0] && setFile(e.target.files[0])}
                                className="absolute inset-0 size-full cursor-pointer opacity-0"
                            />
                            <div className="pointer-events-none space-y-3">
                                {file ? (
                                    <FileArchive className="mx-auto size-10 text-primary" />
                                ) : (
                                    <Upload className="mx-auto size-10 text-fg-3" />
                                )}
                                {file ? (
                                    <>
                                        <p className="break-all font-mono text-sm font-bold text-fg">{file.name}</p>
                                        <p className="font-mono text-[11px] text-primary">{t("wiz.ready_import")}</p>
                                    </>
                                ) : (
                                    <>
                                        <p className="font-mono text-sm text-fg-2">{t("wiz.drop")}</p>
                                        <p className="font-mono text-[10px] uppercase tracking-[0.08em] text-fg-3">
                                            or tap to browse · letterboxd.com → settings → data
                                        </p>
                                    </>
                                )}
                            </div>
                        </div>

                        {/* RSS toggle */}
                        <div className="mt-4 flex items-center justify-between border border-border bg-bg p-3">
                            <div>
                                <div className="flex items-center gap-2">
                                    <span className="font-mono text-xs text-fg">{t("wiz.rss_auto")}</span>
                                    <span className="bg-primary px-1.5 py-0.5 font-display text-[8px] font-bold uppercase tracking-[0.12em] text-primary-ink">
                                        recommended
                                    </span>
                                </div>
                                <p className="mt-0.5 font-mono text-[10px] text-fg-3">
                                    keep pulling new diary entries automatically
                                </p>
                            </div>
                            <BracketToggle checked={rssAutoSync} onChange={setRssAutoSync} label="RSS auto-sync" />
                        </div>
                    </div>

                    <div className="flex items-center justify-between">
                        <button
                            onClick={() => setStep("taste")}
                            className="font-mono text-[11px] uppercase tracking-[0.08em] text-fg-3 transition-colors hover:text-fg-2"
                        >
                            skip import for now →
                        </button>
                        <button
                            onClick={startImport}
                            disabled={(!file && !(rssAutoSync && linkedHandle)) || uploadMutation.isPending || rssMutation.isPending}
                            className="flex items-center gap-2 bg-primary px-6 py-2.5 font-display text-xs font-bold uppercase tracking-[0.08em] text-primary-ink shadow-acid-fg disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            {(uploadMutation.isPending || rssMutation.isPending) && <Loader2 className="size-3.5 animate-spin" />}
                            {file ? "start import →" : "sync via rss →"}
                        </button>
                    </div>
                    {(uploadMutation.isError || rssMutation.isError) && (
                        <p className="font-mono text-[11px] text-danger">{t("wiz.import_failed")}</p>
                    )}
                </m.div>
            )}

            {/* ============ 3 · TASTE ============ */}
            {step === "taste" && (
                <m.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
                    <div className="border border-border-2 bg-bg-2 p-6">
                        <h2 className="font-display text-2xl uppercase tracking-[-0.01em] text-fg">{t("wiz.taste_title")}</h2>
                        <p className="mt-1 font-mono text-[11px] text-fg-3">
                            tap to toggle · these never show up in your recommendations
                            {taskId ? " · your import is running in the background" : ""}
                        </p>
                        <div className="mt-4">
                            <TagSelector value={tagStates} onChange={handleTagChange} />
                        </div>
                    </div>
                    <div className="flex items-center justify-between">
                        <span className="font-mono text-[10px] text-fg-3">
                            {activeTagCount > 0 ? `${activeTagCount} ${t("wiz.filters_active")}` : t("wiz.skip_all")}
                        </span>
                        <button
                            onClick={() => setStep(taskId ? "enrich" : "ready")}
                            className="bg-primary px-6 py-2.5 font-display text-xs font-bold uppercase tracking-[0.08em] text-primary-ink shadow-acid-fg"
                        >
                            continue →
                        </button>
                    </div>
                </m.div>
            )}

            {/* ============ 4 · ENRICH ============ */}
            {step === "enrich" && (
                <m.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="border border-border-2 bg-bg-2 p-6">
                    <h2 className="font-display text-2xl uppercase tracking-[-0.01em] text-fg">{t("wiz.enrich_title")}</h2>
                    <p className="mt-1 font-mono text-[11px] text-fg-3">
                        {taskError
                            ? "import failed"
                            : taskStatus?.step ||
                              (importKind === "rss"
                                  ? "syncing your public diary…"
                                  : "parsing entries · matching TMDB · computing taste embeddings…")}
                    </p>

                    <div className="relative mt-5 h-2.5 overflow-hidden border border-border-2 bg-bg-3">
                        <m.div
                            className="absolute inset-y-0 left-0 bg-primary"
                            animate={{ width: `${taskStatus?.progress || 0}%` }}
                            transition={{ duration: 0.3, ease: "easeOut" }}
                        />
                    </div>
                    <div className="mt-1.5 flex justify-between font-mono text-[10px] uppercase tracking-[0.1em] text-fg-3">
                        <span>{taskError ? "error" : taskStatus?.status || "starting"}</span>
                        <span className="font-display text-primary">{taskStatus?.progress || 0}%</span>
                    </div>

                    {taskError ? (
                        <div className="mt-4 flex gap-2">
                            <p className="flex-1 font-mono text-[11px] text-danger">{taskError}</p>
                            <button
                                onClick={() => {
                                    setTaskId(null);
                                    setStep("import");
                                }}
                                className="border border-border-2 px-3 py-1.5 font-mono text-[10px] uppercase text-fg-2 hover:border-primary hover:text-primary"
                            >
                                retry import
                            </button>
                        </div>
                    ) : !taskId ? (
                        <div className="mt-4 flex items-center justify-between">
                            <p className="font-mono text-[11px] text-fg-3">no import running — rss rolling sync will fill your library.</p>
                            <button
                                onClick={() => setStep("ready")}
                                className="bg-primary px-4 py-2 font-display text-xs font-bold uppercase text-primary-ink"
                            >
                                continue →
                            </button>
                        </div>
                    ) : null}
                </m.div>
            )}

            {/* ============ 5 · READY ============ */}
            {step === "ready" && (
                <m.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="border border-border-2 bg-bg-2 p-6 text-center">
                    <p className="font-display text-5xl text-primary">✓</p>
                    <h2 className="mt-3 font-display text-2xl uppercase tracking-[-0.01em] text-fg">{t("wiz.ready_title")}</h2>
                    <p className="mt-2 font-mono text-[11px] text-fg-3">
                        {space && space.total > 0
                            ? `${space.total} films placed · ${space.clusters.length} clusters · ◆ = you`
                            : "your space is calibrating — it sharpens as films are enriched"}
                    </p>
                    <button
                        onClick={onComplete}
                        className="mt-6 bg-primary px-8 py-3 font-display text-sm font-bold uppercase tracking-[0.08em] text-primary-ink shadow-acid-fg transition-transform hover:-translate-x-px hover:-translate-y-px"
                    >
                        enter feed →
                    </button>
                </m.div>
            )}

            {onSkip && step !== "ready" && (
                <div className="text-center">
                    <button
                        onClick={onSkip}
                        className="font-mono text-xs uppercase tracking-wider text-fg-3 underline decoration-border-2 underline-offset-4 transition-colors hover:text-fg-2"
                    >
                        skip for now — browse without imports
                    </button>
                </div>
            )}
        </div>
    );
}
