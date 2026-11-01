"use client";

import { useSyncExternalStore } from "react";

// Canvas charts cannot read CSS variables, so their neutrals live here, one set per appearance.
const QUERY = "(prefers-color-scheme: dark)";

const LIGHT = {
  text: "#6e6e73",
  grid: "rgba(60, 60, 67, 0.08)",
  border: "#e3e3e8",
  crosshair: "#aeaeb2",
  labelBg: "rgba(255, 255, 255, 0.92)",
};

const DARK: ChartTheme = {
  text: "#98989f",
  grid: "rgba(235, 235, 245, 0.07)",
  border: "#38383a",
  crosshair: "#636366",
  labelBg: "rgba(28, 28, 30, 0.92)",
};

export type ChartTheme = typeof LIGHT;

export const CHART_FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif';

/** The colours for the appearance right now: for code that runs once, such as creating a chart. */
export function chartTheme(): ChartTheme {
  return typeof window !== "undefined" && window.matchMedia(QUERY).matches ? DARK : LIGHT;
}

function subscribe(onChange: () => void) {
  const m = window.matchMedia(QUERY);
  m.addEventListener("change", onChange);
  return () => m.removeEventListener("change", onChange);
}

/** The colours for the appearance, re-rendering when the system switches between light and dark. */
export function useChartTheme(): ChartTheme {
  const dark = useSyncExternalStore(subscribe, () => window.matchMedia(QUERY).matches, () => false);
  return dark ? DARK : LIGHT;
}

/** The lightweight-charts options that carry the appearance. */
export function chartThemeOptions(t: ChartTheme) {
  return {
    layout: { textColor: t.text },
    grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
    rightPriceScale: { borderColor: t.border },
    timeScale: { borderColor: t.border },
    crosshair: { vertLine: { color: t.crosshair }, horzLine: { color: t.crosshair } },
  };
}
