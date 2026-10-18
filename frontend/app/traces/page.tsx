import type { Metadata } from "next";
import TracesView from "@/components/TracesView";

export const metadata: Metadata = { title: "TickerSage Traces" };

export default function TracesPage() {
  return <TracesView />;
}
