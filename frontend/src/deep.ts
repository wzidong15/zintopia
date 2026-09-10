export type OptionsAnalysis = {
  expiry?: string | null;
  dte?: number | null;
  spot?: number | null;
  atm_strike?: number | null;
  atm_iv?: number | null;
  expected_move?: number | null;
  expected_move_pct?: number | null;
  expected_low?: number | null;
  expected_high?: number | null;
  expected_move_basis?: string | null;
  max_pain?: number | null;
  call_oi?: number | null;
  put_oi?: number | null;
  oi_put_call?: number | null;
  oi_by_strike?: { strike: number; call_oi: number; put_oi: number }[];
  iv_samples?: number;
  iv_samples_needed?: number;
  iv_rank?: number | null;
  iv_percentile?: number | null;
  iv_low?: number | null;
  iv_high?: number | null;
  iv_first_sample?: string | null;
  error?: string | null;
};

export type DeepAnalysis = {
  symbol: string;
  price?: number | null;
  name?: string;
  insiders: {
    net_value?: number;
    tilt?: string;
    items: {
      date?: string | null;
      insider?: string | null;
      title?: string | null;
      text?: string;
      shares?: number | null;
      value?: number | null;
    }[];
  };
  options: {
    expiry?: string | null;
    call_volume?: number;
    put_volume?: number;
    put_call?: number | null;
    error?: string | null;
    source?: string | null;
    items: {
      side: string;
      expiry?: string | null;
      strike?: number | null;
      last?: number | null;
      volume?: number | null;
      open_interest?: number | null;
      iv?: number | null;
      vol_oi?: number | null;
    }[];
    analysis?: OptionsAnalysis | null;
  };
  congress: {
    buy_count?: number;
    sell_count?: number;
    tilt?: string;
    source?: string | null;
    last_updated?: string | null;
    filed_through?: string | null;
    status?: string | null;
    note?: string | null;
    items: {
      date?: string | null;
      chamber?: string | null;
      person?: string | null;
      type?: string | null;
      amount?: string | null;
      filed?: string | null;
      link?: string | null;
    }[];
  };
  forecast: {
    target_mean?: number | null;
    target_high?: number | null;
    target_low?: number | null;
    analysts?: number | null;
    recommendation?: string | null;
    upside_pct?: number | null;
  };
  suggestion: {
    action: string;
    score: number;
    reasons: string[];
    disclaimer: string;
  };
};
