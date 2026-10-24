"use client";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

export type BrokerFilter = "" | "paper" | "delta";

export function BrokerTabs({ value, onChange }: { value: BrokerFilter; onChange: (v: BrokerFilter) => void }) {
  return (
    <Tabs value={value || "all"} onValueChange={(v) => onChange(v === "all" ? "" : (v as BrokerFilter))}>
      <TabsList>
        <TabsTrigger value="all">All</TabsTrigger>
        <TabsTrigger value="paper">Paper</TabsTrigger>
        <TabsTrigger value="delta">Delta</TabsTrigger>
      </TabsList>
    </Tabs>
  );
}
