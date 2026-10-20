import type { Metadata } from "next";
import ReportView from "@/components/ReportView";

export const metadata: Metadata = { title: "TickerSage research reports" };

export default function ReportPage() {
  return <ReportView />;
}
