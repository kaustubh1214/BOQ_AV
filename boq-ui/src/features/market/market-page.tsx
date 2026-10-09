import { useQuery } from "@tanstack/react-query";
import { Plus, TrendingUp } from "lucide-react";
import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { type Column, DataList, Pager } from "@/components/common/data-list";
import { EmptyState, InfoGrid, PageHeader, Section, StatusBadge } from "@/components/common/page";
import { MasterSelect, MaterialPicker, type PickedMaterial } from "@/components/common/pickers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { get } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtRate, humanize, todayIso } from "@/lib/format";
import { post, useAction, useList } from "@/lib/hooks";
import type { MarketRate } from "@/lib/types";

interface Compare {
  material: { id: string; name: string; uom: string };
  benchmark: { quotes: number; last_rate: number | null; min_rate: number | null; max_rate: number | null; avg_rate: number | null; last_quoted_on: string | null };
  vendors: { vendor_id: string | null; vendor_name: string; rate: number | null; quoted_on: string; valid_till: string | null; source: string }[];
  trend: { quoted_on: string; rate: number | null; vendor_name: string | null }[];
}

export function MarketPage() {
  const { can } = useAuth();
  const [material, setMaterial] = useState<PickedMaterial | null>(null);
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const { data, isLoading } = useList<MarketRate>("market", "/market-rates", { material_id: material?.id, page, limit: 25 });

  const columns: Column<MarketRate>[] = [
    { key: "mat", header: "Material", cell: (r) => <div><p className="font-medium">{r.material.name}</p><p className="text-xs text-muted-foreground">{r.material.code}</p></div> },
    { key: "vendor", header: "Vendor / source", cell: (r) => r.vendor_name ?? humanize(r.source) },
    { key: "rate", header: "Rate", align: "right", cell: (r) => <span className="tabular-nums">{fmtRate(r.rate)} / {r.material.uom}</span> },
    { key: "date", header: "Quoted", cell: (r) => fmtDate(r.quoted_on) },
    { key: "src", header: "Source", cell: (r) => <StatusBadge status={r.source} /> },
  ];

  return (
    <>
      <PageHeader
        title="Market research"
        subtitle="Step 1 — vendor quotes and market rates that set the BOQ benchmark"
        actions={can("market.manage") && (
          <Button onClick={() => setAdding(true)}>
            <Plus /> Record rate
          </Button>
        )}
      />
      <div className="mb-4 grid gap-2 sm:max-w-md">
        <Field label="Compare a material">
          <MaterialPicker value={material} onChange={(m) => (setMaterial(m), setPage(1))} placeholder="All materials" />
        </Field>
        {material && (
          <button className="justify-self-start text-xs text-primary hover:underline" onClick={() => setMaterial(null)}>
            Clear filter
          </button>
        )}
      </div>
      {material && <ComparePanel materialId={material.id} />}
      <Card className="overflow-hidden">
        <DataList rows={data?.data} columns={columns} rowKey={(r) => r.id} loading={isLoading} empty={<EmptyState title="No rates recorded" hint="Record vendor quotes to build a benchmark for the BOQ." />} />
        <Pager page={page} totalPages={data?.meta.totalPages} total={data?.meta.total} onPage={setPage} />
      </Card>
      {adding && <RateForm onClose={() => setAdding(false)} initial={material} />}
    </>
  );
}

function ComparePanel({ materialId }: { materialId: string }) {
  const { data } = useQuery({ queryKey: ["market-compare", materialId], queryFn: () => get<Compare>("/market-rates/compare", { material_id: materialId }) });
  const c = data?.data;
  if (!c) return null;
  const b = c.benchmark;
  const trend = c.trend.filter((t) => t.rate != null);
  return (
    <div className="mb-4 grid gap-4 lg:grid-cols-2">
      <Section title={`Benchmark — ${c.material.name}`}>
        <div className="p-4 md:p-5">
          <InfoGrid
            items={[
              ["Quotes (180 days)", b.quotes],
              ["Last", fmtRate(b.last_rate)],
              ["Lowest", fmtRate(b.min_rate)],
              ["Average", fmtRate(b.avg_rate)],
            ]}
          />
          <ul className="mt-4 divide-y rounded-lg border">
            {c.vendors.map((v, i) => (
              <li key={(v.vendor_id ?? "") + i} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                <span className="min-w-0 truncate">
                  {i === 0 && <span className="mr-1.5 rounded bg-success/15 px-1.5 py-0.5 text-[10px] font-semibold text-success">LOWEST</span>}
                  {v.vendor_name}
                </span>
                <span className="shrink-0 tabular-nums">
                  {fmtRate(v.rate)} <span className="text-xs text-muted-foreground">· {fmtDate(v.quoted_on)}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      </Section>
      <Section title="Rate trend" actions={<TrendingUp className="size-4 text-muted-foreground" />}>
        {trend.length < 2 ? (
          <EmptyState title="Not enough quotes for a trend" />
        ) : (
          <div className="h-60 px-2 py-4" role="img" aria-label={`Rate trend for ${c.material.name}`}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trend} margin={{ left: 8, right: 16, top: 8, bottom: 4 }}>
                <CartesianGrid vertical={false} stroke="var(--border)" />
                <XAxis dataKey="quoted_on" tickFormatter={(v) => fmtDate(v).slice(0, 6)} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
                <YAxis domain={["auto", "auto"]} tickFormatter={(v) => `₹${v}`} width={56} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: "var(--popover)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
                  labelFormatter={(v) => fmtDate(String(v))}
                  formatter={(v, _n, p) => [`${fmtRate(Number(v))} / ${c.material.uom}`, (p?.payload as { vendor_name?: string } | undefined)?.vendor_name ?? "Market"]}
                />
                <Line type="monotone" dataKey="rate" stroke="var(--series-1)" strokeWidth={2} dot={{ r: 4, fill: "var(--series-1)", stroke: "var(--card)", strokeWidth: 2 }} activeDot={{ r: 6 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Section>
    </div>
  );
}

function RateForm({ onClose, initial }: { onClose: () => void; initial: PickedMaterial | null }) {
  const [material, setMaterial] = useState<PickedMaterial | null>(initial);
  const [vendor, setVendor] = useState("");
  const [source, setSource] = useState("VENDOR_QUOTE");
  const [rate, setRate] = useState("");
  const [uom, setUom] = useState("");
  const [quotedOn, setQuotedOn] = useState(todayIso());
  const [validTill, setValidTill] = useState("");
  const [remarks, setRemarks] = useState("");
  const save = useAction(
    () =>
      post("/market-rates", {
        material_id: material?.id,
        vendor_id: vendor || null,
        source,
        rate: Number(rate),
        uom_id: uom || null,
        quoted_on: quotedOn,
        valid_till: validTill || null,
        remarks: remarks || null,
      }),
    { success: "Rate recorded", invalidate: ["market", "market-compare"], onSuccess: onClose },
  );
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Record market rate" description="Rates entered per another unit (e.g. per bag) are converted to the material's base unit.">
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => (e.preventDefault(), save.mutate(undefined))}>
          <Field label="Material *" className="sm:col-span-2">
            <MaterialPicker value={material} onChange={setMaterial} />
          </Field>
          <Field label="Vendor">
            <MasterSelect path="vendors" value={vendor} onChange={setVendor} placeholder="— Market survey —" />
          </Field>
          <Field label="Source">
            <Select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="VENDOR_QUOTE">Vendor quote</option>
              <option value="MARKET_SURVEY">Market survey</option>
              <option value="ONLINE">Online</option>
            </Select>
          </Field>
          <Field label="Rate (₹) *">
            <Input type="number" inputMode="decimal" step="any" min="0" required value={rate} onChange={(e) => setRate(e.target.value)} />
          </Field>
          <Field label="Per unit" hint={material ? `Blank = per ${material.uom}` : undefined}>
            <MasterSelect path="uoms" value={uom} onChange={setUom} placeholder="Base unit" label={(x) => x.code ?? x.name} />
          </Field>
          <Field label="Quoted on">
            <Input type="date" value={quotedOn} max={todayIso()} onChange={(e) => setQuotedOn(e.target.value)} />
          </Field>
          <Field label="Valid till">
            <Input type="date" value={validTill} onChange={(e) => setValidTill(e.target.value)} />
          </Field>
          <Field label="Remarks" className="sm:col-span-2">
            <Textarea rows={2} value={remarks} onChange={(e) => setRemarks(e.target.value)} />
          </Field>
          <div className="flex flex-col-reverse gap-2 sm:col-span-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" loading={save.isPending} disabled={!material || !rate}>
              Save rate
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
