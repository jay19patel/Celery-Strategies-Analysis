import { Activity, Bot, CalendarDays, Crosshair, Layers, LineChart, ListChecks, Radar, Settings, Sparkles, Zap, type LucideIcon } from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Trading",
    items: [
      { href: "/", label: "Options Desk", icon: Sparkles },
      { href: "/market", label: "Market Desk", icon: Radar },
      { href: "/strategies", label: "Strategies", icon: Crosshair },
      { href: "/auto", label: "Auto Options", icon: Bot },
      { href: "/zero-hero", label: "Zero-Hero", icon: Zap },
      { href: "/positions", label: "Positions", icon: Layers },
    ],
  },
  {
    group: "Portfolio",
    items: [
      { href: "/performance", label: "Performance", icon: LineChart },
      { href: "/journal", label: "Journal", icon: CalendarDays },
      { href: "/orders", label: "Orders", icon: ListChecks },
    ],
  },
  {
    group: "Admin",
    items: [
      { href: "/system", label: "System", icon: Activity },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

export const ALL_NAV = NAV.flatMap((g) => g.items);
