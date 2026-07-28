"use client";

import { useEffect, useState } from "react";
import { Wordmark, TridentMark } from "@/components/ui/wordmark";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronLeft, ChevronRight, LogOut, UserCircle, RotateCw } from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useUser } from "@clerk/nextjs";
import { AppTooltip } from "@/components/info-tooltip";
import { useLanguage } from "@/components/language-provider";
import { LanguageToggle } from "@/components/language-toggle";
import { syncRSS, getRssSyncStatus } from "@/lib/api";
import { useVectorboxLogout } from "@/hooks/useVectorboxLogout";
import { PRIMARY_NAV, SETTINGS_NAV, navLabel, isActive } from "@/components/shell/nav";
import { cn } from "@/lib/utils";

interface SidebarProps {
    collapsed: boolean;
    onToggleCollapse: () => void;
    letterboxdUsername?: string;
}

export function Sidebar({ collapsed, onToggleCollapse, letterboxdUsername }: SidebarProps) {
    const pathname = usePathname();
    const { t } = useLanguage();
    const handleLogout = useVectorboxLogout();
    const { user: clerkUser } = useUser();
    const displayName = clerkUser?.username || clerkUser?.firstName || clerkUser?.fullName || "User";

    return (
        <aside
            style={{ width: collapsed ? 56 : 184 }}
            className="fixed left-0 top-0 z-50 hidden h-screen flex-col border-r border-border-2 bg-bg transition-[width] duration-200 ease-out lg:flex"
        >
            {/* Header — wordmark + collapse toggle */}
            <div className="flex h-[60px] items-center justify-between border-b border-border-2 px-3">
                {/* The mark stays put in both states: the trident alone when there
                    is no room for the name. The old collapsed state invented a
                    "VEC|TBX" abbreviation that appeared nowhere else in the
                    product and read as a different brand. */}
                <Link
                    href="/feed"
                    className="font-display text-sm uppercase tracking-tight"
                    aria-label="VectorBox"
                >
                    {collapsed ? <TridentMark className="text-[18px]" /> : <Wordmark />}
                </Link>
                <button
                    onClick={onToggleCollapse}
                    aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                    className="flex size-8 items-center justify-center border border-transparent text-fg-3 transition-colors hover:border-primary hover:text-primary"
                >
                    {collapsed ? <ChevronRight className="size-4" /> : <ChevronLeft className="size-4" />}
                </button>
            </div>

            {/* Primary nav */}
            <nav className="flex-1 space-y-1 overflow-y-auto px-2 py-4 scrollbar-hide">
                {/* Prototype .sidebar nav: lowercase labels, 3px lime left edge + bg-3 when active, lime icon */}
                {PRIMARY_NAV.map((item) => {
                    const Icon = item.icon;
                    const active = isActive(pathname, item.href);
                    return (
                        <Link
                            key={item.href}
                            href={item.href}
                            title={collapsed ? navLabel(t, item) : undefined}
                            className={cn(
                                "flex items-center gap-2.5 border-l-[3px] py-2.5 pl-2 pr-3 font-mono text-xs lowercase transition-colors",
                                collapsed && "justify-center px-0",
                                active
                                    ? "border-primary bg-bg-3 text-fg"
                                    : "border-transparent text-fg-2 hover:bg-bg-3"
                            )}
                        >
                            <Icon className={cn("size-4 shrink-0", active ? "text-primary" : "text-fg-3")} />
                            {!collapsed && <span className="truncate">{navLabel(t, item)}</span>}
                        </Link>
                    );
                })}
            </nav>

            {/* Foot — RSS indicator · settings · account · lang · legal */}
            <div className="space-y-3 border-t border-border-2 p-2">
                <RssIndicator collapsed={collapsed} letterboxdUsername={letterboxdUsername} />

                <Link
                    href={SETTINGS_NAV.href}
                    title={collapsed ? navLabel(t, SETTINGS_NAV) : undefined}
                    className={cn(
                        "flex items-center gap-2.5 border-l-[3px] py-2 pl-2 pr-3 font-mono text-xs lowercase transition-colors",
                        collapsed && "justify-center px-0",
                        isActive(pathname, SETTINGS_NAV.href)
                            ? "border-primary bg-bg-3 text-fg"
                            : "border-transparent text-fg-3 hover:bg-bg-3 hover:text-fg"
                    )}
                >
                    <SETTINGS_NAV.icon className={cn("size-4 shrink-0", isActive(pathname, SETTINGS_NAV.href) ? "text-primary" : "text-fg-3")} />
                    {!collapsed && <span>{navLabel(t, SETTINGS_NAV)}</span>}
                </Link>

                {!collapsed && (
                    <DropdownMenu.Root>
                        <DropdownMenu.Trigger asChild>
                            <button className="flex w-full items-center gap-2 border border-border-2 bg-bg-2 px-2.5 py-2 text-xs text-fg-2 outline-none transition-colors hover:border-primary/50 hover:text-primary">
                                <span className="size-2 bg-primary" />
                                <span className="truncate font-mono uppercase tracking-wider">{displayName}</span>
                            </button>
                        </DropdownMenu.Trigger>
                        <DropdownMenu.Portal>
                            <DropdownMenu.Content
                                className="z-40 min-w-[160px] border border-border-2 bg-bg p-1 shadow-acid"
                                side="right"
                                align="end"
                                sideOffset={10}
                            >
                                <DropdownMenu.Item asChild>
                                    <Link
                                        href="/you"
                                        className="flex cursor-pointer items-center gap-2 px-3 py-2 font-mono text-xs uppercase tracking-wider text-fg-2 outline-none transition-colors hover:bg-primary hover:text-primary-ink"
                                    >
                                        <UserCircle className="size-4" />
                                        {navLabel(t, { ...SETTINGS_NAV, key: "sidebar.profile" })}
                                    </Link>
                                </DropdownMenu.Item>
                                <DropdownMenu.Separator className="my-1 h-px bg-border-2" />
                                <DropdownMenu.Item
                                    className="flex cursor-pointer items-center gap-2 px-3 py-2 font-mono text-xs uppercase tracking-wider text-danger outline-none transition-colors hover:bg-danger hover:text-bg"
                                    onClick={handleLogout}
                                >
                                    <LogOut className="size-4" />
                                    {t("app.logout")}
                                </DropdownMenu.Item>
                            </DropdownMenu.Content>
                        </DropdownMenu.Portal>
                    </DropdownMenu.Root>
                )}

                <div className={cn("flex items-center pt-1", collapsed ? "flex-col gap-3" : "justify-between")}>
                    <LanguageToggle isCollapsed={collapsed} />
                    <AppTooltip isCollapsed={collapsed} />
                </div>

                {!collapsed && (
                    <div className="flex items-center justify-center gap-2 pt-1">
                        <Link href="/privacy" className="text-[10px] uppercase tracking-wider text-fg-3 hover:text-primary">
                            Privacy
                        </Link>
                        <span className="text-[10px] text-border-2">·</span>
                        <Link href="/terms" className="text-[10px] uppercase tracking-wider text-fg-3 hover:text-primary">
                            Terms
                        </Link>
                    </div>
                )}
            </div>
        </aside>
    );
}

function relativeTime(iso: string): string {
    const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.round(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.round(hrs / 24)}d ago`;
}

function RssIndicator({ collapsed, letterboxdUsername }: { collapsed: boolean; letterboxdUsername?: string }) {
    const [syncing, setSyncing] = useState(false);
    const [lastSync, setLastSync] = useState<string | null>(null);

    useEffect(() => {
        setLastSync(localStorage.getItem("vb_last_rss_sync"));
    }, []);

    if (!letterboxdUsername) return null;

    const doSync = async () => {
        if (syncing) return;
        setSyncing(true);
        try {
            await syncRSS(letterboxdUsername);
            // Keep spinning until the background sync clears its in-progress flag
            // (so the button doesn't lie "synced" the instant the task is queued).
            const deadline = Date.now() + 5 * 60 * 1000;
            while (Date.now() < deadline) {
                await new Promise((r) => setTimeout(r, 2500));
                try {
                    const { syncing: still } = await getRssSyncStatus();
                    if (!still) break;
                } catch {
                    break;
                }
            }
            const now = new Date().toISOString();
            localStorage.setItem("vb_last_rss_sync", now);
            setLastSync(now);
        } catch (e) {
            console.error("RSS sync failed", e);
        } finally {
            setSyncing(false);
        }
    };

    return (
        <button
            onClick={doSync}
            title={collapsed ? "RSS sync" : undefined}
            className={cn(
                "flex w-full items-center gap-2 px-2.5 py-1.5 text-[10px] uppercase tracking-wider text-fg-3 transition-colors hover:text-primary",
                collapsed && "justify-center px-0"
            )}
        >
            <span className={cn("size-1.5 shrink-0 bg-primary", !syncing && "animate-pulse")} />
            {!collapsed && (
                <span className="flex-1 text-left">
                    RSS · {syncing ? "syncing…" : lastSync ? `synced ${relativeTime(lastSync)}` : "tap to sync"}
                </span>
            )}
            {!collapsed && <RotateCw className={cn("size-3", syncing && "animate-spin")} />}
        </button>
    );
}
