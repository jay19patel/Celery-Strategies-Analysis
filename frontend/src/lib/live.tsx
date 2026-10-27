"use client";

// The live connection: one WebSocket to the engine for the whole app. Prices update in place;
// every other event refreshes the data it changes (through SWR) and the important ones toast.

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import useSWR, { useSWRConfig, type SWRConfiguration } from "swr";
import { toast } from "sonner";

import { fetcher } from "@/lib/api";
import { signed } from "@/lib/format";
import type { LiveEvent, LivePrice } from "@/lib/types";

type Prices = Record<string, LivePrice>;

interface Live {
  connected: boolean;
  prices: Prices;
  events: LiveEvent[];
}

const LiveContext = createContext<Live>({ connected: false, prices: {}, events: [] });

// Which cached reads each event makes stale.
const REFRESH: Record<string, string[]> = {
  StructureOpened: ["/trading", "/portfolio", "/auto"],
  StructureClosed: ["/trading", "/portfolio", "/auto"],
  StructureFailed: ["/trading", "/portfolio"],
  StructureError: ["/trading", "/portfolio"],
  RiskHalt: ["/trading", "/portfolio", "/auto"],
  RiskResumed: ["/portfolio", "/auto"],
  KillSwitch: ["/trading", "/portfolio"],
  AutoDecision: ["/auto", "/portfolio/journal"],
  AiBrief: ["/ai"],
  SettingsChanged: ["/settings", "/auto", "/portfolio", "/ai", "/market", "/strategies"],
  Signal: ["/strategies", "/trading", "/portfolio"],
  MarketAnalysis: ["/desk"],
  PaperReset: ["/portfolio", "/trading"],
};

function wsUrl(): string {
  const configured = process.env.NEXT_PUBLIC_WS_URL;
  if (configured) return configured;
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

function announce(e: LiveEvent) {
  const d = e.data;
  switch (e.type) {
    case "StructureOpened":
      toast.success("Structure opened", { description: e.message });
      break;
    case "StructureClosed": {
      const pnl = typeof d.pnl === "number" ? d.pnl : null;
      (pnl !== null && pnl < 0 ? toast.warning : toast.success)(`Closed ${signed(pnl)}`, { description: e.message });
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
  }
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const { mutate } = useSWRConfig();
  const [connected, setConnected] = useState(false);
  const [prices, setPrices] = useState<Prices>({});
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const retry = useRef(1000);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const connect = () => {
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        setConnected(false);
        if (!stopped) {
          timer = setTimeout(connect, retry.current);
          retry.current = Math.min(retry.current * 2, 30_000);
        }
        return;
      }
      ws.onopen = () => {
        setConnected(true);
        retry.current = 1000;
      };
      ws.onmessage = (msg) => {
        let e: LiveEvent;
        try {
          e = JSON.parse(msg.data) as LiveEvent;
        } catch {
          return;
        }
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
        if (stopped) return;
        timer = setTimeout(connect, retry.current);
        retry.current = Math.min(retry.current * 2, 30_000);
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
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
