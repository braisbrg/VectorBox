import type { Metadata } from "next";
import { cookies } from "next/headers";
import { IBM_Plex_Mono } from "next/font/google";
import localFont from "next/font/local";
import { ClerkProvider } from "@clerk/nextjs";
import { LazyMotion, domAnimation } from "framer-motion";
import "./globals.css";
import { Providers } from "./providers";
import { LanguageProvider } from "@/components/language-provider";
import { AuthBridge } from "@/components/auth-bridge";
import { isLanguage, type Language } from "@/lib/i18n";

// ACID system is mono-only: IBM Plex Mono is the workhorse (body/UI), Departure
// Mono (self-hosted, OFL) is display-only for headings (--font-display).
const plex = IBM_Plex_Mono({
    subsets: ["latin"],
    weight: ["400", "500", "600", "700"],
    variable: "--font-plex",
    display: "swap",
});
const departure = localFont({
    src: "../public/fonts/DepartureMono-Regular.woff2",
    variable: "--font-departure",
    display: "swap",
    weight: "400 700",
});

export const metadata: Metadata = {
    title: "VectorBox",
    description: "Advanced AI Movie Recommendations",
    icons: {
        icon: "/icon.png",
    },
};

export const viewport = {
    width: "device-width",
    initialScale: 1,
    // maximumScale removed - accessibility: allow pinch-to-zoom
};

export default async function RootLayout({
    children,
}: Readonly<{
    children: React.ReactNode;
}>) {
    // Locale resolved by middleware (Accept-Language) and persisted in NEXT_LOCALE.
    // Reading it here makes the server render + first paint use the right language
    // (no flash of English before the client hydrates).
    const cookieStore = await cookies();
    const localeCookie = cookieStore.get("NEXT_LOCALE")?.value;
    const initialLanguage: Language = isLanguage(localeCookie) ? localeCookie : "en";

    return (
        <html lang={initialLanguage} data-theme="acid" suppressHydrationWarning>
            <head>
                {/* Apply the persisted accent palette before first paint (no flash). */}
                <script
                    dangerouslySetInnerHTML={{
                        __html: `try{var t=localStorage.getItem("vb_theme");if(t)document.documentElement.dataset.theme=t;}catch(e){}`,
                    }}
                />
            </head>
            <body suppressHydrationWarning className={`${plex.variable} ${departure.variable} font-mono antialiased min-h-screen bg-background text-foreground overflow-x-hidden selection:bg-primary selection:text-[color:var(--primary-ink)]`}>
                <ClerkProvider>
                    <AuthBridge />
                    <LazyMotion features={domAnimation}>
                        <LanguageProvider initialLanguage={initialLanguage}>
                            <Providers>
                                {children}
                            </Providers>
                        </LanguageProvider>
                    </LazyMotion>
                </ClerkProvider>
            </body>
        </html>
    );
}
