"use client";

// Sign-in — handoff split-panel shell (auth-panel.tsx). AUTH LOGIC IS
// BYTE-EQUIVALENT to the pre-reskin version: ?migrate=true flow,
// claim-anonymous single-caller, flushGuestState, redirect targets and the
// hash-routing workaround are untouched. Only the shell/JSX changed.

import { Suspense, useState, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth, SignIn } from "@clerk/nextjs";
import { api } from "@/lib/api";
import { Loader2 } from "lucide-react";
import { AuthSplit, clerkAcidAppearance } from "@/components/auth-panel";
import { useLanguage } from "@/components/language-provider";

function LoginContent() {
    const { t } = useLanguage();
    const { isLoaded, isSignedIn } = useAuth();
    const { push } = useRouter();
    const searchParams = useSearchParams();
    const isMigrate = searchParams.get("migrate") === "true";
    const redirectUrl = isMigrate ? "/login?migrate=true" : "/";
    const [migrating, setMigrating] = useState(false);
    const migrationAttempted = useRef(false);
    const newUserCheckAttempted = useRef(false);

    // After sign-in: migrate guest data or show onboarding chooser for new users
    useEffect(() => {
        if (!isLoaded || !isSignedIn) return;

        // Clear stale guest localStorage. Tags now persist server-side (the
        // guest /onboarding/tags page POSTs to the API, and claim-anonymous
        // copies tag_preferences atomically with ratings) so this is pure
        // cleanup — no HTTP calls, no race window. Runs on BOTH paths
        // (migrate + plain signin) so a stale vb_skip_onboarding from an
        // earlier guest session doesn't strand a fresh signup.
        const flushGuestState = () => {
            [
                "vb_guest_ratings",
                "vb_guest_tags",
                "vb_guest_tags:v1",
                "vb_onboarding_progress",
                "vb_onboarding_movies",
                "vb_skip_onboarding",
            ].forEach((k) => localStorage.removeItem(k));
        };

        if (!isMigrate) {
            // After a plain sign-in: clear stale guest state and go to the
            // dashboard. The dashboard is the SINGLE source of onboarding
            // routing — it redirects 0-rating users to /onboarding. The old
            // in-page onboarding-chooser was unreachable anyway (the <SignIn>
            // forceRedirectUrl="/" navigates away before it could render).
            if (newUserCheckAttempted.current) return;
            newUserCheckAttempted.current = true;
            flushGuestState();
            push("/");
            return;
        }

        if (migrationAttempted.current) return;
        migrationAttempted.current = true;

        const migrateGuestData = async () => {
            setMigrating(true);
            try {
                // Promote anonymous session to registered user (transfers ratings + tag_preferences, deletes cookie)
                await api.post("/api/auth/claim-anonymous");
                flushGuestState();
                push("/?onboarding_complete=true");
            } catch (err) {
                console.error("Migration failed:", err);
                push("/");
            }
        };

        migrateGuestData();
    }, [isLoaded, isSignedIn, isMigrate, push]);

    if (!isLoaded) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-bg">
                <span className="font-mono text-xs text-fg-3">[ LOADING ]</span>
            </div>
        );
    }

    if (migrating) {
        return (
            <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-bg">
                <Loader2 className="size-8 animate-spin text-primary" />
                <p className="font-mono text-xs uppercase tracking-widest text-fg-3">
                    {t("auth.migrating")}
                </p>
            </div>
        );
    }

    // Already signed in on the plain-login path: the useEffect above is
    // pushing to "/". Render a redirect spinner rather than the now-dead
    // <SignIn/> (Clerk renders it blank once a session exists) so there's
    // never a blank flash while navigation completes.
    if (isSignedIn && !isMigrate) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-bg">
                <Loader2 className="size-8 animate-spin text-primary" />
            </div>
        );
    }

    // Straight to the sign-in form — the old "choose" step (letterboxd account /
    // rate films) was redundant friction before the form. Guest/rate paths live on
    // the landing + the "try guest mode" link below.
    return (
        <AuthSplit>
            <div className="w-full max-w-md space-y-4">
                <div>
                    <p className="eyebrow mb-2 text-primary">{t("land.tier_account")}</p>
                    <h1 className="font-display text-4xl uppercase leading-none tracking-[-0.02em] text-fg">
                        {t("land.signin")}
                    </h1>
                    <p className="mt-2 font-mono text-[11px] text-fg-3">{t("auth.signin_sub")}</p>
                </div>

                <SignIn
                    // Hash routing keeps every sub-step (email-code verification,
                    // OAuth/SSO callback, MFA) on THIS page via the URL hash. Path
                    // routing — Clerk's default — navigates to /login/factor-one etc.,
                    // which 404s here because this is NOT a catch-all route.
                    routing="hash"
                    // Keep the "Sign up" link on OUR /register page.
                    signUpUrl="/register"
                    appearance={clerkAcidAppearance}
                    fallbackRedirectUrl={redirectUrl}
                    forceRedirectUrl={redirectUrl}
                    signUpFallbackRedirectUrl={redirectUrl}
                    signUpForceRedirectUrl={redirectUrl}
                />

                <button
                    onClick={() => push("/onboarding")}
                    className="font-mono text-[11px] text-fg-3 transition-colors hover:text-primary"
                >
                    {t("auth.guest_unlock")}
                </button>
            </div>
        </AuthSplit>
    );
}
export default function LoginPage() {
    return (
        <Suspense fallback={<div className="min-h-screen bg-bg" />}>
            <LoginContent />
        </Suspense>
    );
}
