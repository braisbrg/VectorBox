import type { Metadata } from "next";
import { LegalShell, LegalBox, type LegalSection } from "@/components/legal";

export const metadata: Metadata = {
    title: "Privacy Policy - VectorBox",
    description: "How VectorBox handles your data, cookies, and GDPR rights.",
};

const P = "text-sm leading-relaxed text-fg-2";
const MUTED = "text-sm leading-relaxed text-fg-3";
const UL = "list-inside list-disc space-y-3 text-sm leading-relaxed text-fg-2";
const B = "text-fg";

const SECTIONS: LegalSection[] = [
    {
        id: "controller",
        title: "Data Controller",
        body: (
            <>
                <p className={`${P} mb-4`}>
                    VectorBox is a personal, non-commercial project developed and operated by{" "}
                    <span className={B}>VectorBox Project</span>, based in Spain.
                </p>
                <p className={`${P} mb-4`}>
                    VectorBox is an open-source, non-commercial project and does not engage in any commercial
                    activity. No goods or services are sold through this platform.
                </p>
                <LegalBox>
                    <p><span className="text-fg-3">Entity:</span> VectorBox Project</p>
                    <p><span className="text-fg-3">Country:</span> Spain</p>
                    <p><span className="text-fg-3">Contact:</span> vectorbox.app@proton.me</p>
                </LegalBox>
            </>
        ),
    },
    {
        id: "legal-basis",
        title: "Legal Basis for Processing",
        body: (
            <>
                <p className={`${P} mb-3`}>
                    We process your personal data under the following legal bases (Article 6 GDPR):
                </p>
                <ul className={UL}>
                    <li>
                        <strong className={B}>Performance of a contract</strong> - Processing your username, PIN,
                        and preferences is necessary to provide the VectorBox recommendation service you have
                        registered for.
                    </li>
                    <li>
                        <strong className={B}>Explicit consent</strong> - When you upload a Letterboxd CSV export
                        or link your RSS feed, you explicitly consent to VectorBox importing and processing that
                        data to generate recommendations.
                    </li>
                </ul>
            </>
        ),
    },
    {
        id: "data-collected",
        title: "What Data We Collect",
        body: (
            <>
                <ul className={UL}>
                    <li>
                        <strong className={B}>Account identity</strong> - Authentication is handled by Clerk (a
                        third-party identity provider). VectorBox stores your Clerk user ID and display name. We
                        do not store passwords - authentication credentials are managed entirely by Clerk.
                    </li>
                    <li>
                        <strong className={B}>Movie ratings</strong> - Imported from your Letterboxd account via
                        CSV export or RSS feed. These ratings power your personalized recommendations.
                    </li>
                    <li>
                        <strong className={B}>Streaming preferences</strong> - Your selected streaming platforms
                        (e.g., Netflix, HBO Max) used to filter recommendations by availability.
                    </li>
                    <li>
                        <strong className={B}>Country code</strong> - Used to determine regional streaming
                        availability for recommended titles.
                    </li>
                    <li>
                        <strong className={B}>Minimum age</strong> - VectorBox is intended for users aged 14 and
                        over, in accordance with Spanish data protection law (LOPDGDD Art. 7). By registering,
                        you confirm that you meet this requirement.
                    </li>
                </ul>
                <p className={`${MUTED} mt-4 italic`}>
                    Note: Letterboxd CSV exports may contain an email field. This field is discarded immediately
                    in memory during import and is never written to our database.
                </p>
            </>
        ),
    },
    {
        id: "data-use",
        title: "How We Use Your Data",
        body: (
            <ul className={UL}>
                <li>
                    Generate personalized movie recommendations using semantic vector embeddings and K-Medoids
                    clustering over your rating history.
                </li>
                <li>
                    Improve feed relevance through our Trident recommendation engine, which combines
                    collaborative filtering, taste profiling, and hidden gem discovery.
                </li>
                <li>We do not sell, share, or transfer your personal data to any third parties.</li>
            </ul>
        ),
    },
    {
        id: "cookies",
        title: "Cookies",
        body: (
            <>
                <p className={`${P} mb-3`}>VectorBox uses a single, strictly necessary cookie:</p>
                <LegalBox>
                    <p><span className="text-fg-3">Name:</span> __session, __clerk_* (set by Clerk)</p>
                    <p><span className="text-fg-3">Type:</span> HttpOnly, SameSite=Lax, Secure (production)</p>
                    <p><span className="text-fg-3">Purpose:</span> Session authentication via Clerk</p>
                    <p><span className="text-fg-3">Duration:</span> Session-based (managed by Clerk)</p>
                </LegalBox>
                <p className={`${MUTED} mt-4`}>
                    These cookies are used exclusively to maintain your authenticated session via Clerk. They are
                    not tracking or analytics cookies. As strictly necessary cookies, they are exempt from consent
                    requirements under GDPR Article 5(3) of the ePrivacy Directive.
                </p>
            </>
        ),
    },
    {
        id: "transfers",
        title: "International Data Transfers",
        body: (
            <>
                <p className={`${P} mb-3`}>
                    To provide the Natural Language Search feature, VectorBox sends anonymised query data to the
                    following third-party AI providers located outside the European Economic Area:
                </p>
                <ul className="list-inside list-disc space-y-2 text-sm leading-relaxed text-fg-2">
                    <li>
                        <strong className={B}>Clerk, Inc.</strong> (United States) - Used for user authentication
                        and session management.
                    </li>
                    <li>
                        <strong className={B}>Groq, Inc.</strong> (United States) - Used for LLM-based query
                        parsing and cinematic description generation.
                    </li>
                    <li>
                        <strong className={B}>OpenAI, LLC</strong> (United States) - Used as fallback LLM provider.
                    </li>
                </ul>
                <p className={`${MUTED} mt-4`}>
                    These transfers are governed by Standard Contractual Clauses (SCCs) as established by the
                    European Commission, and both providers participate in the EU-US Data Privacy Framework. Only
                    the text of your search query is transmitted - no personal identifiers, usernames, or rating
                    history are shared with these providers.
                </p>
                <p className={`${MUTED} mt-3`}>
                    For details on how Clerk handles your authentication data, see{" "}
                    <a
                        href="https://clerk.com/legal/privacy"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-fg underline hover:text-primary"
                    >
                        Clerk&apos;s Privacy Policy
                    </a>
                    .
                </p>
            </>
        ),
    },
    {
        id: "retention",
        title: "Data Retention",
        body: (
            <p className={P}>
                Your data is retained for as long as your account remains active. You may request complete
                deletion of your account and all associated data by contacting{" "}
                <span className={B}>vectorbox.app@proton.me</span>. Upon receiving a valid deletion request, we
                will erase all personal data within 30 days.
            </p>
        ),
    },
    {
        id: "rights",
        title: "Your Rights (GDPR)",
        body: (
            <>
                <p className={`${P} mb-3`}>
                    Under the General Data Protection Regulation, you have the right to:
                </p>
                <ul className="list-inside list-disc space-y-2 text-sm leading-relaxed text-fg-2">
                    <li><strong className={B}>Access</strong> - Request a copy of the personal data we hold about you.</li>
                    <li><strong className={B}>Rectification</strong> - Correct any inaccurate personal data.</li>
                    <li><strong className={B}>Erasure</strong> - Request deletion of your personal data.</li>
                    <li><strong className={B}>Portability</strong> - Receive your data in a structured, machine-readable format.</li>
                    <li><strong className={B}>Restriction</strong> - Request that we restrict the processing of your personal data under certain circumstances.</li>
                    <li><strong className={B}>Objection</strong> - Object to the processing of your personal data.</li>
                </ul>
                <p className={`${MUTED} mt-4`}>
                    Supervisory authority: Agencia Española de Protección de Datos (AEPD), Spain.
                </p>
            </>
        ),
    },
    {
        id: "changes",
        title: "Changes to This Policy",
        body: (
            <p className={P}>
                We reserve the right to update this privacy policy at any time. Material changes will be
                communicated through the service. We encourage you to review this page periodically.
            </p>
        ),
    },
];

export default function PrivacyPage() {
    return (
        <LegalShell
            title="Privacy Policy"
            updated="May 4, 2026"
            sibling={{ href: "/terms", label: "Terms of Service" }}
            sections={SECTIONS}
        />
    );
}
