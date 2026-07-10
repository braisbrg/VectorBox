import {
    LayoutList,
    Film,
    Orbit,
    Users,
    Bookmark,
    Settings,
    Sparkles,
    UserCircle,
    type LucideIcon,
} from "lucide-react";

export interface NavItem {
    href: string;
    /** i18n key (must exist in both locales — wave-C parity enforced). */
    key: string;
    icon: LucideIcon;
}

// Primary shell navigation (handoff sidebar: feed · similars · space · groups · watchlist · magic box).
export const PRIMARY_NAV: NavItem[] = [
    { href: "/feed", key: "sidebar.feed", icon: LayoutList },
    { href: "/mlt", key: "sidebar.more_like_this", icon: Film },
    { href: "/space", key: "sidebar.space", icon: Orbit },
    { href: "/grp", key: "sections.group_vibe", icon: Users },
    { href: "/watch", key: "sidebar.watchlist", icon: Bookmark },
    { href: "/mb", key: "sidebar.magic_box", icon: Sparkles },
];

export const SETTINGS_NAV: NavItem = {
    href: "/set",
    key: "sidebar.settings",
    icon: Settings,
};

export const YOU_NAV: NavItem = {
    href: "/you",
    key: "sidebar.profile",
    icon: UserCircle,
};

/** Route lookup — never index PRIMARY_NAV positionally. */
const byHref = (href: string): NavItem => {
    const item = PRIMARY_NAV.find((i) => i.href === href);
    if (!item) throw new Error(`nav: no PRIMARY_NAV item for ${href}`);
    return item;
};

/** Mobile bottom-tab subset (handoff: feed · similars · [magic] · groups · you). */
export const MOBILE_TABS: NavItem[] = [byHref("/feed"), byHref("/mlt"), byHref("/grp"), YOU_NAV];

/** Sub-screens where the bottom tab bar is hidden (handoff: back-rows instead). */
export const MOBILE_SUBSCREENS = ["/watch", "/set", "/space", "/movie", "/why", "/import"];

export function navLabel(t: (k: string) => string, item: NavItem): string {
    return t(item.key);
}

/** Active when the pathname is the route or a child of it. */
export function isActive(pathname: string, href: string): boolean {
    return pathname === href || pathname.startsWith(href + "/");
}
