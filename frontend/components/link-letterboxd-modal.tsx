"use client";

import { useState } from "react";
import { m, AnimatePresence } from "framer-motion";
import { X, Link2, Loader2, Check, ExternalLink } from "lucide-react";
import { linkLetterboxd } from "@/lib/api";

interface LinkLetterboxdModalProps {
    isOpen: boolean;
    onClose: () => void;
    userId: number;
    currentLetterboxd?: string | null;
    onSuccess?: (username: string) => void;
}

/**
 * Link Letterboxd Modal - v1.1 Component
 * Allows users to link or update their Letterboxd profile.
 */
export function LinkLetterboxdModal({
    isOpen,
    onClose,
    userId,
    currentLetterboxd,
    onSuccess,
}: LinkLetterboxdModalProps) {
    const [username, setUsername] = useState(currentLetterboxd || "");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError(null);
        setLoading(true);

        try {
            await linkLetterboxd(userId, username);
            setSuccess(true);
            onSuccess?.(username);

            // Auto-close after success
            setTimeout(() => {
                onClose();
                setSuccess(false);
            }, 1500);
        } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            setError(error.response?.data?.detail || "Failed to link profile");
        } finally {
            setLoading(false);
        }
    };

    const handleClose = () => {
        if (!loading) {
            setError(null);
            setSuccess(false);
            onClose();
        }
    };

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <m.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-950/80 backdrop-blur-sm"
                onClick={handleClose}
            >
                <m.div
                    initial={{ scale: 0.9, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    exit={{ scale: 0.9, opacity: 0 }}
                    className="w-full max-w-md mx-4 bg-zinc-900 border border-zinc-700"
                    onClick={(e) => e.stopPropagation()}
                >
                    {/* Header */}
                    <div className="flex items-center justify-between p-4 border-b border-zinc-700">
                        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                            <Link2 className="text-primary" size={20} />
                            Link Letterboxd
                        </h2>
                        <button
                            onClick={handleClose}
                            disabled={loading}
                            className="text-zinc-400 hover:text-white transition-colors disabled:opacity-50"
                        >
                            <X size={20} />
                        </button>
                    </div>

                    {/* Body */}
                    <form onSubmit={handleSubmit} className="p-6">
                        {success ? (
                            <m.div
                                initial={{ scale: 0.8, opacity: 0 }}
                                animate={{ scale: 1, opacity: 1 }}
                                className="text-center py-8"
                            >
                                <div className="size-16 mx-auto mb-4 bg-primary rounded-full flex items-center justify-center">
                                    <Check size={32} className="text-black" />
                                </div>
                                <p className="text-white text-lg font-medium">Linked!</p>
                                <p className="text-zinc-400 text-sm mt-1">
                                    Your profile is now connected to @{username}
                                </p>
                            </m.div>
                        ) : (
                            <>
                                {/* Error Message */}
                                {error && (
                                    <div className="mb-4 p-3 bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
                                        {error}
                                    </div>
                                )}

                                {/* Current Profile */}
                                {currentLetterboxd && (
                                    <div className="mb-4 p-3 bg-zinc-800 border border-zinc-700 text-sm">
                                        <span className="text-zinc-400">Currently linked: </span>
                                        <a
                                            href={`https://letterboxd.com/${currentLetterboxd}/`}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="text-primary hover:underline inline-flex items-center gap-1"
                                        >
                                            @{currentLetterboxd}
                                            <ExternalLink size={12} />
                                        </a>
                                    </div>
                                )}

                                {/* Username Input */}
                                <div className="mb-6">
                                    <label htmlFor="lb-username" className="block text-sm font-medium text-zinc-400 mb-2 uppercase tracking-wider">
                                        Letterboxd Username
                                    </label>
                                    <div className="flex items-center">
                                        <span className="p-3 bg-zinc-800 border border-r-0 border-zinc-600 text-zinc-500">
                                            letterboxd.com/
                                        </span>
                                        <input
                                            id="lb-username"
                                            type="text"
                                            value={username}
                                            onChange={(e) => setUsername(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
                                            placeholder="yourprofile"
                                            className="flex-1 px-4 py-3 bg-zinc-800 border border-zinc-600 text-white placeholder-zinc-500 focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-colors"
                                            required
                                            minLength={1}
                                            maxLength={50}
                                        />
                                    </div>
                                    <p className="text-xs text-zinc-500 mt-2">
                                        This is the public profile used to import your watch history and ratings.
                                    </p>
                                </div>

                                {/* Submit Button */}
                                <m.button
                                    type="submit"
                                    disabled={loading || !username}
                                    whileHover={{ scale: 1.02 }}
                                    whileTap={{ scale: 0.98 }}
                                    className="w-full py-3 bg-primary text-black font-bold uppercase tracking-wider flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed glow-primary-hover transition-all"
                                >
                                    {loading ? (
                                        <>
                                            <Loader2 className="animate-spin" size={18} />
                                            Linking…
                                        </>
                                    ) : (
                                        <>
                                            <Link2 size={18} />
                                            {currentLetterboxd ? "Update Profile" : "Link Profile"}
                                        </>
                                    )}
                                </m.button>
                            </>
                        )}
                    </form>
                </m.div>
            </m.div>
        </AnimatePresence>
    );
}

export default LinkLetterboxdModal;
