"use client";

import Link from "next/link";
import { SignUp } from "@clerk/nextjs";
import { AuthSplit, clerkAcidAppearance } from "@/components/auth-panel";
import { useLanguage } from "@/components/language-provider";

export default function RegisterPage() {
    const { t } = useLanguage();
    return (
        <AuthSplit>
            <div className="w-full max-w-md space-y-5">
                <div>
                    <p className="eyebrow mb-2 text-primary">{t("land.tier_account")}</p>
                    <h1 className="font-display text-4xl uppercase leading-none tracking-[-0.02em] text-fg">
                        {t("auth.create_title")}
                    </h1>
                    <p className="mt-2 font-mono text-[10px] text-fg-3">
                        {t("land.auth_note")}
                    </p>
                </div>

                <SignUp
                    // See login/page.tsx: hash routing keeps email-verification /
                    // SSO sub-steps on this page instead of path-navigating to a
                    // non-existent /register/* route (blank-page bug).
                    routing="hash"
                    // Keep the "Sign in" link on OUR /login page.
                    signInUrl="/login"
                    appearance={clerkAcidAppearance}
                    fallbackRedirectUrl="/"
                    forceRedirectUrl="/login?migrate=true"
                />

                <div className="flex items-center justify-between font-mono text-[11px]">
                    <Link href="/login" className="text-fg-2 underline underline-offset-4 transition-colors hover:text-primary">
                        {t("auth.already")}
                    </Link>
                    <Link href="/onboarding" className="text-fg-3 transition-colors hover:text-primary">
                        {t("auth.try_guest")}
                    </Link>
                </div>
            </div>
        </AuthSplit>
    );
}
