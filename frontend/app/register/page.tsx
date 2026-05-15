import { SignUp } from "@clerk/nextjs";

export default function RegisterPage() {
    return (
        <main className="min-h-screen flex items-center justify-center bg-background">
            <SignUp
                appearance={{
                    elements: {
                        rootBox: "font-mono",
                        card: "bg-background border border-border",
                        headerTitle: "text-primary font-mono",
                        formButtonPrimary: "bg-primary text-background font-mono rounded-none",
                    },
                }}
                fallbackRedirectUrl="/"
                forceRedirectUrl="/login?migrate=true"
            />
        </main>
    );
}
