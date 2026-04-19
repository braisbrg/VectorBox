import type { Metadata } from "next";
import { Inter, Space_Grotesk, Space_Mono } from "next/font/google"; // Added Space Mono
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";
import { Providers } from "./providers";
import { LanguageProvider } from "@/components/language-provider";
import { MobileNavProvider } from "@/components/mobile-nav-context";
import { AuthBridge } from "@/components/auth-bridge";

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

export default function RootLayout({
    children,
}: Readonly<{
    children: React.ReactNode;
}>) {
    return (
        <html lang="en" suppressHydrationWarning>
            <body suppressHydrationWarning className={`${inter.className} ${spaceGrotesk.variable} ${spaceMono.variable} antialiased min-h-screen bg-background text-foreground overflow-x-hidden selection:bg-primary selection:text-black`}>
                <ClerkProvider>
                    <AuthBridge />
                    <LanguageProvider>
                        <Providers>
                            <MobileNavProvider>
                                {children}
                            </MobileNavProvider>
                        </Providers>
                    </LanguageProvider>
                </ClerkProvider>
            </body>
        </html>
    );
}
