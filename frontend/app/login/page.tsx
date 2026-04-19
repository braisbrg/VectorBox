"use client";

import { useAuth, SignIn } from "@clerk/nextjs";

export default function LoginPage() {
    const { isLoaded } = useAuth();

    if (!isLoaded) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-background">
                <span className="font-mono text-xs text-zinc-600">[ LOADING ]</span>
            </div>
        );
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-background">
            <SignIn
                appearance={{
                    elements: {
                        rootBox: "font-mono",
                        card: "bg-background border border-border",
                        headerTitle: "text-primary font-mono",
                        formButtonPrimary: "bg-primary text-background font-mono rounded-none",
                    },
                }}
                fallbackRedirectUrl="/"
            />
        </div>
    );
}
