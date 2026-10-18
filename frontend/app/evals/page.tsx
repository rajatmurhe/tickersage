import type { Metadata } from "next";
import EvalsView from "@/components/EvalsView";

export const metadata: Metadata = { title: "TickerSage Evals" };

export default function EvalsPage() {
  return <EvalsView />;
}
