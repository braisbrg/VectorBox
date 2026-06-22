import type { Metadata } from "next";
import { cookies } from "next/headers";
import { Inter, Space_Grotesk, Space_Mono } from "next/font/google"; // Added Space Mono
import { ClerkProvider } from "@clerk/nextjs";
import { LazyMotion, domAnimation } from "framer-motion";
import "./globals.css";
import { Providers } from "./providers";
import { LanguageProvider } from "@/components/language-provider";
import { MobileNavProvider } from "@/components/mobile-nav-context";
import { AuthBridge } from "@/components/auth-bridge";
import { isLanguage, type Language } from "@/lib/i18n";

const inter = Inter({ subsets: ["latin"], display: "optional" });
const spaceGrotesk = Space_Grotesk({ subsets: ["latin"], variable: "--font-space", display: "optional" });
const spaceMono = Space_Mono({ weight: ["400", "700"], subsets: ["latin"], variable: "--font-mono-acid", display: "optional" });

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
        <html lang={initialLanguage} suppressHydrationWarning>
            <body suppressHydrationWarning className={`${inter.className} ${spaceGrotesk.variable} ${spaceMono.variable} antialiased min-h-screen bg-background text-foreground overflow-x-hidden selection:bg-primary selection:text-black`}>
                <ClerkProvider>
                    <AuthBridge />
                    <LazyMotion features={domAnimation}>
                        <LanguageProvider initialLanguage={initialLanguage}>
                            <Providers>
                                <MobileNavProvider>
                                    {children}
                                </MobileNavProvider>
                            </Providers>
                        </LanguageProvider>
                    </LazyMotion>
                </ClerkProvider>
            </body>
        </html>
    );
}
