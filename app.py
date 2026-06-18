"""Huis & Hypotheek — Dutch first-home buy-vs-invest comparison dashboard.

Run with:  streamlit run app.py

The UI is a thin layer over the (Streamlit-free, tested) ``mortgage`` package.
It is organised output-first: shared inputs live in the sidebar, split into
Basics and Advanced; the main canvas leads with a single, plain-language
recommendation and keeps all supporting detail in tabs.
"""

from __future__ import annotations

from html import escape
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
TYPES = {"annuity": "Annuïteitenhypotheek", "linear": "Lineaire hypotheek"}
SWEEP_VEHICLES = ["savings", "deposit", "investment"]
VEH_COLORS = {"savings": "#0f766e", "deposit": "#b45309", "investment": "#2563eb"}
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
            extra_repay_once=float(C["extra_once"]), extra_repay_annual=float(C["extra_annual"]),
        )
        for (n, t, d) in C["scenarios"]
    ]
    return tax, alt, scenarios, eff_horizon


def _metric_of(r, metric: str) -> float:
    return {"Vermogen bij verkoop": r.net_worth_end,
            "Netto resultaat": r.net_result,
            "Jaarlijks rendement": r.annual_return}[metric]


@st.cache_data(show_spinner=False)
def compute_core(snap_json: str):
    """Run every configured scenario; cached on the committed input snapshot."""
    C = json.loads(snap_json)
    tax, alt, scenarios, _ = build_models(C)
    budget = reference_budget(scenarios)
    scenario_cash = reference_cash(scenarios)
    ref_cash = max(float(C.get("cash_pool", scenario_cash)), scenario_cash)
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
            other_purchase_costs=float(C["other"]), selling_cost_rate=C["sell"], nhg=C["nhg"],
            extra_repay_once=float(C["extra_once"]), extra_repay_annual=float(C["extra_annual"]))

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
    page_title="Huis & Hypotheek — welke keuze past het best?",
    page_icon="🏠",
    layout="wide",
)

# Design system ------------------------------------------------------------- #
FONT = '"IBM Plex Sans", "Aptos", "Segoe UI", sans-serif'
DISPLAY_FONT = '"IBM Plex Serif", Georgia, serif'
INK = "#17212b"
MUTED = "#667085"
GRID = "#e7ecf2"
LINE = "#d7dee8"
GREEN = "#0f766e"
RED = "#c2410c"
AMBER = "#b7791f"
BLUE = "#2d6cdf"
ACCENTS = [BLUE, GREEN, AMBER]

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Serif:wght@600;700&display=swap');

      html, body, [class*="css"] { font-family: 'IBM Plex Sans', 'Aptos', 'Segoe UI', sans-serif; }
      .stApp { background: #f3f6f8; }
      .block-container { padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1280px; }
      h1, h2, h3, h4 { letter-spacing: 0; color: #17212b; }
      #MainMenu, footer { visibility: hidden; }

      /* ---- Hero ---- */
      .hero { display:grid; grid-template-columns:minmax(0, 1.45fr) minmax(260px, .55fr);
              gap:24px; align-items:end; margin:2px 0 18px; padding-bottom:18px;
              border-bottom:1px solid #dfe6ee; }
      .brand-kicker { color:#0f766e; font-size:.78rem; font-weight:700; text-transform:uppercase; }
      .hero h1 { font-family:'IBM Plex Serif', Georgia, serif; font-size:2.15rem;
                 line-height:1.08; margin:4px 0 8px; font-weight:700; color:#17212b; }
      .hero p  { margin:0; color:#4b5563; font-size:1rem; max-width:760px; line-height:1.55; }
      .hero-facts { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; }
      .hero-fact { background:#fff; border:1px solid #dfe6ee; border-radius:8px; padding:10px 12px; }
      .hero-fact .label { color:#667085; font-size:.72rem; }
      .hero-fact .value { color:#17212b; font-size:.98rem; font-weight:700; margin-top:2px; }

      /* ---- Recommendation card (the primary output) ---- */
      .reco { background:linear-gradient(135deg,#1e3a8a 0%, #2563eb 58%, #4f46e5 100%);
              color:#fff; border-radius:8px; padding:24px 28px; margin:0 0 12px;
              box-shadow:0 18px 40px -24px rgba(37,99,235,.7); }
      .reco-badge { display:inline-block; background:rgba(255,255,255,.18); color:#fff;
              font-weight:700; font-size:.78rem; padding:5px 10px; border-radius:6px; }
      .reco-title { font-family:'IBM Plex Serif', Georgia, serif; font-size:1.72rem;
              font-weight:700; margin: 14px 0 4px; line-height:1.2; max-width:900px; }
      .reco-title b { color:#fff; }
      .reco-sub { font-size:1rem; color:rgba(255,255,255,.92); margin: 8px 0 18px; max-width:880px; line-height:1.55; }
      .reco-stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; }
      .reco-stat { background:rgba(255,255,255,.13); color:#fff; border:1px solid rgba(255,255,255,.2);
              border-radius:8px; padding:13px 14px; min-width:0; }
      .reco-stat .l { font-size:.73rem; color:rgba(255,255,255,.78); }
      .reco-stat .v { font-size:1.24rem; font-weight:700; margin-top:3px; }
      .reco-stat .d { font-size:.78rem; color:rgba(255,255,255,.82); margin-top:2px; }

      .compare-note { background:#fff; border:1px solid #dfe6ee; border-radius:8px;
              padding:12px 14px; color:#475467; font-size:.92rem; margin-bottom:18px; }

      /* ---- Scenario column headers ---- */
      .section-title { margin:22px 0 10px; color:#17212b; font-size:1rem; font-weight:700; }
      .scenario-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(240px, 1fr)); gap:12px; }
      .scenario-card { background:#fff; border:1px solid #dfe6ee; border-top:4px solid var(--accent);
              border-radius:8px; padding:15px 16px 14px; box-shadow:0 1px 2px rgba(23,33,43,.04); }
      .scenario-card.is-win { background:#fbfffd; border-color:#dfe6ee; border-top-color:var(--accent);
              box-shadow:0 18px 38px -30px rgba(15,118,110,.75); }
      .scenario-top { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; margin-bottom:12px; }
      .scenario-name { font-size:1rem; font-weight:700; color:#17212b; }
      .scenario-type { font-size:.78rem; font-weight:600; color:var(--accent); margin-top:1px; }
      .win-badge { background:#dff7ee; color:#0f766e; border-radius:6px; padding:2px 7px;
              font-size:.7rem; font-weight:700; white-space:nowrap; }
      .scenario-value { font-size:1.45rem; line-height:1.1; color:#17212b; font-weight:700; margin-bottom:3px; }
      .scenario-label { font-size:.76rem; color:#667085; margin-bottom:12px; }
      .metric-list { display:grid; gap:8px; border-top:1px solid #edf1f5; padding-top:10px; }
      .metric-row { display:flex; justify-content:space-between; gap:12px; font-size:.88rem; }
      .metric-row span:first-child { color:#667085; }
      .metric-row span:last-child { color:#17212b; font-weight:700; text-align:right; }

      /* ---- Tabs / sidebar polish ---- */
      .stTabs [data-baseweb="tab"] { font-weight:600; color:#475467; padding-top:8px; padding-bottom:8px; }
      .stTabs [aria-selected="true"] { color:#0f766e; }
      section[data-testid="stSidebar"] { background:#ffffff; border-right:1px solid #dfe6ee; }
      section[data-testid="stSidebar"] h2 { font-size:1.02rem; margin-top:.15rem; }
      section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap:.72rem; }
      .side-kicker { font-size:.72rem; font-weight:700; letter-spacing:0; text-transform:uppercase;
                     color:#0f766e; margin: 2px 0 -4px 0; }
      .side-summary { background:#f3f6f8; border:1px solid #dfe6ee; border-radius:8px;
                      padding:9px 10px; color:#475467; font-size:.82rem; line-height:1.35; }
      div[data-testid="stExpander"] { border-color:#dfe6ee; border-radius:8px; background:#fff; }
      div[data-testid="stButton"] button { border-radius:8px; font-weight:700;
              background:linear-gradient(135deg,#1e3a8a 0%, #2563eb 58%, #4f46e5 100%);
              border-color:#2563eb; color:#fff; }
      div[data-testid="stButton"] button:hover {
              background:linear-gradient(135deg,#172e6e 0%, #1f57c9 58%, #4339c4 100%);
              border-color:#1f57c9; color:#fff; }

      @media (max-width: 900px) {
        .hero { grid-template-columns:1fr; }
        .hero-facts, .reco-stats { grid-template-columns:1fr; }
      }
    </style>
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


DEFAULTS = [
    dict(name="Annuïteit", down=0, typ="annuity"),
    dict(name="Lineair", down=0, typ="linear"),
    dict(name="Meer eigen geld", down=50_000, typ="annuity"),
]


# --------------------------------------------------------------------------- #
# Sidebar — all shared inputs, split into Basics and Advanced
# --------------------------------------------------------------------------- #
with st.sidebar:
    # Rendered into below, after inputs are read — kept at the very top so it's
    # always visible without scrolling.
    calc_slot = st.container()
    st.divider()
    st.markdown('<div class="side-kicker">Jouw situatie</div>', unsafe_allow_html=True)
    st.header("Basisgegevens")
    st.caption("Deze gegevens gelden voor alle scenario's en bepalen het grootste deel van de uitkomst.")

    price = st.number_input("Koopsom woning", 50_000, 2_000_000, 280_000, 5_000,
                            help="De aankoopprijs van de woning die je wilt vergelijken.")
    income = st.number_input("Bruto jaarinkomen", 0, 1_000_000, 60_000, 1_000,
                             help="Bepaalt je belastingschijf en daarmee de waarde van de hypotheekrenteaftrek.")

    cash_pool = st.number_input(
        "Eigen geld (totaal beschikbaar)", 0, 2_000_000, 50_000, 5_000, key="cash_pool",
        help="Al je eigen geld bij aankoop. Per scenario kies je hoeveel hiervan als eigen "
             "inbreng in de woning gaat; de rest is vrij geld.")
    vehicle = st.radio(
        "Vrij geld gaat naar:",
        options=list(CASH_VEHICLES.keys()),
        format_func=lambda k: CASH_VEHICLES[k],
        index=0,
        help="Wat gebeurt er met het eigen geld dat je niet in de woning stopt (vrij geld)? "
             "Spaarrekening, deposito of beleggen: het wordt aan het begin weggezet en het "
             "maandelijkse verschil in lasten komt er bij; het groeit na kosten en box 3. "
             "‘Extra aflossen op de woning’: het vrije geld wordt juist gebruikt om de hypotheek "
             "vervroegd af te lossen (1× per jaar), wat rente bespaart. Rendementen stel je fijn "
             "bij onder ‘Verfijnen’.")

    horizon = st.slider("Aantal jaren tot verkoop", 1, 30, 5,
                        help="Hoe lang je de woning naar verwachting houdt. De vergelijking loopt over deze periode.")
    rate = st.number_input("Hypotheekrente %", 0.0, 15.0, 3.9, 0.05,
                           help="De jaarlijkse nominale hypotheekrente.") / 100
    appr = st.number_input("Verwachte waardestijging % per jaar", -10.0, 20.0, 3.0, 0.25,
                           help="De gemiddelde jaarlijkse waardestijging van de woning. Dit heeft veel invloed op de uitkomst.") / 100

    st.divider()
    st.header("Scenario's vergelijken")
    st.caption("Kies per scenario de hypotheekvorm en de eigen inbreng. De rest van je "
               "eigen geld blijft vrij geld.")
    n_scenarios = st.radio("Aantal varianten", [1, 2, 3], index=2, horizontal=True,
                           label_visibility="collapsed", key="n_scenarios")
    cash_cap = int(min(int(price), int(cash_pool)))
    live_scenarios: list[tuple[str, str, float]] = []
    ab_down = 0.0
    for i in range(n_scenarios):
        d = DEFAULTS[i]
        with st.expander(f"Scenario {chr(65 + i)} · {d['name']}", expanded=i == 0):
            name = st.text_input("Naam scenario", d["name"], key=f"name{i}")
            mtype = st.selectbox("Hypotheekvorm", list(TYPES), index=list(TYPES).index(d["typ"]),
                                 format_func=lambda k: TYPES[k], key=f"type{i}")
            if i == 1:  # Scenario B follows Scenario A's cash automatically.
                down = ab_down
                st.number_input("Eigen inbreng in de woning", value=int(ab_down), disabled=True, key="down_locked1")
                st.caption("Gelijk aan scenario A.")
            else:
                down = st.number_input(
                    "Eigen inbreng in de woning", 0, cash_cap, min(int(d["down"]), cash_cap), 5_000,
                    key=f"down{i}",
                    help="Hoeveel van je eigen geld in deze woning gaat. De hypotheek is de koopsom "
                         "minus dit bedrag; wat je niet inbrengt, blijft vrij geld.")
                if i == 0:
                    ab_down = float(down)
            loan = max(0.0, price - down)
            vrij = max(0.0, cash_pool - down)
            st.markdown(
                f'<div class="side-summary">{TYPES[mtype]} · verkoop na {horizon} jaar<br>'
                f'Eigen inbreng <b>{euro(down)}</b> · hypotheek <b>{euro(loan)}</b><br>'
                f'Vrij geld <b>{euro(vrij)}</b> → {CASH_VEHICLES[vehicle].lower()}</div>',
                unsafe_allow_html=True,
            )

            live_scenarios.append((name, mtype, float(down)))

    st.divider()
    st.markdown('<div class="side-kicker">Verfijnen</div>', unsafe_allow_html=True)
    st.caption("De standaardwaarden zijn gebaseerd op Nederlandse uitgangspunten voor 2025.")

    with st.expander("Hypotheekgegevens"):
        term = st.slider("Looptijd hypotheek in jaren", 5, 30, 30)
        fixed = st.selectbox("Rentevaste periode in jaren", [1, 5, 10, 20, 30], index=2)
        nhg = st.checkbox("NHG gebruiken", value=True)

    with st.expander("Extra aflossen"):
        st.caption("Los je naast de reguliere maandlast extra af op de hypotheek? Dat verlaagt de "
                   "schuld en bespaart rente. Dit staat los van ‘Vrij geld gaat naar’ en geldt voor "
                   "alle scenario's.")
        extra_amount = st.number_input(
            "Extra aflossing", 0, 1_000_000, 0, 1_000,
            help="Bedrag dat je extra aflost, bovenop de reguliere aflossing. Dit is geld uit eigen "
                 "zak en telt mee in je totale inleg.")
        extra_freq = st.radio("Frequentie", ["Eenmalig", "Per jaar"], index=0, horizontal=True,
                              help="Eenmalig bij aankoop, of elk jaar (gemiddeld) tijdens de looptijd.")
    extra_once = extra_amount if extra_freq == "Eenmalig" else 0
    extra_annual = extra_amount if extra_freq == "Per jaar" else 0

    with st.expander("Rendement op vrij geld"):
        st.caption("Verwacht rendement per bestemming van het vrije geld. De bestemming zelf "
                   "kies je onder Basisgegevens (‘Vrij geld gaat naar’).")
        sav_rate = st.number_input("Spaarrente % per jaar", 0.0, 15.0, 2.25, 0.1) / 100
        dep_rate = st.number_input("Depositorente % per jaar", 0.0, 15.0, 3.0, 0.1) / 100
        inv_ret = st.number_input("Beleggingsrendement % per jaar", -10.0, 25.0, 6.0, 0.5) / 100
        fee = st.number_input("Beleggingskosten % per jaar", 0.0, 5.0, 0.3, 0.05,
                              help="Jaarlijkse kosten van de portefeuille, bijvoorbeeld fonds- of ETF-kosten.") / 100

    with st.expander("Aankoop- en verkoopkosten"):
        other = st.number_input("Overige aankoopkosten (notaris, taxatie, advies)",
                                0, 50_000, 4_000, 250)
        sell = st.number_input("Verkoopkosten % (makelaar e.d.)", 0.0, 10.0, 1.5, 0.1) / 100

    with st.expander("Belastinginstellingen (2025)"):
        st.caption("Pas deze bedragen aan wanneer regels of percentages veranderen.")
        st.markdown("**Box 1 — eigen woning**")
        ewf_rate = st.number_input("Eigenwoningforfait %", 0.0, 2.0, 0.35, 0.01) / 100
        max_ded = st.number_input("Maximaal aftrekpercentage rente %", 0.0, 60.0, 37.48, 0.1) / 100
        hillen = st.number_input("Percentage Wet Hillen %", 0.0, 100.0, 76.67, 0.5) / 100
        st.markdown("**Eenmalige aankoopbelasting**")
        starters = st.checkbox("Startersvrijstelling overdrachtsbelasting", value=True)
        transfer = st.number_input("Overdrachtsbelasting zonder vrijstelling %", 0.0, 10.0, 2.0, 0.1) / 100
        nhg_rate = st.number_input("NHG-premie %", 0.0, 5.0, 0.6, 0.05) / 100
        st.markdown("**Box 3 — vrij vermogen**")
        b3_rate = st.number_input("Belastingtarief box 3 %", 0.0, 60.0, 36.0, 0.5) / 100
        b3_save = st.number_input("Fictief rendement spaargeld/deposito %", 0.0, 15.0, 1.44, 0.1) / 100
        b3_inv = st.number_input("Fictief rendement beleggingen %", 0.0, 15.0, 5.88, 0.1) / 100
        b3_allow = st.number_input("Heffingsvrij vermogen (alleenstaand)", 0, 200_000, 57_684, 1_000)

    eff_horizon = min(horizon, term)
    if horizon > term:
        st.warning(f"De periode tot verkoop ({horizon} jaar) is beperkt tot de hypotheeklooptijd ({term} jaar).")

# Shared inputs are collected into a snapshot below. The model only reruns when
# the user clicks Bereken, not on every widget change.

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
    cash_pool=int(cash_pool),
    extra_once=int(extra_once), extra_annual=int(extra_annual),
    scenarios=live_scenarios,
)

calc_clicked = calc_slot.button("Bereken", type="primary", use_container_width=True,
                                help="Pas je invoer toe en bereken de resultaten opnieuw.")
if calc_clicked or "committed" not in st.session_state:
    st.session_state.committed = live

C = st.session_state.committed
snap_json = json.dumps(C, sort_keys=True)
if live != C:
    calc_slot.caption("⚠️ Invoer gewijzigd — klik op **Bereken** om de resultaten bij te werken.")

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


def build_hero_html() -> str:
    return f"""
    <div class="hero">
      <div>
        <div class="brand-kicker">Hypotheekvergelijker</div>
        <h1>Welke hypotheekkeuze levert jou het meeste op?</h1>
        <p>Vergelijk eigen inbreng, hypotheekvorm en strategie voor vrij geld in een Nederlands rekenmodel
           met renteaftrek, eigenwoningforfait, NHG en box 3.</p>
      </div>
      <div class="hero-facts">
        <div class="hero-fact"><div class="label">Koopsom</div><div class="value">{euro(price)}</div></div>
        <div class="hero-fact"><div class="label">Periode</div><div class="value">{eff_horizon} jaar</div></div>
        <div class="hero-fact"><div class="label">Scenario's</div><div class="value">{len(results)}</div></div>
      </div>
    </div>
    """


# --------------------------------------------------------------------------- #
# THE RECOMMENDATION — primary output
# --------------------------------------------------------------------------- #
def reason_line(ws, wr, rs, rr) -> str:
    """One plain-language sentence on why the winner wins, vs the runner-up."""
    net_rate = alt.net_rate()
    dp_diff = ws.down_payment - rs.down_payment
    if abs(dp_diff) > 1_000:
        if alt.repays:
            # Free cash is used to repay too, so down payment vs free cash is mostly
            # a timing difference — both pay the loan down.
            if dp_diff > 0:
                return (f"Meer eigen geld meteen in de woning ({euro(ws.down_payment)} tegenover "
                        f"{euro(rs.down_payment)}) wint hier net: je verlaagt de schuld direct in plaats "
                        f"van pas bij de jaarlijkse extra aflossing, en bespaart zo iets meer rente.")
            return (f"Minder eigen geld vooraf en de rest jaarlijks extra aflossen werkt hier nipt beter: "
                    f"je houdt langer geld achter de hand terwijl de hypotheek alsnog versneld omlaag gaat.")
        if dp_diff > 0:
            return (f"Meer eigen geld in de woning ({euro(ws.down_payment)} tegenover "
                    f"{euro(rs.down_payment)}) pakt hier het beste uit: je hypotheekrente van {pct(ws.interest_rate)} "
                    f"is hoger dan het rendement van {pct(net_rate)} op vrij geld, na kosten en box 3.")
        return (f"Minder eigen geld in de woning en meer vrij geld werkt hier beter: dat geld levert "
                f"{pct(net_rate)} op na kosten en box 3, meer dan je hypotheekrente van {pct(ws.interest_rate)}.")
    if ws.mortgage_type != rs.mortgage_type:
        if ws.mortgage_type == "linear":
            return ("De lineaire hypotheek wint: je lost sneller af en betaalt minder rente. "
                    "De vergelijking houdt rekening met de hogere maandlasten in het begin.")
        return ("De annuïteitenhypotheek wint: de lagere maandlasten in het begin laten meer ruimte over "
                "voor vrij geld, wat hier gunstig uitpakt.")
    return f"Dit scenario houdt na belasting, rente en kosten het meeste over na {ws.horizon_years} jaar."


def build_reco_html() -> str:
    ws, wr = ranked[0]
    winner = escape(ws.name)
    profit_sign = "winst" if wr.net_result >= 0 else "verlies"

    if len(ranked) == 1:
        verdict = ("een positief resultaat" if wr.net_result > 0 else
                   "een negatief resultaat. Kijk nog eens naar koopsom, looptijd of rente")
        title = f"Na {ws.horizon_years} jaar geeft <b>{winner}</b> {verdict}"
        sub = (f"Bij verkoop houd je naar schatting <b>{euro(wr.net_worth_end)}</b> over: een netto {profit_sign} van "
               f"{euro(wr.net_result)}, oftewel {pct(wr.annual_return)} per jaar.")
        stats = [
            ("Over bij verkoop", euro(wr.net_worth_end), "overwaarde + vrij geld"),
            ("Netto resultaat", euro(wr.net_result), "na je totale inleg"),
            ("Rendement per jaar", pct(wr.annual_return), "kasstroomgewogen"),
            ("Maandlast start", euro(wr.monthly_payment_start), "eerste maand"),
        ]
    else:
        rs, rr = ranked[1]
        runner = escape(rr.name)
        margin = wr.net_result - rr.net_result
        if margin < 2_500:
            title = f"Het ligt dicht bij elkaar: <b>{winner}</b> komt net bovenaan"
            sub = (f"<b>{winner}</b> is over {ws.horizon_years} jaar maar {euro(margin)} beter dan "
                   f"<b>{runner}</b>. Betalingszekerheid en flexibiliteit mogen hier dus zwaar meewegen. "
                   + reason_line(ws, wr, rs, rr))
        else:
            title = f"Op basis van je invoer is <b>{winner}</b> financieel het sterkst"
            sub = reason_line(ws, wr, rs, rr)
        stats = [
            ("Over bij verkoop", euro(wr.net_worth_end), "overwaarde + vrij geld"),
            ("Netto resultaat", euro(wr.net_result), f"{pct(wr.annual_return)} per jaar"),
            (f"Voorsprong op {runner}", f"+{euro(margin)}", "extra netto resultaat"),
            ("Maandlast start", euro(wr.monthly_payment_start), "eerste maand"),
        ]

    chips = "".join(
        f'<div class="reco-stat"><div class="l">{l}</div>'
        f'<div class="v">{v}</div><div class="d">{d}</div></div>'
        for l, v, d in stats
    )
    return (
        '<div class="reco">'
        '<div class="reco-copy">'
        '<span class="reco-badge">Advies op basis van je invoer</span>'
        f'<div class="reco-title">{title}</div>'
        f'<div class="reco-sub">{sub}</div>'
        '</div>'
        f'<div class="reco-stats">{chips}</div>'
        '</div>'
    )


def build_comparison_note_html() -> str:
    if alt.repays:
        body = (
            f"Elk scenario start met hetzelfde eigen geld ({euro(ref_cash)}) en hetzelfde maandbudget van "
            f"{euro(budget)}. Wat niet als eigen inbreng in de woning gaat, is vrij geld en wordt gebruikt "
            f"om de hypotheek 1× per jaar extra af te lossen. Rangschikking op netto resultaat."
        )
    elif alt.invests:
        body = (
            f"Elk scenario start met hetzelfde eigen geld ({euro(ref_cash)}) en hetzelfde maandbudget van "
            f"{euro(budget)}. Wat niet als eigen inbreng in de woning gaat, is vrij geld en gaat naar "
            f"{CASH_VEHICLES[vehicle].lower()} na kosten en box 3. Rangschikking op netto resultaat."
        )
    else:
        body = ("Vrij geld wordt niet meegenomen. Elk scenario wordt beoordeeld op de eigen inbreng "
                "en maandlast. Rangschikking op netto resultaat.")
    if C["extra_once"] or C["extra_annual"]:
        if C["extra_once"]:
            extra = f"eenmalig {euro(C['extra_once'])} extra afgelost bij aankoop"
        else:
            extra = f"jaarlijks {euro(C['extra_annual'])} extra afgelost"
        body += f" In elk scenario wordt {extra} uit eigen zak."
    return f'<div class="compare-note">{escape(body)}</div>'


def build_scenario_cards_html() -> str:
    cards = []
    for i, (s, r) in enumerate(pairs):
        accent = ACCENTS[i % len(ACCENTS)]
        is_win = r.name == winner_name
        rows = [
            ("Vermogen bij verkoop", euro(r.net_worth_end)),
            ("Maandlast start", euro(r.monthly_payment_start)),
            ("Eigen inbreng in woning", euro(s.down_payment)),
            ("Resterende hypotheek", euro(r.remaining_balance)),
        ]
        if alt.repays:
            rows.append(("Extra afgelost met vrij geld", euro(r.extra_repaid_from_free_cash)))
            if r.side_pot_end > 1:  # cash left once the loan was fully repaid
                rows.append(("Resterend vrij geld (cash)", euro(r.side_pot_end)))
        elif alt.invests:
            rows.append(("Spaar-/beleggingspot bij verkoop", euro(r.side_pot_end)))
        if r.extra_repaid_explicit > 1:
            rows.append(("Extra afgelost (eigen zak)", euro(r.extra_repaid_explicit)))
        row_html = "".join(
            f'<div class="metric-row"><span>{label}</span><span>{value}</span></div>'
            for label, value in rows
        )
        badge = '<span class="win-badge">Beste keuze</span>' if is_win else ""
        classes = "scenario-card is-win" if is_win else "scenario-card"
        cards.append(
            f'<div class="{classes}" style="--accent:{accent}">'
            '<div class="scenario-top">'
            f'<div><div class="scenario-name">{escape(r.name)}</div>'
            f'<div class="scenario-type">{TYPES[s.mortgage_type]}</div></div>{badge}'
            '</div>'
            f'<div class="scenario-value">{euro(r.net_result)}</div>'
            f'<div class="scenario-label">Netto resultaat · {pct(r.annual_return)} per jaar</div>'
            f'<div class="metric-list">{row_html}</div>'
            '</div>'
        )
    return '<div class="scenario-grid">' + "".join(cards) + '</div>'


st.markdown(build_hero_html(), unsafe_allow_html=True)
st.markdown(build_reco_html(), unsafe_allow_html=True)
st.markdown(build_comparison_note_html(), unsafe_allow_html=True)

# --------------------------------------------------------------------------- #
# KPI cards — winner highlighted
# --------------------------------------------------------------------------- #
st.markdown('<div class="section-title">Scenario-overzicht</div>', unsafe_allow_html=True)
st.markdown(build_scenario_cards_html(), unsafe_allow_html=True)

# --------------------------------------------------------------------------- #
# Detailed analysis — everything in tabs
# --------------------------------------------------------------------------- #
st.markdown('<div class="section-title">Verdieping</div>', unsafe_allow_html=True)
t_optim, t_time, t_wealth, t_profit, t_bank, t_table, t_year = st.tabs([
    "Eigen inbreng", "Vermogen door de tijd", "Opbouw vermogen",
    "Resultaat verklaard", "Naar de bank", "Vergelijking", "Jaaroverzicht",
])

# --- Optimal down payment sweep --- #
with t_optim:
    st.caption(
        "**Hoeveel van je eigen geld kun je het best als eigen inbreng in de woning stoppen?** "
        "De grafiek houdt je totale eigen geld en maandbudget gelijk. Wat je niet inbrengt, blijft "
        "vrij geld en gaat naar sparen, deposito of beleggen. Elke lijn toont een combinatie van "
        "hypotheekvorm en bestemming van vrij geld. De gemarkeerde punten zijn jouw scenario's."
    )
    oc1, oc2 = st.columns([2, 3])
    with oc1:
        cash_avail = st.number_input(
            "Eigen geld voor deze grafiek", 0, int(price),
            min(int(ref_cash) if ref_cash > 0 else 100_000, int(price)), 5_000,
            help="De X-as loopt van €0 eigen inbreng tot dit bedrag. Standaard sluit dit aan op je scenario's.")
    with oc2:
        metric = st.radio("Optimaliseren op",
                          ["Netto resultaat", "Vermogen bij verkoop", "Jaarlijks rendement"],
                          horizontal=True,
                          help="Netto resultaat laat zien wat de keuze oplevert nadat je totale inleg is verrekend.")

    cap = min(float(cash_avail), float(price))
    is_pct = metric == "Jaarlijks rendement"

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
                hovertemplate=f"{label}<br>Eigen inbreng €%{{x:,.0f}}<br>{metric}: %{{customdata}}<extra></extra>",
                customdata=[pct(y) if is_pct else euro(y) for y in c["ys"]]))
            # subdued per-curve sweet-spot marker
            fig5.add_trace(go.Scatter(
                x=[c["best_d"]], y=[c["best_y"]], mode="markers",
                marker=dict(size=8, color=VEH_COLORS[v], symbol="circle",
                            opacity=0.55, line=dict(width=1, color="#fff")),
                showlegend=False,
                hovertemplate=f"{label}<br>Beste eigen inbreng: €%{{x:,.0f}}<extra></extra>"))

    # The single overall best, marked unmistakably.
    bc = curves[best_key]
    fig5.add_trace(go.Scatter(
        x=[bc["best_d"]], y=[bc["best_y"]], mode="markers",
        marker=dict(size=20, color="#f59e0b", symbol="star",
                    line=dict(width=2, color="#0f172a")),
        name="Beste keuze",
        hovertemplate=f"Beste keuze<br>Eigen inbreng €%{{x:,.0f}}<extra></extra>"))

    # Overlay the configured scenarios as labelled dots, using your configured vehicle.
    for dot_name, dot_down, dot_y in dots:
        fig5.add_trace(go.Scatter(
            x=[dot_down], y=[dot_y], mode="markers+text",
            marker=dict(size=10, color="#0f172a", line=dict(width=2, color="#fff")),
            text=[f" {dot_name}"], textposition="bottom center",
            textfont=dict(size=11, color=MUTED), showlegend=False,
            hovertemplate=f"{dot_name}<br>Eigen inbreng €%{{x:,.0f}}<extra></extra>"))

    fig5.update_layout(xaxis_title="Eigen inbreng in de woning bij aankoop")
    style_fig(fig5, 500, metric, money_y=not is_pct)
    fig5.update_xaxes(tickprefix="€", tickformat="~s")
    fig5.update_layout(
        showlegend=True,
        margin=dict(l=8, r=8, t=78, b=30),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.08,
            xanchor="left",
            x=0,
            bgcolor="rgba(255,255,255,.92)",
            bordercolor="#dfe6ee",
            borderwidth=1,
            font=dict(size=12),
        ),
    )
    if is_pct:
        fig5.update_yaxes(tickprefix="", tickformat=".1%")
    st.plotly_chart(fig5, width="stretch")

    if cap <= 0:
        st.info("Vul meer dan €0 beschikbaar eigen geld in om de lijnen te zien.")
    else:
        def fmt(v):
            return pct(v) if is_pct else euro(v)

        def edge_note(c) -> str:
            if c["best_i"] >= N_SWEEP - 1:
                return ("blijft stijgen tot de rand. Binnen dit bedrag is meer eigen inbreng dus gunstiger; "
                        "verhoog het beschikbare eigen geld om te zien waar het kantelpunt ligt")
            if c["best_i"] <= 0:
                return "is het hoogst bij €0 eigen inbreng. Vrij geld werkt hier beter"
            return f"is het sterkst bij **{euro(c['best_d'])}** eigen inbreng"

        bv, bmt = best_key
        st.success(
            f"Beste uitkomst: breng **{euro(bc['best_d'])}** van je {euro(cap)} in met een "
            f"**{TYPES[bmt].lower()}** en zet de rest op **{CASH_VEHICLES[bv].lower()}**. "
            f"Dat geeft een {metric.lower()} van **{fmt(bc['best_y'])}**."
        )
        # One line per vehicle: its better mortgage type and where that curve peaks.
        lines = " ".join(
            (lambda bmt_v, c: f"**{CASH_VEHICLES[v]}**: beste combinatie met "
                              f"{TYPES[bmt_v].lower()}, {fmt(c['best_y'])}; de lijn {edge_note(c)}.")(
                bmt_v := max(TYPES, key=lambda mt: curves[(v, mt)]["best_y"]),
                curves[(v, bmt_v)])
            for v in SWEEP_VEHICLES
        )
        st.caption(lines)
        st.caption("De uitkomst ligt vaak aan een uiteinde: niets inleggen of juist veel inleggen. "
                   "Als rendement na belasting hoger is dan de netto hypotheekkosten, loont vrij geld; "
                   "anders werkt extra aflossen meestal beter.")
        if is_pct:
            st.caption("Let op: jaarlijks rendement piekt vaak bij lage eigen inbreng door hefboomwerking. "
                       "Netto resultaat en vermogen bij verkoop kijken meer naar absolute euro's.")
        elif cap != ref_cash:
            st.caption(f"Je verkent nu {euro(cap)} beschikbaar geld; je scenario's gebruiken {euro(ref_cash)}. "
                       "Zet het beschikbare eigen geld gelijk aan je scenario's om de punten exact te laten aansluiten."
                       .replace(",", "."))

# --- Net worth over time --- #
with t_time:
    st.caption("Overwaarde na verkoopkosten en resterende hypotheek, plus vrij geld per jaar.")
    fig = go.Figure()
    for i, (s, r) in enumerate(pairs):
        years = [row["year"] for row in r.yearly]
        nw = [row["home_value"] * (1 - s.selling_cost_rate) - row["remaining_balance"] + row["invested_pot"]
              for row in r.yearly]
        fig.add_trace(go.Scatter(x=years, y=nw, mode="lines+markers", name=r.name,
                                 line=dict(width=3, color=ACCENTS[i % len(ACCENTS)])))
    fig.update_layout(xaxis_title="Jaar")
    st.plotly_chart(style_fig(fig, 420, "Vermogen"), width="stretch")

# --- Wealth breakdown --- #
with t_wealth:
    st.caption("Zo is het vermogen bij verkoop opgebouwd: verkoopopbrengst na kosten, min resterende hypotheek, "
               "plus vrij geld.")
    names = [r.name for r in results]
    fig3 = go.Figure()
    fig3.add_trace(go.Bar(name="Netto verkoopopbrengst", x=names,
                          y=[r.home_value_end - r.selling_costs for r in results], marker_color=GREEN))
    fig3.add_trace(go.Bar(name="- Resterende hypotheek", x=names,
                          y=[-r.remaining_balance for r in results], marker_color=RED))
    fig3.add_trace(go.Bar(name="Vrij geld", x=names,
                          y=[r.side_pot_end for r in results], marker_color=ACCENTS[0]))
    fig3.update_layout(barmode="relative")
    st.plotly_chart(style_fig(fig3, 380), width="stretch")

# --- Where profit comes from --- #
with t_profit:
    st.caption("Aflossen is geen winst: je zet geld om in overwaarde. Het netto resultaat komt vooral uit "
               "waardestijging, rente, aankoop- en verkoopkosten, belastingvoordeel en rendement op vrij geld.")
    if alt.repays:
        st.caption("Je vrije geld lost de hypotheek af, dus ‘rendement vrij geld’ is hier €0; het voordeel "
                   "zie je terug in een lagere post **betaalde rente**.")
    pcols = st.columns(len(results))
    for i, (col, s, r) in enumerate(zip(pcols, scenarios, results)):
        appreciation = r.home_value_end - s.house_price
        labels = ["Waarde-<br>stijging", "Betaalde<br>rente", "Aankoop-<br>kosten",
                  "Verkoop-<br>kosten", "Belasting-<br>voordeel", "Rendement<br>vrij geld", "Netto<br>resultaat"]
        values = [appreciation, -r.total_interest, -r.purchase_costs["net"],
                  -r.selling_costs, r.total_tax_benefit, r.side_pot_gain, r.net_result]
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
            st.plotly_chart(style_fig(wf, 360), width="stretch", key=f"wf_{i}")

# --- Paid to the bank --- #
with t_bank:
    st.caption("Elke staaf toont de betalingen aan de bank per jaar. Volle kleur is aflossing; gearceerd is rente. "
               "Bij annuïteit blijft de maandlast gelijker, bij lineair daalt die sneller.")
    fig4 = go.Figure()
    for i, r in enumerate(results):
        years = [row["year"] for row in r.yearly]
        base = ACCENTS[i % len(ACCENTS)]
        fig4.add_trace(go.Bar(x=years, y=[row["principal_paid"] for row in r.yearly],
                              name=f"{r.name} · aflossing", offsetgroup=str(i), legendgroup=r.name,
                              marker_color=base,
                              hovertemplate="Jaar %{x}<br>Aflossing €%{y:,.0f}<extra></extra>"))
        fig4.add_trace(go.Bar(x=years, y=[row["interest_paid"] for row in r.yearly],
                              name=f"{r.name} · rente", offsetgroup=str(i), legendgroup=r.name,
                              marker_color=base, opacity=0.45, marker_pattern_shape="/",
                              hovertemplate="Jaar %{x}<br>Rente €%{y:,.0f}<extra></extra>"))
    fig4.update_layout(barmode="stack", xaxis_title="Jaar")
    st.plotly_chart(style_fig(fig4, 420, "Betaald aan de bank"), width="stretch")

# --- Full comparison table --- #
with t_table:
    table = pd.DataFrame({
        r.name: {
            "Koopsom woning": euro(s.house_price),
            "Eigen geld": euro(s.down_payment),
            "Hypotheek": euro(s.loan_amount),
            "Aankoopkosten netto": euro(r.purchase_costs["net"]),
            "Maandlast start": euro(r.monthly_payment_start),
            "Startbedrag vrij geld": euro(r.invested_cash_start),
            "Maandruimte vrij geld": euro(r.spare_invested_total),
            "Woningwaarde bij verkoop": euro(r.home_value_end),
            "Resterende hypotheek": euro(r.remaining_balance),
            "Verkoopkosten": euro(r.selling_costs),
            "Vrij geld bij verkoop": euro(r.side_pot_end),
            "Extra afgelost (vrij geld)": euro(r.extra_repaid_from_free_cash),
            "Extra afgelost (eigen zak)": euro(r.extra_repaid_explicit),
            "Totale rente": euro(r.total_interest),
            "Totale aflossing (incl. extra)": euro(r.total_principal_repaid),
            "Totaal betaald aan bank": euro(r.total_interest + r.total_principal_repaid),
            "Netto belastingvoordeel box 1": euro(r.total_tax_benefit),
            "Box 3-belasting op pot": euro(r.total_box3_tax),
            "Totale eigen inleg": euro(r.total_contributed),
            "Vermogen bij verkoop": euro(r.net_worth_end),
            "Netto resultaat": euro(r.net_result),
            "Jaarlijks rendement": pct(r.annual_return),
        }
        for s, r in pairs
    })
    st.dataframe(table, width="stretch")

# --- Yearly detail --- #
with t_year:
    for s, r in pairs:
        with st.expander(f"{r.name} — aankoopkosten en jaaroverzicht"):
            pc = r.purchase_costs
            st.write(
                f"**Aankoopkosten (kosten koper):** overdrachtsbelasting {euro(pc['transfer_tax'])} · "
                f"NHG-premie {euro(pc['nhg_premium'])} · overig {euro(pc['other'])} · "
                f"aftrek financieringskosten -{euro(pc['financing_deduction'])} → **netto {euro(pc['net'])}**"
            )
            df = pd.DataFrame(r.yearly).rename(columns={
                "year": "Jaar", "interest_paid": "Rente", "principal_paid": "Aflossing",
                "paid_to_bank": "Betaald aan bank", "remaining_balance": "Restschuld",
                "home_value": "Woningwaarde", "ewf": "EWF",
                "net_tax_benefit": "Netto belastingvoordeel", "box3_tax": "Box 3-belasting",
                "invested_pot": "Vrij geld",
            })
            for c in df.columns:
                if c != "Jaar":
                    df[c] = df[c].map(euro)
            st.dataframe(df, width="stretch", hide_index=True)

st.divider()
st.caption(
    "ℹ️ Dit is een vereenvoudigd en transparant rekenmodel om scenario's te vergelijken. "
    "Het is **geen belasting- of financieel advies**. Nederlandse regels voor renteaftrek, "
    "eigenwoningforfait, box 3, NHG en startersvrijstelling veranderen; controleer de percentages links."
)
