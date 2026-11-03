// The only place the frontend reads the current time (spec section 12).
import type { PartialDate } from "./contracts";

export function todayUTC(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10);
}

export function ageDays(isoDate: string, today: string): number {
  const ms = Date.parse(`${today}T00:00:00Z`) - Date.parse(`${isoDate}T00:00:00Z`);
  return Math.round(ms / 86_400_000);
}

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
  "September", "October", "November", "December"];

export function formatPublished(d: PartialDate): string {
  switch (d.precision) {
    case "day":
      return `${d.day} ${MONTHS[(d.month ?? 1) - 1]} ${d.year}`;
    case "month":
      return `${MONTHS[(d.month ?? 1) - 1]} ${d.year} · month only`;
    case "year":
      return `${d.year} · publication year only`;
    default:
      return "Date unknown";
  }
}

export function isApproximate(d: PartialDate): boolean {
  return d.precision !== "day";
}
