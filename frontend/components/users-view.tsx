"use client";

import { UploadZone } from "@/components/upload-zone";
import { User } from "@/lib/api";
import { useLanguage } from "@/components/language-provider";

interface UsersViewProps {
    users: User[];
    currentUserId: number | null;
    onUserSelect: (id: number) => void;
    onUserCreated: (user: User) => void;
    onUploadSuccess: (userId: number) => void;
}

export function UsersView({
    users,
    currentUserId,
    onUserSelect,
    onUserCreated,
    onUploadSuccess
}: UsersViewProps) {
    const { t } = useLanguage();

    return (
        <div className="max-w-4xl mx-auto space-y-8">
            <div className="text-center mb-8">
                <h2 className="text-3xl font-bold mb-2">{t("users.title")}</h2>
                <p className="text-muted-foreground">
                    {t("users.subtitle")}
                </p>
            </div>

            <div className="bg-card border rounded-xl p-8 shadow-sm">
                <UploadZone
                    users={users}
                    currentUserId={currentUserId}
                    onUserSelect={onUserSelect}
                    onUserCreated={onUserCreated}
                    onUploadSuccess={onUploadSuccess}
                />
            </div>

            <div className="grid md:grid-cols-2 gap-6">
                <div className="bg-muted/30 p-6 rounded-lg border">
                    <h3 className="font-semibold mb-2">{t("users.why.title")}</h3>
                    <p className="text-sm text-muted-foreground">
                        {t("users.why.desc")}
                    </p>
                </div>
                <div className="bg-muted/30 p-6 rounded-lg border">
                    <h3 className="font-semibold mb-2">{t("users.how.title")}</h3>
                    <p className="text-sm text-muted-foreground">
                        {t("users.how.desc")}
                    </p>
                </div>
            </div>
        </div>
    );
}
