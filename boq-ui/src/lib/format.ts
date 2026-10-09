const qtyFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 });
const intFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const moneyFmt = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const rateFmt = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 4 });
const compactMoney = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 2 });

/** 100000 → "1,00,000" (Indian grouping, as written in the sketch). */
export const fmtQty = (v: number | null | undefined) => (v == null ? "—" : qtyFmt.format(v));
export const fmtInt = (v: number | null | undefined) => (v == null ? "—" : intFmt.format(v));
export const fmtMoney = (v: number | null | undefined) => (v == null ? "—" : moneyFmt.format(v));
export const fmtRate = (v: number | null | undefined) => (v == null ? "—" : rateFmt.format(v));
export const fmtMoneyShort = (v: number | null | undefined) => (v == null ? "—" : compactMoney.format(v));
export const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(1)}%`);

export function fmtDate(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v.length === 10 ? `${v}T00:00:00` : v);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function fmtDateTime(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function fmtCell(v: unknown, type: string): string {
  if (v == null || v === "") return "—";
  switch (type) {
    case "qty":
      return fmtQty(Number(v));
    case "int":
      return fmtInt(Number(v));
    case "money":
      return fmtMoney(Number(v));
    case "rate":
      return fmtRate(Number(v));
    case "pct":
      return fmtPct(Number(v));
    case "date":
      return fmtDate(String(v));
    case "datetime":
      return fmtDateTime(String(v));
    default:
      return String(v);
  }
}

export const humanize = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase()).replace(/\bpm\b/i, "PM");

export const todayIso = () => {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
};
