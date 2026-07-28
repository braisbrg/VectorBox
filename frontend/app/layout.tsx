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

// ACID system is mono-only: IBM Plex Mono is the workhorse (body/UI), Martian
// Mono (self-hosted, OFL, variable 400-700) is display-only for headings.
//
// Martian replaced Departure Mono on 2026-07-28. Departure is a pixel/bitmap
// face: it reads as retro gaming rather than film archive, and it has neither
// italics nor real intermediate weights, so it works as a sign and falls apart
// in a paragraph. Martian keeps the mono-only rule the system is built on —
// which matters because this UI is a grid of numbers (VBS, years, runtimes) and
// a monospace aligns them without `tabular-nums` — while carrying far more
// presence than Plex at display size.
const plex = IBM_Plex_Mono({
    subsets: ["latin"],
    // 500 was declared and never used — nothing in the app sets font-medium — so
    // next/font preloaded a weight no glyph ever needed, which is the
    // "preloaded with link preload was not used" warning in the console.
    // Counted 2026-07-28: font-normal 5, font-semibold 5, font-bold 95, medium 0.
    weight: ["400", "600", "700"],
    variable: "--font-plex",
    display: "swap",
});
const martian = localFont({
    src: "../public/fonts/MartianMono-Variable.woff2",
    // Kept as --font-departure so every `font-display` call site and the
    // .eyebrow/.trident-mark kit classes pick it up without a sweep. Renaming
    // the variable is cosmetic churn across ~45 files; renaming the FACE is the
    // change that matters. TODO if it ever bothers anyone: rename to
    // --font-display-face in one pass.
    variable: "--font-departure",
    display: "swap",
    weight: "400 700",
});

export const metadata: Metadata = {
    title: "VectorBox",
    description: "Advanced AI Movie Recommendations",
    icons: {
        // SVG first (sharp at every size, 300 bytes vs the 300KB PNG); the PNG
        // stays as the fallback for anything that cannot render an SVG favicon.
        icon: [
            { url: "/icon.svg", type: "image/svg+xml" },
            { url: "/icon.png", type: "image/png" },
        ],
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

    // The next/font variables MUST live on <html>, not <body>: @theme declares
    // --font-mono/--font-sans/--font-display on :root as var(--font-plex)…, and
    // a custom property whose var() target is undefined at its own declaring
    // element is invalid at computed-value time — and that invalid value is what
    // every descendant inherits. With them on <body>, the whole mono system
    // silently resolved to Tailwind's default sans across the entire app.
    return (
        <html
            lang={initialLanguage}
            data-theme="acid"
            suppressHydrationWarning
            className={`${plex.variable} ${martian.variable}`}
        >
            <head>
                {/* Apply the persisted accent palette before first paint (no flash). */}
                <script
                    dangerouslySetInnerHTML={{
                        __html: `try{var t=localStorage.getItem("vb_theme");if(t)document.documentElement.dataset.theme=t;}catch(e){}`,
                    }}
                />
            </head>
            <body suppressHydrationWarning className={`font-mono antialiased min-h-screen bg-background text-foreground overflow-x-clip selection:bg-primary selection:text-[color:var(--primary-ink)]`}>
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
