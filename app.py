"""Huis & Hypotheek — Dutch first-home buy-vs-invest comparison dashboard.

Run with:  streamlit run app.py

The UI is a thin layer over the (Streamlit-free, tested) ``mortgage`` package.
It is organised output-first: shared inputs live in the sidebar, split into
Basics and Advanced; the main canvas leads with a single, plain-language
recommendation and keeps all supporting detail in tabs.
"""

from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mortgage import (
    CASH_VEHICLES,
    CashAlternative,
    ScenarioInput,
    TaxAssumptions,
    reference_budget,
    reference_cash,
    run_scenario,
)

# --------------------------------------------------------------------------- #
# Model labels & the optimisation sweep grid (shared by widgets and compute)
# --------------------------------------------------------------------------- #
TYPES = {"annuity": "Annuïteit (annuity)", "linear": "Lineair (linear)"}
SWEEP_VEHICLES = ["savings", "deposit", "investment"]
VEH_COLORS = {"savings": "#0891b2", "deposit": "#7c3aed", "investment": "#2563eb"}
TYPE_DASH = {"annuity": "solid", "linear": "dot"}
N_SWEEP = 41


# --------------------------------------------------------------------------- #
# Compute layer — pure, cached on the *committed* input snapshot.
#
# Every input widget feeds a snapshot dict; the heavy model only re-runs when
# that snapshot changes (i.e. when the user clicks Calculate), not on every
# keystroke. The two in-tab sweep controls (cash pool, metric) are extra cache
# keys, so they stay live without re-running anything else.
# --------------------------------------------------------------------------- #
def build_models(C: dict) -> tuple[TaxAssumptions, CashAlternative, list[ScenarioInput], int]:
    tax = TaxAssumptions(
        ewf_rate=C["ewf_rate"], hillen_pct=C["hillen"], max_deduction_rate=C["max_ded"],
        starters_exemption=C["starters"], transfer_tax_rate=C["transfer"],
        nhg_premium_rate=C["nhg_rate"], box3_tax_rate=C["b3_rate"],
        box3_return_savings=C["b3_save"], box3_return_investments=C["b3_inv"],
        box3_allowance=float(C["b3_allow"]),
    )
    alt = CashAlternative(
        vehicle=C["vehicle"], savings_rate=C["sav_rate"], deposit_rate=C["dep_rate"],
        investment_return=C["inv_ret"], annual_fee=C["fee"],
    )
    eff_horizon = min(int(C["horizon"]), int(C["term"]))
    scenarios = [
        ScenarioInput(
            name=n, house_price=float(C["price"]), down_payment=float(d),
            interest_rate=C["rate"], mortgage_type=t, mortgage_term_years=int(C["term"]),
            fixed_period_years=int(C["fixed"]), horizon_years=eff_horizon,
            appreciation_rate=C["appr"], gross_income=float(C["income"]),
            other_purchase_costs=float(C["other"]), selling_cost_rate=C["sell"], nhg=C["nhg"],
        )
        for (n, t, d) in C["scenarios"]
    ]
    return tax, alt, scenarios, eff_horizon


def _metric_of(r, metric: str) -> float:
    return {"Net worth at sale": r.net_worth_end,
            "Net result (profit)": r.net_result,
            "Annualised return": r.annual_return}[metric]


@st.cache_data(show_spinner=False)
def compute_core(snap_json: str):
    """Run every configured scenario; cached on the committed input snapshot."""
    C = json.loads(snap_json)
    tax, alt, scenarios, _ = build_models(C)
    budget = reference_budget(scenarios)
    ref_cash = reference_cash(scenarios)
    results = [
        run_scenario(s, tax, alt=alt, invested_cash=ref_cash - s.down_payment,
                     budget_monthly=budget)
        for s in scenarios
    ]
    return scenarios, results, budget, ref_cash


@st.cache_data(show_spinner=False)
def compute_sweep(snap_json: str, cap: float, metric: str):
    """Sweep the down payment over 2 mortgage types × 3 vehicles. Heavy: cached."""
    C = json.loads(snap_json)
    tax, alt, scenarios, eff_horizon = build_models(C)
    price = float(C["price"])

    def scenario_at(d, mtype):
        return ScenarioInput(
            name="s", house_price=price, down_payment=min(d, price), interest_rate=C["rate"],
            mortgage_type=mtype, mortgage_term_years=int(C["term"]),
            fixed_period_years=int(C["fixed"]), horizon_years=eff_horizon,
            appreciation_rate=C["appr"], gross_income=float(C["income"]),
            other_purchase_costs=float(C["other"]), selling_cost_rate=C["sell"], nhg=C["nhg"])

    # Fixed monthly budget = highest first-month payment (full linear loan), so the
    # monthly spare invested never goes negative and all curves share one X grid.
    sweep_budget = max(
        reference_budget([scenario_at(0.0, mt)]) for mt in TYPES
    )

    def run_at(d, mtype, alt_v):
        return run_scenario(scenario_at(d, mtype), tax, alt=alt_v,
                            invested_cash=cap - d, budget_monthly=sweep_budget)

    xs = [cap * k / (N_SWEEP - 1) if N_SWEEP > 1 else 0.0 for k in range(N_SWEEP)]
    curves: dict[tuple[str, str], dict] = {}
    for v in SWEEP_VEHICLES:
        av = CashAlternative(vehicle=v, savings_rate=C["sav_rate"], deposit_rate=C["dep_rate"],
                             investment_return=C["inv_ret"], annual_fee=C["fee"])
        for mt in TYPES:
            ys = [_metric_of(run_at(d, mt, av), metric) for d in xs]
            bi = max(range(N_SWEEP), key=lambda i: ys[i])
            curves[(v, mt)] = {"ys": ys, "best_d": xs[bi], "best_y": ys[bi], "best_i": bi}
    best_key = max(curves, key=lambda k: curves[k]["best_y"])

    # Configured-scenario dots (using the chosen vehicle), computed here so no
    # run_scenario call escapes the cache.
    dots: list[tuple[str, float, float]] = []
    if alt.invests:
        seen: set = set()
        for s in scenarios:
            key = (s.mortgage_type, round(s.down_payment))
            if key in seen or s.down_payment > cap:
                continue
            seen.add(key)
            y = _metric_of(run_at(s.down_payment, s.mortgage_type, alt), metric)
            dots.append((s.name, s.down_payment, y))
    return xs, curves, best_key, dots

# --------------------------------------------------------------------------- #
# Page setup
# --------------------------------------------------------------------------- #
st.set_page_config(
    page_title="Huis & Hypotheek — which mortgage choice wins?",
    page_icon="🏠",
    layout="wide",
)

# Design system ------------------------------------------------------------- #
FONT = '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
INK = "#0f172a"
MUTED = "#64748b"
GRID = "#eef2f7"
LINE = "#e2e8f0"
GREEN = "#16a34a"
RED = "#ef4444"
ACCENTS = ["#2563eb", "#7c3aed", "#0891b2"]  # blue, violet, cyan — one per scenario

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

      html, body, [class*="css"] { font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif; }
      .block-container { padding-top: 2.0rem; padding-bottom: 3rem; max-width: 1360px; }
      h1, h2, h3, h4 { letter-spacing: -0.02em; color: #0f172a; }
      #MainMenu, footer { visibility: hidden; }

      /* ---- Hero ---- */
      .hero { padding: 4px 2px 14px 2px; border-bottom: 1px solid #eef2f7; margin-bottom: 22px; }
      .hero h1 { font-size: 1.6rem; margin: 0 0 4px 0; font-weight: 800; }
      .hero p  { margin: 0; color: #64748b; font-size: .98rem; max-width: 760px; }
      .pill { display:inline-block; background:#eef4ff; color:#2563eb; font-weight:600;
              padding:3px 11px; border-radius:999px; font-size:.74rem; margin:8px 6px 0 0; }

      /* ---- Recommendation card (the primary output) ---- */
      .reco { background: linear-gradient(135deg,#1e3a8a 0%, #2563eb 55%, #4f46e5 100%);
              color:#fff; border-radius: 22px; padding: 26px 30px; margin: 4px 0 10px 0;
              box-shadow: 0 18px 40px -18px rgba(37,99,235,.55); }
      .reco-badge { display:inline-block; background:rgba(255,255,255,.18); color:#fff;
              font-weight:700; letter-spacing:.06em; font-size:.7rem;
              padding:5px 12px; border-radius:999px; }
      .reco-title { font-size: 1.7rem; font-weight:800; margin: 14px 0 2px 0; line-height:1.2; }
      .reco-title b { color:#fff; }
      .reco-sub { font-size:1.0rem; opacity:.92; margin: 8px 0 20px 0; max-width: 820px; line-height:1.5; }
      .reco-stats { display:flex; flex-wrap:wrap; gap:12px; }
      .reco-stat { background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.18);
              border-radius:14px; padding:12px 16px; min-width:150px; flex:1; }
      .reco-stat .l { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; opacity:.8; }
      .reco-stat .v { font-size:1.35rem; font-weight:800; margin-top:3px; }
      .reco-stat .d { font-size:.78rem; opacity:.85; margin-top:2px; }

      /* ---- Scenario column headers ---- */
      .sc-head { border-top: 4px solid #2563eb; border-radius: 4px 4px 0 0;
                 padding: 8px 2px 4px 2px; font-weight:700; font-size:1.0rem; }
      .sc-win { display:inline-block; background:#ecfdf5; color:#16a34a; font-weight:700;
                font-size:.66rem; padding:2px 8px; border-radius:999px; margin-left:6px;
                vertical-align:middle; border:1px solid #bbf7d0; }

      /* ---- Metric cards ---- */
      div[data-testid="stMetric"] {
          background:#fff; border:1px solid #e8edf3; border-radius:14px;
          padding:12px 16px; box-shadow:0 1px 2px rgba(16,24,40,.04); }
      div[data-testid="stMetricLabel"] { opacity:.62; font-weight:600; }

      /* ---- Tabs / sidebar polish ---- */
      .stTabs [data-baseweb="tab"] { font-weight:600; }
      section[data-testid="stSidebar"] { background:#fbfcfe; border-right:1px solid #eef2f7; }
      section[data-testid="stSidebar"] h2 { font-size:1.05rem; }
      .side-kicker { font-size:.72rem; font-weight:700; letter-spacing:.08em;
                     text-transform:uppercase; color:#94a3b8; margin: 2px 0 -4px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
      <h1>🏠 Huis &amp; Hypotheek</h1>
      <p>Compare house-buying scenarios for a Dutch first-time, solo buyer — and get a
         clear recommendation. Accounts for hypotheekrenteaftrek, eigenwoningforfait,
         NHG, box 3, and the spare cash invested instead.</p>
      <div>
        <span class="pill">Annuïteit &amp; Lineair</span>
        <span class="pill">Renteaftrek</span>
        <span class="pill">Eigenwoningforfait</span>
        <span class="pill">NHG</span>
        <span class="pill">Box 3</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)


def euro(x: float) -> str:
    return f"€{x:,.0f}".replace(",", ".")


def pct(x: float) -> str:
    return f"{x*100:.2f}%"


def style_fig(fig: go.Figure, height: int = 380, ytitle: str = "€", money_y: bool = True) -> go.Figure:
    """Apply the shared, clean plotly look."""
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=14, b=8),
        font=dict(family=FONT, size=13, color="#334155"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        yaxis_title=ytitle,
        legend=dict(orientation="h", yanchor="bottom", y=-0.24, x=0, font=dict(size=12)),
        hoverlabel=dict(font_size=12, font_family=FONT, bgcolor="#fff"),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=LINE, ticks="outside",
                     tickcolor=LINE, tickfont=dict(color=MUTED))
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=True, zerolinecolor=LINE,
                     tickfont=dict(color=MUTED))
    if money_y:
        fig.update_yaxes(tickprefix="€", tickformat="~s")
    return fig


# --------------------------------------------------------------------------- #
# Sidebar — all shared inputs, split into Basics and Advanced
# --------------------------------------------------------------------------- #
with st.sidebar:
    # Rendered into below, after inputs are read — kept at the very top so it's
    # always visible without scrolling.
    calc_slot = st.container()
    st.divider()
    st.markdown('<div class="side-kicker">Your situation</div>', unsafe_allow_html=True)
    st.header("Basics")
    st.caption("The few inputs that drive most of the answer. Shared by every scenario.")

    price = st.number_input("House price", 50_000, 2_000_000, 280_000, 5_000,
                            help="The purchase price you're comparing scenarios against.")
    income = st.number_input("Gross annual income", 0, 1_000_000, 60_000, 1_000,
                             help="Sets your tax bracket — and so the value of mortgage-interest deduction.")
    horizon = st.slider("Years before you sell", 1, 30, 5,
                        help="How long you keep the house. The whole comparison runs over this period.")
    rate = st.number_input("Mortgage interest %", 0.0, 15.0, 3.9, 0.05,
                           help="Your hypotheekrente (annual nominal rate).") / 100
    appr = st.number_input("Expected house growth %/yr", -10.0, 20.0, 3.0, 0.25,
                           help="Average yearly rise in the home's value, compounded — the single biggest "
                                "driver of whether buying pays off.") / 100

    st.divider()
    st.markdown('<div class="side-kicker">Advanced settings</div>', unsafe_allow_html=True)
    st.caption("Sensible Dutch 2025 defaults — open only if you want to fine-tune.")

    with st.expander("🏦 Mortgage details"):
        term = st.slider("Mortgage duration (years)", 5, 30, 30)
        fixed = st.selectbox("Fixed-interest period (years)", [1, 5, 10, 20, 30], index=2)
        nhg = st.checkbox("NHG (mortgage guarantee)", value=True)

    with st.expander("📈 Spare cash — where it's invested"):
        st.caption("Whatever a scenario doesn't put into the house is invested here, "
                   "growing net of fees and taxed yearly in box 3.")
        vehicle = st.radio(
            "Invest spare cash in:",
            options=list(CASH_VEHICLES.keys()),
            format_func=lambda k: CASH_VEHICLES[k],
            index=0,
        )
        sav_rate = st.number_input("Savings rente %/yr", 0.0, 15.0, 2.25, 0.1) / 100
        dep_rate = st.number_input("Deposit rente %/yr", 0.0, 15.0, 3.0, 0.1) / 100
        inv_ret = st.number_input("Investment return %/yr", -10.0, 25.0, 6.0, 0.5) / 100
        fee = st.number_input("Investment fee %/yr", 0.0, 5.0, 0.3, 0.05,
                              help="Annual cost of the portfolio (fund/ETF fees), applied to investments only.") / 100

    with st.expander("🧾 One-off & selling costs"):
        other = st.number_input("Other purchase costs (notary, valuation, advice)",
                                0, 50_000, 4_000, 250)
        sell = st.number_input("Selling costs % (makelaar etc.)", 0.0, 10.0, 1.5, 0.1) / 100

    with st.expander("🇳🇱 Tax assumptions (2025)"):
        st.caption("Editable — update as the rules change each year.")
        st.markdown("**Box 1 — owner-occupied home**")
        ewf_rate = st.number_input("Eigenwoningforfait rate %", 0.0, 2.0, 0.35, 0.01) / 100
        max_ded = st.number_input("Max interest-deduction rate %", 0.0, 60.0, 37.48, 0.1) / 100
        hillen = st.number_input("Wet Hillen factor %", 0.0, 100.0, 76.67, 0.5) / 100
        st.markdown("**One-off purchase taxes**")
        starters = st.checkbox("Starters' exemption (0% transfer tax)", value=True)
        transfer = st.number_input("Transfer tax (if no exemption) %", 0.0, 10.0, 2.0, 0.1) / 100
        nhg_rate = st.number_input("NHG premium %", 0.0, 5.0, 0.6, 0.05) / 100
        st.markdown("**Box 3 — wealth tax on invested cash**")
        b3_rate = st.number_input("Box 3 tax rate %", 0.0, 60.0, 36.0, 0.5) / 100
        b3_save = st.number_input("Deemed return — savings/deposit %", 0.0, 15.0, 1.44, 0.1) / 100
        b3_inv = st.number_input("Deemed return — investments %", 0.0, 15.0, 5.88, 0.1) / 100
        b3_allow = st.number_input("Tax-free allowance (single)", 0, 200_000, 57_684, 1_000)

# Shared inputs are now collected into a snapshot below (with the scenarios) and
# the model objects are rebuilt from the *committed* snapshot, so nothing heavy
# runs until the user clicks Calculate.

# --------------------------------------------------------------------------- #
# Scenarios — only what differs between the cases you compare
# --------------------------------------------------------------------------- #
DEFAULTS = [
    dict(name="Annuity mortgage", down=0, typ="annuity"),
    dict(name="Linear mortgage", down=0, typ="linear"),
    dict(name="Bigger down payment", down=50_000, typ="annuity"),
]

eff_horizon = min(horizon, term)

st.subheader("⚖️ Scenarios to compare")
top = st.columns([3, 2])
with top[0]:
    st.caption("Everything else is shared (set in the sidebar). Per scenario you choose the "
               "**mortgage type** and the **cash you bring in**. Scenario B always uses Scenario A's cash.")
with top[1]:
    n_scenarios = st.radio("Number of scenarios", [1, 2, 3], index=2, horizontal=True,
                           label_visibility="collapsed")

if horizon > term:
    st.warning(f"Holding period ({horizon}y) capped to the mortgage duration ({term}y).")

live_scenarios: list[tuple[str, str, float]] = []
with st.container(border=True):
    tabs = st.tabs([f"Scenario {chr(65 + i)} — {DEFAULTS[i]['name']}" for i in range(n_scenarios)])
    ab_down = 0.0
    for i in range(n_scenarios):
        d = DEFAULTS[i]
        with tabs[i]:
            c1, c2, c3 = st.columns(3)
            with c1:
                name = st.text_input("Scenario name", d["name"], key=f"name{i}")
            with c2:
                mtype = st.selectbox("Mortgage type", list(TYPES), index=list(TYPES).index(d["typ"]),
                                     format_func=lambda k: TYPES[k], key=f"type{i}")
            with c3:
                if i == 1:  # Scenario B follows Scenario A's cash automatically.
                    down = ab_down
                    st.number_input("Cash brought in (eigen inbreng)", value=int(ab_down), disabled=True)
                    st.caption("Automatically equal to Scenario A.")
                else:
                    down = st.number_input(
                        "Cash brought in (eigen inbreng)", 0, int(price), d["down"], 5_000,
                        key=f"down{i}", help="Your own money. The mortgage is the house price minus this.")
                    if i == 0:
                        ab_down = float(down)

            loan = max(0.0, price - down)
            st.caption(
                f"→ **{name}**: cash in **{euro(down)}**  ·  loan **{euro(loan)}**  ·  "
                f"mortgage **{TYPES[mtype]}**  ·  sell after **{eff_horizon} yr**  ·  "
                f"spare cash → **{CASH_VEHICLES[vehicle]}**"
            )

            live_scenarios.append((name, mtype, float(down)))

# --------------------------------------------------------------------------- #
# Calculate gate — commit inputs only when the button is clicked, so the heavy
# model (cached on the snapshot) reruns then, not on every keystroke.
# --------------------------------------------------------------------------- #
live = dict(
    price=int(price), income=int(income), horizon=int(horizon), rate=rate, appr=appr,
    term=int(term), fixed=int(fixed), nhg=bool(nhg), vehicle=vehicle,
    sav_rate=sav_rate, dep_rate=dep_rate, inv_ret=inv_ret, fee=fee,
    other=int(other), sell=sell, ewf_rate=ewf_rate, max_ded=max_ded, hillen=hillen,
    starters=bool(starters), transfer=transfer, nhg_rate=nhg_rate, b3_rate=b3_rate,
    b3_save=b3_save, b3_inv=b3_inv, b3_allow=int(b3_allow),
    scenarios=live_scenarios,
)

calc_clicked = calc_slot.button("📊 Calculate", type="primary", use_container_width=True,
                                help="Apply your inputs and re-run the model.")
if calc_clicked or "committed" not in st.session_state:
    st.session_state.committed = live

C = st.session_state.committed
snap_json = json.dumps(C, sort_keys=True)
if live != C:
    calc_slot.caption("⚠️ Inputs changed — click **Calculate** to update the results.")

# --------------------------------------------------------------------------- #
# Run the model (cached on the committed snapshot)
# --------------------------------------------------------------------------- #
tax, alt, scenarios, eff_horizon = build_models(C)
# Rebind the loose names the rest of the page reads, to the *committed* values.
price, income, rate, appr = C["price"], C["income"], C["rate"], C["appr"]
term, fixed, nhg, vehicle = C["term"], C["fixed"], C["nhg"], C["vehicle"]
sav_rate, dep_rate, inv_ret, fee = C["sav_rate"], C["dep_rate"], C["inv_ret"], C["fee"]
other, sell = C["other"], C["sell"]

scenarios, results, budget, ref_cash = compute_core(snap_json)
pairs = list(zip(scenarios, results))
ranked = sorted(pairs, key=lambda sr: sr[1].net_result, reverse=True)
winner_name = ranked[0][1].name

# --------------------------------------------------------------------------- #
# THE RECOMMENDATION — primary output
# --------------------------------------------------------------------------- #
def reason_line(ws, wr, rs, rr) -> str:
    """One plain-language sentence on why the winner wins, vs the runner-up."""
    net_rate = alt.net_rate()
    dp_diff = ws.down_payment - rs.down_payment
    if abs(dp_diff) > 1_000:
        if dp_diff > 0:
            return (f"Putting more of your cash into the house ({euro(ws.down_payment)} vs "
                    f"{euro(rs.down_payment)}) wins here: your {pct(ws.interest_rate)} mortgage costs "
                    f"more than the {pct(net_rate)} your spare cash earns after fees &amp; box 3.")
        return (f"Keeping cash out of the house and investing it wins: it earns {pct(net_rate)} "
                f"after fees &amp; box 3, beating your {pct(ws.interest_rate)} mortgage rate.")
    if ws.mortgage_type != rs.mortgage_type:
        if ws.mortgage_type == "linear":
            return ("A linear mortgage wins: you repay faster and pay less total interest — and the "
                    "fair comparison still invests the higher early payments, so you keep the upside.")
        return ("An annuity mortgage wins: its lower early payments leave more cash to invest, which "
                "here outgrows the extra interest you pay.")
    return f"It leaves you with the most after tax, interest and costs over {ws.horizon_years} years."


def build_reco_html() -> str:
    ws, wr = ranked[0]
    profit_sign = "profit" if wr.net_result >= 0 else "loss"

    if len(ranked) == 1:
        verdict = ("a solid result" if wr.net_result > 0 else
                   "a loss over this period — worth reconsidering the price, horizon or rate")
        title = f"Over {ws.horizon_years} years, <b>{ws.name}</b> gives {verdict}"
        sub = (f"You walk away with <b>{euro(wr.net_worth_end)}</b> at sale — a net {profit_sign} of "
               f"{euro(wr.net_result)}, or {pct(wr.annual_return)} per year (money-weighted).")
        stats = [
            ("You walk away with", euro(wr.net_worth_end), "home equity + invested pot"),
            ("Net result", euro(wr.net_result), f"after everything you put in"),
            ("Annualised return", pct(wr.annual_return), "money-weighted (IRR)"),
            ("Monthly payment", euro(wr.monthly_payment_start), "first month"),
        ]
    else:
        rs, rr = ranked[1]
        margin = wr.net_result - rr.net_result
        if margin < 2_500:
            title = f"It's almost a tie — <b>{ws.name}</b> just edges ahead"
            sub = (f"<b>{ws.name}</b> beats <b>{rr.name}</b> by only {euro(margin)} over "
                   f"{ws.horizon_years} years — so pick on preference (payment certainty vs. flexibility). "
                   + reason_line(ws, wr, rs, rr))
        else:
            title = f"Based on your situation, the best choice is <b>{ws.name}</b>"
            sub = reason_line(ws, wr, rs, rr)
        stats = [
            ("You walk away with", euro(wr.net_worth_end), "home equity + invested pot"),
            ("Net result", euro(wr.net_result), f"{pct(wr.annual_return)} per year"),
            (f"Ahead of {rr.name}", f"+{euro(margin)}", "extra net result"),
            ("Monthly payment", euro(wr.monthly_payment_start), "first month"),
        ]

    chips = "".join(
        f'<div class="reco-stat"><div class="l">{l}</div>'
        f'<div class="v">{v}</div><div class="d">{d}</div></div>'
        for l, v, d in stats
    )
    return (
        '<div class="reco">'
        '<span class="reco-badge">★ RECOMMENDED FOR YOU</span>'
        f'<div class="reco-title">{title}</div>'
        f'<div class="reco-sub">{sub}</div>'
        f'<div class="reco-stats">{chips}</div>'
        '</div>'
    )


st.markdown(build_reco_html(), unsafe_allow_html=True)

if alt.invests:
    st.caption(
        f"Fair comparison: every scenario is given the same **{euro(ref_cash)}** upfront and "
        f"**{euro(budget)}/mo** budget; whatever it doesn't put into the house is invested in a "
        f"**{CASH_VEHICLES[vehicle].lower()}** (after fees &amp; box 3). Ranked by net result (profit)."
    )
else:
    st.caption("Spare cash is ignored — each scenario is evaluated at its own down payment and monthly payment. "
               "Ranked by net result (profit).")

# --------------------------------------------------------------------------- #
# KPI cards — winner highlighted
# --------------------------------------------------------------------------- #
st.markdown("#### At a glance")
cols = st.columns(len(results))
for i, (col, s, r) in enumerate(zip(cols, scenarios, results)):
    accent = ACCENTS[i % len(ACCENTS)]
    is_win = r.name == winner_name
    badge = '<span class="sc-win">🏆 BEST</span>' if is_win else ""
    with col:
        st.markdown(f'<div class="sc-head" style="border-color:{accent}">{r.name}{badge}</div>',
                    unsafe_allow_html=True)
        st.metric("Net worth at sale", euro(r.net_worth_end),
                  help="What you walk away with = home equity (home value − selling costs − remaining "
                       "mortgage) plus the invested-cash pot (after fees & box 3).")
        st.metric("Net result (profit)", euro(r.net_result), delta=f"{pct(r.annual_return)} / yr",
                  help="Net worth at sale minus everything you put in.")
        if alt.invests:
            st.metric("Invested-cash pot", euro(r.side_pot_end),
                      help=f"Down-payment lump ({euro(r.invested_cash_start)}) + spare monthly cash "
                           f"({euro(r.spare_invested_total)}), grown after fees & box 3.")
        st.metric("Monthly mortgage (start)", euro(r.monthly_payment_start))

# --------------------------------------------------------------------------- #
# Detailed analysis — everything in tabs
# --------------------------------------------------------------------------- #
st.markdown("#### Detailed analysis")
t_optim, t_time, t_wealth, t_profit, t_bank, t_table, t_year = st.tabs([
    "💡 Optimal own money", "📈 Net worth over time", "🧱 Wealth breakdown",
    "💸 Where profit comes from", "🏦 Paid to the bank", "📋 Full comparison", "🔎 Yearly detail",
])

# --- Optimal down payment sweep --- #
with t_optim:
    st.caption(
        "**How much of your own money should go into the house at the start?** Holding your "
        "total cash and monthly budget fixed, this sweeps the down payment — money *not* put "
        "into the house is invested instead. One curve per **mortgage type × spare-cash vehicle** "
        "(line style = type, colour = vehicle); the peak of each is the amount that leaves you "
        "richest with that combination. The single brightest star is the **overall best**. "
        "Your configured scenarios appear as labelled dots."
    )
    oc1, oc2 = st.columns([2, 3])
    with oc1:
        cash_avail = st.number_input(
            "Total cash you could put in", 0, int(price),
            min(int(ref_cash) if ref_cash > 0 else 100_000, int(price)), 5_000,
            help="The X-axis runs from €0 (put nothing in, invest it all) to this amount "
                 "(put everything into the house). Defaults to your scenarios' cash so the "
                 "curve and dots match the recommendation above.")
    with oc2:
        metric = st.radio("Optimise for",
                          ["Net result (profit)", "Net worth at sale", "Annualised return"],
                          horizontal=True,
                          help="Net result (profit) is the default — what the decision actually earns "
                               "you over the horizon, net of everything paid in.")

    cap = min(float(cash_avail), float(price))
    is_pct = metric == "Annualised return"

    # Heavy sweep (2 mortgage types × 3 vehicles), cached on the committed snapshot
    # plus the live cash pool / metric so it only re-runs when those actually change.
    xs, curves, best_key, dots = compute_sweep(snap_json, cap, metric)

    fig5 = go.Figure()
    for v in SWEEP_VEHICLES:
        for mt in TYPES:
            c = curves[(v, mt)]
            label = f"{CASH_VEHICLES[v].split(' (')[0]} · {TYPES[mt].split(' ')[0]}"
            fig5.add_trace(go.Scatter(
                x=xs, y=c["ys"], mode="lines",
                line=dict(width=2.5, color=VEH_COLORS[v], dash=TYPE_DASH[mt]),
                name=label,
                hovertemplate=f"{label}<br>Own money €%{{x:,.0f}}<br>{metric}: %{{customdata}}<extra></extra>",
                customdata=[pct(y) if is_pct else euro(y) for y in c["ys"]]))
            # subdued per-curve sweet-spot marker
            fig5.add_trace(go.Scatter(
                x=[c["best_d"]], y=[c["best_y"]], mode="markers",
                marker=dict(size=8, color=VEH_COLORS[v], symbol="circle",
                            opacity=0.55, line=dict(width=1, color="#fff")),
                showlegend=False,
                hovertemplate=f"{label} sweet spot<br>€%{{x:,.0f}} in<extra></extra>"))

    # The single overall best, marked unmistakably.
    bc = curves[best_key]
    fig5.add_trace(go.Scatter(
        x=[bc["best_d"]], y=[bc["best_y"]], mode="markers",
        marker=dict(size=20, color="#f59e0b", symbol="star",
                    line=dict(width=2, color="#0f172a")),
        name="Best overall",
        hovertemplate=f"Best overall<br>€%{{x:,.0f}} in<extra></extra>"))

    # Overlay the configured scenarios as labelled dots, using your configured vehicle.
    for dot_name, dot_down, dot_y in dots:
        fig5.add_trace(go.Scatter(
            x=[dot_down], y=[dot_y], mode="markers+text",
            marker=dict(size=10, color="#0f172a", line=dict(width=2, color="#fff")),
            text=[f" {dot_name}"], textposition="bottom center",
            textfont=dict(size=11, color=MUTED), showlegend=False,
            hovertemplate=f"{dot_name}<br>€%{{x:,.0f}} in<extra></extra>"))

    fig5.update_layout(xaxis_title="Own money put in at the start (eigen inbreng)",
                       legend=dict(orientation="h", yanchor="bottom", y=-0.30, x=0))
    style_fig(fig5, 460, metric, money_y=not is_pct)
    fig5.update_xaxes(tickprefix="€", tickformat="~s")
    fig5.update_layout(showlegend=True)
    if is_pct:
        fig5.update_yaxes(tickprefix="", tickformat=".1%")
    st.plotly_chart(fig5, width="stretch")

    if cap <= 0:
        st.info("Set a 'Total cash you could put in' above €0 to see the curves.")
    else:
        def fmt(v):
            return pct(v) if is_pct else euro(v)

        def edge_note(c) -> str:
            if c["best_i"] >= N_SWEEP - 1:
                return ("rises all the way to the edge — within this cash it's *more is better*, "
                        "and the true optimum may be even higher (raise 'total cash' to check)")
            if c["best_i"] <= 0:
                return "is highest at €0 — here *keeping your cash invested* beats putting it in"
            return f"peaks at a sweet spot of **{euro(c['best_d'])}**"

        bv, bmt = best_key
        st.success(
            f"Best overall: put in **{euro(bc['best_d'])}** of your {euro(cap)} as a "
            f"**{TYPES[bmt].split(' ')[0]}** mortgage and invest the rest in a "
            f"**{CASH_VEHICLES[bv].split(' (')[0].lower()}** → {metric.lower()} of **{fmt(bc['best_y'])}**."
        )
        # One line per vehicle: its better mortgage type and where that curve peaks.
        lines = " ".join(
            (lambda bmt_v, c: f"**{CASH_VEHICLES[v].split(' (')[0]}**: best as "
                              f"{TYPES[bmt_v].split(' ')[0]}, {fmt(c['best_y'])} — the curve {edge_note(c)}.")(
                bmt_v := max(TYPES, key=lambda mt: curves[(v, mt)]["best_y"]),
                curves[(v, bmt_v)])
            for v in SWEEP_VEHICLES
        )
        st.caption(lines)
        st.caption("This model is close to linear in the down payment, so the best answer is usually a "
                   "**corner** (€0 or your whole pot): if a vehicle's after-tax return beats the after-tax "
                   "mortgage cost, keep cash invested; if not, put it all in. Interior *sweet spots* come "
                   "from the box-3 tax-free allowance, NHG and the deduction cap.")
        if is_pct:
            st.caption("Note: *annualised return* usually peaks at a low down payment (more leverage), "
                       "while *net profit / net worth* peak where your absolute money is highest — "
                       "switch the metric to compare.")
        elif cap != ref_cash:
            st.caption(f"You're exploring a €{cap:,.0f} pool; your scenarios above use €{ref_cash:,.0f}. "
                       "Set 'total cash' to that for the dots to match the recommendation exactly."
                       .replace(",", "."))

# --- Net worth over time --- #
with t_time:
    st.caption("Equity (home value − selling costs − remaining mortgage) plus the invested pot, year by year.")
    fig = go.Figure()
    for i, (s, r) in enumerate(pairs):
        years = [row["year"] for row in r.yearly]
        nw = [row["home_value"] * (1 - s.selling_cost_rate) - row["remaining_balance"] + row["invested_pot"]
              for row in r.yearly]
        fig.add_trace(go.Scatter(x=years, y=nw, mode="lines+markers", name=r.name,
                                 line=dict(width=3, color=ACCENTS[i % len(ACCENTS)])))
    fig.update_layout(xaxis_title="Year")
    st.plotly_chart(style_fig(fig, 420, "Net worth"), width="stretch")

# --- Wealth breakdown --- #
with t_wealth:
    st.caption("How the net worth at sale is built up: net sale proceeds, minus the remaining mortgage, "
               "plus the invested-cash pot.")
    names = [r.name for r in results]
    fig3 = go.Figure()
    fig3.add_trace(go.Bar(name="Net sale proceeds", x=names,
                          y=[r.home_value_end - r.selling_costs for r in results], marker_color=GREEN))
    fig3.add_trace(go.Bar(name="− Remaining mortgage", x=names,
                          y=[-r.remaining_balance for r in results], marker_color=RED))
    fig3.add_trace(go.Bar(name="Invested-cash pot", x=names,
                          y=[r.side_pot_end for r in results], marker_color=ACCENTS[0]))
    fig3.update_layout(barmode="relative")
    st.plotly_chart(style_fig(fig3, 380), width="stretch")

# --- Where profit comes from --- #
with t_profit:
    st.caption("Repaying principal is **not** profit — it just turns cash into home equity. Your real gain "
               "is appreciation, minus the cost of owning (interest, buying & selling costs), plus tax relief, "
               "plus the invested-cash gain. The bars reconcile exactly to the net result.")
    pcols = st.columns(len(results))
    for col, s, r in zip(pcols, scenarios, results):
        appreciation = r.home_value_end - s.house_price
        invest_gain = r.side_pot_end - r.invested_cash_start - r.spare_invested_total
        labels = ["House<br>appreciation", "Interest<br>paid", "Purchase<br>costs",
                  "Selling<br>costs", "Tax<br>benefit", "Invested<br>cash gain", "Net<br>result"]
        values = [appreciation, -r.total_interest, -r.purchase_costs["net"],
                  -r.selling_costs, r.total_tax_benefit, invest_gain, r.net_result]
        measures = ["relative"] * 6 + ["total"]
        with col:
            st.markdown(f"**{r.name}**")
            wf = go.Figure(go.Waterfall(
                orientation="v", measure=measures, x=labels, y=values,
                text=[euro(v) for v in values], textposition="outside",
                connector={"line": {"color": "#cbd5e1"}},
                increasing={"marker": {"color": GREEN}},
                decreasing={"marker": {"color": RED}},
                totals={"marker": {"color": ACCENTS[0]}},
            ))
            wf.update_xaxes(tickfont=dict(size=10))
            st.plotly_chart(style_fig(wf, 360), width="stretch")

# --- Paid to the bank --- #
with t_bank:
    st.caption("Each bar is one year's payments to the bank. Solid = **principal** (repaying the loan), "
               "hatched = **interest**. Annuity keeps the total level; linear keeps repayment flat so the total falls.")
    fig4 = go.Figure()
    for i, r in enumerate(results):
        years = [row["year"] for row in r.yearly]
        base = ACCENTS[i % len(ACCENTS)]
        fig4.add_trace(go.Bar(x=years, y=[row["principal_paid"] for row in r.yearly],
                              name=f"{r.name} · repayment", offsetgroup=str(i), legendgroup=r.name,
                              marker_color=base,
                              hovertemplate="Year %{x}<br>Repayment €%{y:,.0f}<extra></extra>"))
        fig4.add_trace(go.Bar(x=years, y=[row["interest_paid"] for row in r.yearly],
                              name=f"{r.name} · interest", offsetgroup=str(i), legendgroup=r.name,
                              marker_color=base, opacity=0.45, marker_pattern_shape="/",
                              hovertemplate="Year %{x}<br>Interest €%{y:,.0f}<extra></extra>"))
    fig4.update_layout(barmode="stack", xaxis_title="Year")
    st.plotly_chart(style_fig(fig4, 420, "Paid to the bank"), width="stretch")

# --- Full comparison table --- #
with t_table:
    table = pd.DataFrame({
        r.name: {
            "House price": euro(s.house_price),
            "Cash brought in": euro(s.down_payment),
            "Loan": euro(s.loan_amount),
            "Purchase costs (net)": euro(r.purchase_costs["net"]),
            "Start monthly payment": euro(r.monthly_payment_start),
            "Cash invested (lump)": euro(r.invested_cash_start),
            "Spare monthly invested": euro(r.spare_invested_total),
            "Home value at sale": euro(r.home_value_end),
            "Remaining mortgage": euro(r.remaining_balance),
            "Selling costs": euro(r.selling_costs),
            "Invested pot at sale": euro(r.side_pot_end),
            "Total interest": euro(r.total_interest),
            "Total repayment": euro(r.total_principal_repaid),
            "Total paid to bank": euro(r.total_interest + r.total_principal_repaid),
            "Net tax benefit (box 1)": euro(r.total_tax_benefit),
            "Box 3 tax on pot": euro(r.total_box3_tax),
            "Total contributed": euro(r.total_contributed),
            "Net worth at sale": euro(r.net_worth_end),
            "Net result": euro(r.net_result),
            "Annualised return": pct(r.annual_return),
        }
        for s, r in pairs
    })
    st.dataframe(table, width="stretch")

# --- Yearly detail --- #
with t_year:
    for s, r in pairs:
        with st.expander(f"{r.name} — purchase costs & yearly breakdown"):
            pc = r.purchase_costs
            st.write(
                f"**Purchase costs (kosten koper):** transfer tax {euro(pc['transfer_tax'])} · "
                f"NHG premium {euro(pc['nhg_premium'])} · other {euro(pc['other'])} · "
                f"financing deduction −{euro(pc['financing_deduction'])} → **net {euro(pc['net'])}**"
            )
            df = pd.DataFrame(r.yearly).rename(columns={
                "year": "Year", "interest_paid": "Interest", "principal_paid": "Repayment",
                "paid_to_bank": "Paid to bank", "remaining_balance": "Balance",
                "home_value": "Home value", "ewf": "EWF",
                "net_tax_benefit": "Net tax benefit", "box3_tax": "Box 3 tax",
                "invested_pot": "Invested pot",
            })
            for c in df.columns:
                if c != "Year":
                    df[c] = df[c].map(euro)
            st.dataframe(df, width="stretch", hide_index=True)

st.divider()
st.caption(
    "ℹ️ This is a simplified, transparent model for comparison — **not tax or financial advice**. "
    "Dutch tax rules (renteaftrek, eigenwoningforfait, box 3, NHG, startersvrijstelling) change yearly; "
    "verify and update the figures in the sidebar's tax section."
)
