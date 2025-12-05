import type { Metadata } from "next";
import { Inter, Space_Grotesk } from "next/font/google"; // Added Space Grotesk for display
import "./globals.css";
import { Providers } from "./providers";
import { LanguageProvider } from "@/components/language-provider";

const inter = Inter({ subsets: ["latin"] });
const spaceGrotesk = Space_Grotesk({ subsets: ["latin"], variable: "--font-space" });

export const metadata: Metadata = {
    title: "VectorBox",
    description: "Advanced AI Movie Recommendations",
    icons: {
        icon: "/icon.png",
    },
};

export default function RootLayout({
    children,
}: Readonly<{
    children: React.ReactNode;
}>) {
    return (
        <html lang="en" suppressHydrationWarning>
            <body className={`${inter.className} ${spaceGrotesk.variable} antialiased min-h-screen bg-background text-foreground overflow-x-hidden selection:bg-primary selection:text-black`}>
                <LanguageProvider>
                    <Providers>
                        {children}
                    </Providers>
                </LanguageProvider>
            </body>
        </html>
    );
}
