import type { Metadata } from "next";
import StrategyStudio from "@/components/StrategyStudio";

export const metadata: Metadata = { title: "TickerSage Strategy Studio" };

export default function StrategyPage() {
  return <StrategyStudio />;
}
