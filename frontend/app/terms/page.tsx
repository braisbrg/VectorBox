import type { Metadata } from "next";
import { LegalShell, LegalBox, type LegalSection } from "@/components/legal";

export const metadata: Metadata = {
    title: "Terms of Service - VectorBox",
    description: "Terms and conditions for using the VectorBox movie recommendation service.",
};

const P = "text-sm leading-relaxed text-fg-2";
const UL = "list-inside list-disc space-y-3 text-sm leading-relaxed text-fg-2";
const B = "text-fg";

const SECTIONS: LegalSection[] = [
    {
        id: "legal-notice",
        title: "Legal Notice & Acceptance",
        body: (
            <>
                <p className={`${P} mb-4`}>
                    In accordance with the Spanish Law on Information Society Services (LSSI-CE), the following
                    identifies the operator of this service:
                </p>
                <LegalBox>
                    <p><span className="text-fg-3">Operator:</span> VectorBox Project</p>
                    <p><span className="text-fg-3">Nature:</span> Non-commercial personal project</p>
                    <p><span className="text-fg-3">Country:</span> Spain</p>
                    <p><span className="text-fg-3">Contact:</span> vectorbox.app@proton.me</p>
                </LegalBox>
                <p className={`${P} mt-4`}>
                    By accessing or using VectorBox, you agree to be bound by these Terms of Service. If you do
                    not agree, you must not use the service. VectorBox is intended for users aged 14 and over.
                </p>
            </>
        ),
    },
    {
        id: "service",
        title: "The Service",
        body: (
            <p className={P}>
                VectorBox provides AI-powered, personalized movie recommendations based on your viewing history
                and preferences. The service is currently offered in beta and is provided on an &quot;as is&quot;
                basis without warranties of any kind, express or implied.
            </p>
        ),
    },
    {
        id: "accounts",
        title: "Accounts",
        body: (
            <ul className={UL}>
                <li>
                    You are responsible for maintaining the security of your account credentials. Authentication
                    is managed by Clerk, a third-party identity provider.
                </li>
                <li>One account per person. Duplicate or shared accounts may be suspended.</li>
                <li>
                    We reserve the right to suspend or terminate accounts that violate these terms without prior
                    notice.
                </li>
            </ul>
        ),
    },
    {
        id: "acceptable-use",
        title: "Acceptable Use",
        body: (
            <>
                <p className={`${P} mb-3`}>You agree not to:</p>
                <ul className={UL}>
                    <li>Engage in automated scraping, crawling, or mass data extraction from VectorBox.</li>
                    <li>
                        Attempt to access data belonging to other users or circumvent authentication mechanisms.
                    </li>
                    <li>
                        Conduct brute-force attacks, denial-of-service attacks, or any other activity that
                        disrupts the service.
                    </li>
                    <li>Use the service for any unlawful purpose or in violation of applicable laws.</li>
                </ul>
            </>
        ),
    },
    {
        id: "letterboxd-data",
        title: "Letterboxd Data",
        body: (
            <p className={P}>
                VectorBox allows you to import your own movie rating data from Letterboxd via CSV export or RSS
                feed. VectorBox is not affiliated with, endorsed by, or connected to Letterboxd Ltd. You are
                solely responsible for ensuring that you have the right to import and use your Letterboxd data
                within VectorBox.
            </p>
        ),
    },
    {
        id: "data-sources",
        title: "Data Sources & Attribution",
        body: (
            <>
                <p className={`${P} mb-4`}>
                    VectorBox is built on data generously provided by third-party services:
                </p>
                <LegalBox>
                    <p className="mb-2">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src="/tmdb.svg" alt="TMDB" className="mb-2 h-3" />
                        This product uses the TMDB API but is not endorsed or certified by{" "}
                        <a href="https://www.themoviedb.org" className="underline hover:text-primary" target="_blank" rel="noreferrer">TMDB</a>.
                    </p>
                    <p className="mb-2">
                        Streaming availability data is provided by{" "}
                        <a href="https://www.justwatch.com" className="underline hover:text-primary" target="_blank" rel="noreferrer">JustWatch</a>{" "}
                        via the TMDB API.
                    </p>
                    <p className="mb-2">
                        Ratings data is sourced from the{" "}
                        <a href="https://www.omdbapi.com" className="underline hover:text-primary" target="_blank" rel="noreferrer">OMDb API</a>,
                        licensed under{" "}
                        <a href="https://creativecommons.org/licenses/by-nc/4.0/" className="underline hover:text-primary" target="_blank" rel="noreferrer">CC BY-NC 4.0</a>.
                    </p>
                    <p>
                        We gratefully acknowledge{" "}
                        <a href="https://letterboxd.com" className="underline hover:text-primary" target="_blank" rel="noreferrer">Letterboxd</a>{" "}
                        as the source of user-initiated rating imports (see the Letterboxd Data section above).
                        VectorBox is not affiliated with any of these services, and will remove or adjust any
                        integration upon request from the respective rights holder.
                    </p>
                </LegalBox>
            </>
        ),
    },
    {
        id: "ip",
        title: "Intellectual Property",
        body: (
            <p className={P}>
                Movie metadata (titles, posters, synopses, cast) is sourced from The Movie Database (TMDB).
                VectorBox does not claim ownership of third-party movie metadata. The VectorBox source code is
                released under the MIT License. The VectorBox name, logo, and design are the property of their
                respective creators.
            </p>
        ),
    },
    {
        id: "liability",
        title: "Limitation of Liability",
        body: (
            <p className={P}>
                During the beta period, VectorBox does not guarantee continuous, uninterrupted availability of
                the service. We are not liable for the accuracy, relevance, or completeness of any movie
                recommendations. To the maximum extent permitted by law, VectorBox shall not be liable for any
                indirect, incidental, or consequential damages arising from the use of the service. VectorBox
                also assumes no liability for service interruptions caused by third-party infrastructure
                providers, including but not limited to Groq, OpenAI, The Movie Database (TMDB), Qdrant, or
                cloud hosting providers.
            </p>
        ),
    },
    {
        id: "governing-law",
        title: "Governing Law",
        body: (
            <p className={P}>
                These terms are governed by and construed in accordance with the laws of Spain. Any disputes
                arising from these terms shall be subject to the exclusive jurisdiction of the courts of Spain.
            </p>
        ),
    },
    {
        id: "contact",
        title: "Contact",
        body: (
            <p className={P}>
                For questions about these terms, contact us at <span className={B}>vectorbox.app@proton.me</span>.
            </p>
        ),
    },
];

export default function TermsPage() {
    return (
        <LegalShell
            title="Terms of Service"
            updated="July 14, 2026"
            sibling={{ href: "/privacy", label: "Privacy Policy" }}
            sections={SECTIONS}
        />
    );
}
