"use client";

import { useState, useCallback, useEffect } from "react";
import { Upload, FileText, Loader2, FileArchive, Plus, User as UserIcon, RefreshCw, Check, AlertCircle } from "lucide-react";
import { useMutation } from "@tanstack/react-query";
import { uploadExportZIP, createUser, syncRSS } from "@/lib/api";
import { motion } from "framer-motion";

interface User {
    id: number;
    username: string;
}

interface UploadZoneProps {
    onUploadSuccess: (userId: number) => void;
    users: User[];
    onUserCreated: (user: User) => void;
    currentUserId?: number | null;
    onUserSelect?: (userId: number) => void;
}

function RSSSyncButton({ username, onSyncSuccess }: { username: string, onSyncSuccess: () => void }) {
    const [isLoading, setIsLoading] = useState(false);
    const [status, setStatus] = useState<"idle" | "success" | "error">("idle");
    const [stats, setStats] = useState<any>(null);

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
                    Sync RSS for {username}
                </button>
            </div>

            {/* Info Box */}
            <div className="text-xs text-muted-foreground bg-muted/50 p-3 rounded border w-full text-center">
                <p>
                    Syncs last <strong>50 items</strong> from Diary & Watchlist.
                    <br />
                    Use <strong>Zip Upload ONLY</strong> for full history.
                </p>
            </div>

            {status === "success" && stats && (
                <div className="flex flex-col gap-1">
                    <div className="text-xs text-green-500 flex items-center gap-1.5 bg-green-500/10 p-2 rounded animate-in fade-in slide-in-from-top-1">
                        <Check className="w-3 h-3" />
                        <span>
                            Synced: {stats.rss_new_movies || 0} new, {stats.rss_new_ratings || 0} watched, {stats.rss_updated_ratings || 0} updated, {stats.watchlist_added || 0} watchlist
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
                    <span>Sync failed. Check console for details (likely timeout).</span>
                </div>
            )}
        </div>
    );
}

export function UploadZone({ onUploadSuccess, users, onUserCreated, currentUserId, onUserSelect }: UploadZoneProps) {
    const [isDragging, setIsDragging] = useState(false);
    const [file, setFile] = useState<File | null>(null);
    const [localSelectedUserId, setLocalSelectedUserId] = useState<number | "new">(
        currentUserId || (users.length > 0 ? users[0].id : "new")
    );
    const [newUsername, setNewUsername] = useState("");
    const [isCreatingUser, setIsCreatingUser] = useState(false);

    // Sync with parent prop if provided
    useEffect(() => {
        if (currentUserId) {
            setLocalSelectedUserId(currentUserId);
        }
    }, [currentUserId]);

    const createUserMutation = useMutation({
        mutationFn: createUser,
        onSuccess: (newUser) => {
            onUserCreated(newUser);
            setLocalSelectedUserId(newUser.id);
            if (onUserSelect) onUserSelect(newUser.id);
            setIsCreatingUser(false);
            setNewUsername("");
        },
    });

    const uploadMutation = useMutation({
        mutationFn: (file: File) => {
            if (localSelectedUserId === "new") throw new Error("Please select or create a user");
            return uploadExportZIP(file, localSelectedUserId);
        },
        onSuccess: () => {
            if (typeof localSelectedUserId === "number") {
                onUploadSuccess(localSelectedUserId);
            }
        },
    });

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
        if (file && localSelectedUserId !== "new") {
            uploadMutation.mutate(file);
        } else if (localSelectedUserId === "new" && newUsername) {
            createUserMutation.mutate({ username: newUsername }, {
                onSuccess: (newUser) => {
                    if (file) {
                        // Immediate upload after creation
                        uploadExportZIP(file, newUser.id)
                            .then(() => onUploadSuccess(newUser.id))
                            .catch(() => { }); // Handle error if needed
                    }
                }
            });
        }
    };

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className="relative space-y-4"
        >
            {/* User Selection */}
            <div className="flex items-center justify-center gap-4 p-4 bg-card rounded-lg border">
                <div className="flex items-center gap-2">
                    <UserIcon className="w-4 h-4 text-muted-foreground" />
                    <span className="text-sm font-medium">User:</span>
                </div>

                {isCreatingUser || (users.length === 0 && localSelectedUserId === "new") ? (
                    <div className="flex items-center gap-2">
                        <input
                            type="text"
                            placeholder="Enter Letterboxd username"
                            value={newUsername}
                            onChange={(e) => setNewUsername(e.target.value)}
                            className="px-3 py-1 bg-background border rounded text-sm"
                            autoFocus
                        />
                        <button
                            onClick={() => {
                                if (newUsername) createUserMutation.mutate({ username: newUsername });
                            }}
                            disabled={createUserMutation.isPending || !newUsername}
                            className="px-3 py-1 bg-primary text-primary-foreground rounded text-sm font-medium disabled:opacity-50"
                        >
                            {createUserMutation.isPending ? "Creating..." : "Create"}
                        </button>
                        {users.length > 0 && (
                            <button
                                onClick={() => {
                                    setIsCreatingUser(false);
                                    if (currentUserId) setLocalSelectedUserId(currentUserId);
                                }}
                                className="p-1 hover:bg-muted rounded"
                            >
                                ✕
                            </button>
                        )}
                    </div>
                ) : (
                    <div className="flex items-center gap-2">
                        <select
                            value={localSelectedUserId}
                            onChange={(e) => {
                                const val = Number(e.target.value);
                                setLocalSelectedUserId(val);
                                if (onUserSelect) onUserSelect(val);
                            }}
                            className="px-3 py-1 bg-background border rounded text-sm min-w-[150px]"
                        >
                            {users.map((u) => (
                                <option key={u.id} value={u.id}>
                                    {u.username}
                                </option>
                            ))}
                        </select>

                        <button
                            onClick={() => {
                                setIsCreatingUser(true);
                                setLocalSelectedUserId("new");
                            }}
                            className="p-1.5 bg-primary/10 hover:bg-primary/20 text-primary rounded-md transition-colors"
                            title="Create New User"
                        >
                            <Plus className="w-4 h-4" />
                        </button>
                    </div>
                )}
            </div>

            {/* RSS Sync Button - Only show if user is selected */}
            {typeof localSelectedUserId === "number" && !isCreatingUser && (
                <div className="flex justify-center">
                    <RSSSyncButton
                        username={users.find(u => u.id === localSelectedUserId)?.username || ""}
                        onSyncSuccess={() => onUploadSuccess(localSelectedUserId)}
                    />
                </div>
            )}


            <div
                onDragOver={(e) => {
                    e.preventDefault();
                    setIsDragging(true);
                }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={handleDrop}
                className={`
          border-2 border-dashed rounded-lg p-12 text-center transition-all
          ${isDragging ? "border-primary bg-primary/5 scale-105" : "border-muted-foreground/25"}
          ${uploadMutation.isPending ? "opacity-50 pointer-events-none" : ""}
        `}
            >
                {uploadMutation.isPending ? (
                    <div className="space-y-4">
                        <Loader2 className="w-12 h-12 mx-auto animate-spin text-primary" />
                        <p className="text-lg font-medium">Processing your Letterboxd export...</p>
                        <p className="text-sm text-muted-foreground">
                            Parsing ratings, watchlist, and likes to build your profile
                        </p>
                    </div>
                ) : uploadMutation.isSuccess ? (
                    <div className="space-y-4">
                        <div className="w-12 h-12 mx-auto rounded-full bg-green-500/10 flex items-center justify-center">
                            <span className="text-2xl">✓</span>
                        </div>
                        <p className="text-lg font-medium text-green-600 dark:text-green-400">
                            Upload successful!
                        </p>
                        <p className="text-sm text-muted-foreground">
                            Your taste clusters are being generated...
                        </p>
                        <button
                            onClick={() => uploadMutation.reset()}
                            className="text-xs text-primary hover:underline"
                        >
                            Upload another file
                        </button>
                    </div>
                ) : (
                    <div className="space-y-4">
                        <FileArchive className="w-12 h-12 mx-auto text-muted-foreground" />
                        <div>
                            <p className="text-lg font-medium mb-2">
                                Upload your Letterboxd Export (ZIP)
                            </p>
                            <p className="text-sm text-muted-foreground mb-4">
                                Drag and drop or click to browse
                            </p>
                        </div>

                        {!file ? (
                            <label className="inline-flex items-center gap-2 px-6 py-3 bg-primary text-primary-foreground rounded-lg cursor-pointer hover:bg-primary/90 transition-colors">
                                <FileText className="w-4 h-4" />
                                <span>Choose ZIP File</span>
                                <input
                                    type="file"
                                    accept=".zip"
                                    onChange={handleFileInput}
                                    className="hidden"
                                />
                            </label>
                        ) : (
                            <div className="space-y-3">
                                <p className="text-sm font-medium bg-muted px-3 py-1 rounded inline-block">
                                    {file.name}
                                </p>
                                <div>
                                    <button
                                        onClick={handleUpload}
                                        disabled={localSelectedUserId === "new" && !newUsername}
                                        className="px-6 py-3 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50"
                                    >
                                        Start Upload
                                    </button>
                                    <button
                                        onClick={() => setFile(null)}
                                        className="ml-2 px-4 py-3 text-sm hover:bg-muted rounded-lg transition-colors"
                                    >
                                        Cancel
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {uploadMutation.isError && (
                    <div className="mt-4 p-4 bg-destructive/10 border border-destructive/20 rounded-lg">
                        <p className="text-sm text-destructive">
                            Upload failed. Please ensure you&apos;re uploading a valid Letterboxd export ZIP.
                        </p>
                    </div>
                )}
            </div>

            <div className="mt-4 text-center text-xs text-muted-foreground">
                <p>
                    Export your data from{" "}
                    <a
                        href="https://letterboxd.com/settings/data/"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary hover:underline"
                    >
                        Letterboxd Settings → Data
                    </a>
                </p>
            </div>
        </motion.div >
    );
}
