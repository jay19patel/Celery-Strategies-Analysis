import { Activity, Bell, Bot, Crosshair, Layers, LineChart, ListChecks, Radar, Settings, Sparkles, type LucideIcon } from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** The icon tile's colour, as in the iOS Settings list. */
  tint: string;
}

export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Trading",
    items: [
      { href: "/strategies", label: "Strategies", icon: Crosshair, tint: "bg-[#ff9500]" },
      { href: "/auto", label: "Option Decisions", icon: Bot, tint: "bg-[#34c759]" },
      { href: "/desk", label: "Options Desk", icon: Sparkles, tint: "bg-[#007aff]" },
      { href: "/market", label: "Market Desk", icon: Radar, tint: "bg-[#5856d6]" },
    ],
  },
  {
    group: "Portfolio",
    items: [
      { href: "/performance", label: "Performance", icon: LineChart, tint: "bg-[#34c759]" },
      { href: "/positions", label: "Positions", icon: Layers, tint: "bg-[#30b0c7]" },
      { href: "/orders", label: "Orders", icon: ListChecks, tint: "bg-[#af52de]" },
      { href: "/notifications", label: "Notifications", icon: Bell, tint: "bg-[#ff2d55]" },
    ],
  },
  {
    group: "Admin",
    items: [
      { href: "/system", label: "System", icon: Activity, tint: "bg-[#8e8e93]" },
      { href: "/settings", label: "Settings", icon: Settings, tint: "bg-[#8e8e93]" },
    ],
  },
];

export const ALL_NAV = NAV.flatMap((g) => g.items);

export const isActive = (pathname: string, href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));
