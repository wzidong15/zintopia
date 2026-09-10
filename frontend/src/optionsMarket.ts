export type VixPoint = {
  symbol: string;
  tenor: string;
  price: number | null;
  change: number | null;
  change_pct: number | null;
  source?: string | null;
};

export type OptionsMarket = {
  vix: {
    points: VixPoint[];
    vix_vix3m: number | null;
    structure: "contango" | "flat" | "backwardation" | string | null;
    near_term_stress: boolean;
    skew: { price: number | null; change: number | null; source?: string | null } | null;
  };
  put_call: {
    date: string | null;
    ratios: Partial<Record<"total" | "index" | "equity" | "etp" | "spx" | "vix", number | null>>;
    volume?: { call?: number | null; put?: number | null; total?: number | null };
    open_interest?: { call?: number | null; put?: number | null; total?: number | null };
    source?: string;
    url?: string;
    error?: string;
  };
  as_of: number;
  note?: string;
};

export function structureTone(structure: string | null | undefined): "up" | "down" | "" {
  if (structure === "backwardation") return "down";
  if (structure === "contango") return "up";
  return "";
}

export function pcTone(ratio: number | null | undefined): "up" | "down" | "" {
  if (ratio == null) return "";
  if (ratio >= 1.0) return "down";
  if (ratio <= 0.7) return "up";
  return "";
}
