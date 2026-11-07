"use client";

// The live connection: one WebSocket to the engine for the whole app. Prices update in place;
// every other event refreshes the data it changes (through SWR) and the important ones toast.

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import useSWR, { useSWRConfig, type SWRConfiguration } from "swr";
import { toast } from "sonner";

import { api, backoff, fetcher } from "@/lib/api";
import { arrowSigned } from "@/lib/format";
import type { LiveEvent, LivePrice } from "@/lib/types";

type Prices = Record<string, LivePrice>;

interface Live {
  connected: boolean;
  prices: Prices;
  events: LiveEvent[];
}

const LiveContext = createContext<Live>({ connected: false, prices: {}, events: [] });

// Prices arrive every 2s: a socket silent this long is dead even if the browser has not noticed.
const SILENT_MS = 15_000;

// Which cached reads each event makes stale.
const REFRESH: Record<string, string[]> = {
  StructureOpened: ["/trading", "/portfolio", "/auto", "/notifications"],
  StopTrailed: ["/trading"],
  StructureClosed: ["/trading", "/portfolio", "/auto", "/notifications"],
  StructureFailed: ["/trading", "/portfolio", "/notifications"],
  StructureError: ["/trading", "/portfolio", "/notifications"],
  RiskHalt: ["/trading", "/portfolio", "/auto", "/notifications"],
  RiskResumed: ["/portfolio", "/auto", "/notifications"],
  KillSwitch: ["/trading", "/portfolio", "/notifications"],
  AutoDecision: ["/auto", "/portfolio/journal"],
  ZeroHeroDecision: ["/zerohero", "/portfolio/journal"],
  AiBrief: ["/ai"],
  SettingsChanged: ["/settings", "/auto", "/zerohero", "/portfolio", "/ai", "/market", "/strategies"],
  Signal: ["/strategies", "/trading", "/portfolio"],
  MarketAnalysis: ["/desk"],
  PaperReset: ["/portfolio", "/trading"],
  DataReset: ["/portfolio", "/trading", "/auto", "/zerohero", "/strategies", "/system", "/ai", "/market", "/desk", "/notifications"],
};

async function wsUrl(): Promise<string> {
  const configured = process.env.NEXT_PUBLIC_WS_URL;
  if (configured) return configured;
  // The dashboard's /api is proxied, but /ws is not, and the session cookie belongs to the
  // dashboard's host: the backend says where its socket is and hands a one-time ticket into it.
  const { url, ticket } = await api<{ url: string; ticket: string }>("/auth/ws");
  return `${url}?ticket=${encodeURIComponent(ticket)}`;
}

function announce(e: LiveEvent) {
  const d = e.data;
  switch (e.type) {
    case "StructureOpened":
      toast.success("Structure opened", { description: e.message });
      break;
    case "StructureClosed": {
      const pnl = typeof d.pnl === "number" ? d.pnl : null;
      (pnl !== null && pnl < 0 ? toast.warning : toast.success)(`Closed ${arrowSigned(pnl)}`, { description: e.message });
      break;
    }
    case "StructureFailed":
    case "RiskHalt":
    case "KillSwitch":
      toast.warning(e.type === "RiskHalt" ? "Daily loss limit" : e.type === "KillSwitch" ? "Close all" : "Not opened", { description: e.message });
      break;
    case "StructureError":
      toast.error("Needs attention", { description: e.message, duration: 15_000 });
      break;
    case "AutoDecision":
      if (d.action === "open") toast.info("Auto-trader opened a structure", { description: e.message });
      if (d.action === "error") toast.error("Auto-trader error", { description: e.message });
      break;
    case "ZeroHeroDecision":
      if (d.action === "open") toast.info("Zero-hero traded", { description: e.message });
      if (d.action === "error") toast.error("Zero-hero error", { description: e.message });
      break;
  }
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const { mutate } = useSWRConfig();
  const [connected, setConnected] = useState(false);
  const [prices, setPrices] = useState<Prices>({});
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const attempt = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let heard = 0;
    let opened = false;
    const retry = () => {
      timer = setTimeout(connect, backoff(attempt.current));
      attempt.current += 1;
    };
    const watchdog = setInterval(() => {
      if (ws?.readyState === WebSocket.OPEN && Date.now() - heard > SILENT_MS) ws.close();
    }, 5_000);

    const connect = async () => {
      try {
        const url = await wsUrl();
        if (stopped) return;
        ws = new WebSocket(url);
      } catch {
        setConnected(false);
        if (!stopped) retry();
        return;
      }
      ws.onopen = () => {
        setConnected(true);
        heard = Date.now();
        attempt.current = 0;
        // Events sent while the socket was down are gone: every cached read is refetched once.
        if (opened) void mutate(() => true);
        opened = true;
      };
      ws.onmessage = (msg) => {
        let e: LiveEvent;
        try {
          e = JSON.parse(msg.data) as LiveEvent;
        } catch {
          return;
        }
        heard = Date.now();
        if (e.type === "Prices") {
          setPrices((e.data.prices ?? {}) as Prices);
          return;
        }
        if (e.type === "Hello") return;
        setEvents((prev) => [e, ...prev].slice(0, 100));
        const prefixes = REFRESH[e.type];
        if (prefixes) mutate((key) => typeof key === "string" && prefixes.some((p) => key.startsWith(p)));
        announce(e);
      };
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) retry();
      };
      ws.onerror = () => ws?.close();
    };
    void connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearInterval(watchdog);
      ws?.close();
    };
  }, [mutate]);

  return <LiveContext.Provider value={{ connected, prices, events }}>{children}</LiveContext.Provider>;
}

export function useLive(): Live {
  return useContext(LiveContext);
}

/** A backend read, cached and shared by key. Pass null to skip. */
export function useApi<T>(path: string | null, config?: SWRConfiguration<T>) {
  return useSWR<T>(path, fetcher, { revalidateOnFocus: true, keepPreviousData: true, ...config });
}
