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
    page_title="Huis & Hypotheek — welke keuze past het best?",
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
ACCENTS = ["#2563eb", "#0f766e", "#b45309"]

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

      html, body, [class*="css"] { font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif; }
      .stApp { background: #f7f9fc; }
      .block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1320px; }
      h1, h2, h3, h4 { letter-spacing: 0; color: #0f172a; }
      #MainMenu, footer { visibility: hidden; }

      /* ---- Hero ---- */
      .hero { background:#fff; border:1px solid #e6edf5; border-left:5px solid #0f766e;
              border-radius:8px; padding:18px 22px 16px; margin-bottom:18px;
              box-shadow:0 1px 2px rgba(15,23,42,.04); }
      .hero h1 { font-size: 1.55rem; margin: 0 0 5px 0; font-weight: 800; }
      .hero p  { margin: 0; color: #475569; font-size: .98rem; max-width: 820px; line-height:1.5; }
      .pill { display:inline-block; background:#ecfdf5; color:#0f766e; font-weight:600;
              padding:3px 10px; border-radius:8px; font-size:.74rem; margin:10px 6px 0 0; }

      /* ---- Recommendation card (the primary output) ---- */
      .reco { background:#102a2c; color:#fff; border-radius:8px; padding:24px 28px;
              margin: 2px 0 10px 0; box-shadow: 0 14px 30px -22px rgba(15,23,42,.6);
              border:1px solid rgba(255,255,255,.12); }
      .reco-badge { display:inline-block; background:#d1fae5; color:#065f46;
              font-weight:700; letter-spacing:0; font-size:.72rem;
              padding:5px 10px; border-radius:8px; }
      .reco-title { font-size: 1.55rem; font-weight:800; margin: 13px 0 2px 0; line-height:1.25; }
      .reco-title b { color:#fff; }
      .reco-sub { font-size:.98rem; opacity:.92; margin: 8px 0 18px 0; max-width: 860px; line-height:1.55; }
      .reco-stats { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap:10px; }
      .reco-stat { background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.18);
              border-radius:8px; padding:12px 14px; min-width:0; }
      .reco-stat .l { font-size:.72rem; text-transform:none; letter-spacing:0; opacity:.82; }
      .reco-stat .v { font-size:1.28rem; font-weight:800; margin-top:3px; }
      .reco-stat .d { font-size:.78rem; opacity:.85; margin-top:2px; }

      /* ---- Scenario column headers ---- */
      .sc-head { border-top: 4px solid #2563eb; border-radius: 6px 6px 0 0;
                 padding: 8px 2px 4px 2px; font-weight:700; font-size:1.0rem; }
      .sc-win { display:inline-block; background:#ecfdf5; color:#16a34a; font-weight:700;
                font-size:.66rem; padding:2px 8px; border-radius:8px; margin-left:6px;
                vertical-align:middle; border:1px solid #bbf7d0; }

      /* ---- Metric cards ---- */
      div[data-testid="stMetric"] {
          background:#fff; border:1px solid #e8edf3; border-radius:8px;
          padding:12px 16px; box-shadow:0 1px 2px rgba(16,24,40,.04); }
      div[data-testid="stMetricLabel"] { opacity:.62; font-weight:600; }

      /* ---- Tabs / sidebar polish ---- */
      .stTabs [data-baseweb="tab"] { font-weight:600; }
      section[data-testid="stSidebar"] { background:#fff; border-right:1px solid #e6edf5; }
      section[data-testid="stSidebar"] h2 { font-size:1.05rem; }
      .side-kicker { font-size:.72rem; font-weight:700; letter-spacing:0;
                     text-transform:uppercase; color:#94a3b8; margin: 2px 0 -4px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
      <h1>🏠 Huis &amp; Hypotheek</h1>
      <p>Vergelijk hypotheekscenario's op basis van jouw woning, eigen geld en looptijd.
         Je ziet direct welke keuze financieel het sterkst is, inclusief renteaftrek,
         eigenwoningforfait, NHG, box 3 en het rendement op geld dat je niet in de woning stopt.</p>
      <div>
        <span class="pill">Annuïteit &amp; lineair</span>
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
    st.markdown('<div class="side-kicker">Jouw situatie</div>', unsafe_allow_html=True)
    st.header("Basisgegevens")
    st.caption("Deze gegevens gelden voor alle scenario's en bepalen het grootste deel van de uitkomst.")

    price = st.number_input("Koopsom woning", 50_000, 2_000_000, 280_000, 5_000,
                            help="De aankoopprijs van de woning die je wilt vergelijken.")
    income = st.number_input("Bruto jaarinkomen", 0, 1_000_000, 60_000, 1_000,
                             help="Bepaalt je belastingschijf en daarmee de waarde van de hypotheekrenteaftrek.")
    horizon = st.slider("Aantal jaren tot verkoop", 1, 30, 5,
                        help="Hoe lang je de woning naar verwachting houdt. De vergelijking loopt over deze periode.")
    rate = st.number_input("Hypotheekrente %", 0.0, 15.0, 3.9, 0.05,
                           help="De jaarlijkse nominale hypotheekrente.") / 100
    appr = st.number_input("Verwachte waardestijging % per jaar", -10.0, 20.0, 3.0, 0.25,
                           help="De gemiddelde jaarlijkse waardestijging van de woning. Dit heeft veel invloed op de uitkomst.") / 100

    st.divider()
    st.markdown('<div class="side-kicker">Verfijnen</div>', unsafe_allow_html=True)
    st.caption("De standaardwaarden zijn gebaseerd op Nederlandse uitgangspunten voor 2025.")

    with st.expander("🏦 Hypotheekgegevens"):
        term = st.slider("Looptijd hypotheek in jaren", 5, 30, 30)
        fixed = st.selectbox("Rentevaste periode in jaren", [1, 5, 10, 20, 30], index=2)
        nhg = st.checkbox("NHG gebruiken", value=True)

    with st.expander("📈 Geld buiten de woning"):
        st.caption("Geld dat een scenario niet in de woning stopt, groeit hier door. Kosten en box 3 worden meegenomen.")
        vehicle = st.radio(
            "Resterend geld gaat naar:",
            options=list(CASH_VEHICLES.keys()),
            format_func=lambda k: CASH_VEHICLES[k],
            index=0,
        )
        sav_rate = st.number_input("Spaarrente % per jaar", 0.0, 15.0, 2.25, 0.1) / 100
        dep_rate = st.number_input("Depositorente % per jaar", 0.0, 15.0, 3.0, 0.1) / 100
        inv_ret = st.number_input("Beleggingsrendement % per jaar", -10.0, 25.0, 6.0, 0.5) / 100
        fee = st.number_input("Beleggingskosten % per jaar", 0.0, 5.0, 0.3, 0.05,
                              help="Jaarlijkse kosten van de portefeuille, bijvoorbeeld fonds- of ETF-kosten.") / 100

    with st.expander("🧾 Aankoop- en verkoopkosten"):
        other = st.number_input("Overige aankoopkosten (notaris, taxatie, advies)",
                                0, 50_000, 4_000, 250)
        sell = st.number_input("Verkoopkosten % (makelaar e.d.)", 0.0, 10.0, 1.5, 0.1) / 100

    with st.expander("🇳🇱 Belastinginstellingen (2025)"):
        st.caption("Pas deze bedragen aan wanneer regels of percentages veranderen.")
        st.markdown("**Box 1 — eigen woning**")
        ewf_rate = st.number_input("Eigenwoningforfait %", 0.0, 2.0, 0.35, 0.01) / 100
        max_ded = st.number_input("Maximaal aftrekpercentage rente %", 0.0, 60.0, 37.48, 0.1) / 100
        hillen = st.number_input("Percentage Wet Hillen %", 0.0, 100.0, 76.67, 0.5) / 100
        st.markdown("**Eenmalige aankoopbelasting**")
        starters = st.checkbox("Startersvrijstelling overdrachtsbelasting", value=True)
        transfer = st.number_input("Overdrachtsbelasting zonder vrijstelling %", 0.0, 10.0, 2.0, 0.1) / 100
        nhg_rate = st.number_input("NHG-premie %", 0.0, 5.0, 0.6, 0.05) / 100
        st.markdown("**Box 3 — vermogen buiten de woning**")
        b3_rate = st.number_input("Belastingtarief box 3 %", 0.0, 60.0, 36.0, 0.5) / 100
        b3_save = st.number_input("Fictief rendement spaargeld/deposito %", 0.0, 15.0, 1.44, 0.1) / 100
        b3_inv = st.number_input("Fictief rendement beleggingen %", 0.0, 15.0, 5.88, 0.1) / 100
        b3_allow = st.number_input("Heffingsvrij vermogen (alleenstaand)", 0, 200_000, 57_684, 1_000)

# Shared inputs are now collected into a snapshot below (with the scenarios) and
# the model objects are rebuilt from the *committed* snapshot, so nothing heavy
# runs until the user clicks Calculate.

# --------------------------------------------------------------------------- #
# Scenarios — only what differs between the cases you compare
# --------------------------------------------------------------------------- #
DEFAULTS = [
    dict(name="Annuïteit", down=0, typ="annuity"),
    dict(name="Lineair", down=0, typ="linear"),
    dict(name="Meer eigen geld", down=50_000, typ="annuity"),
]

eff_horizon = min(horizon, term)

st.subheader("⚖️ Scenario's vergelijken")
top = st.columns([3, 2])
with top[0]:
    st.caption("De basisgegevens staan links. Per scenario kies je alleen het **hypotheektype** "
               "en hoeveel **eigen geld** je inbrengt. Scenario B gebruikt automatisch hetzelfde eigen geld als scenario A.")
with top[1]:
    n_scenarios = st.radio("Aantal scenario's", [1, 2, 3], index=2, horizontal=True,
                           label_visibility="collapsed")

if horizon > term:
    st.warning(f"De periode tot verkoop ({horizon} jaar) is beperkt tot de looptijd van de hypotheek ({term} jaar).")

live_scenarios: list[tuple[str, str, float]] = []
with st.container(border=True):
    tabs = st.tabs([f"Scenario {chr(65 + i)} — {DEFAULTS[i]['name']}" for i in range(n_scenarios)])
    ab_down = 0.0
    for i in range(n_scenarios):
        d = DEFAULTS[i]
        with tabs[i]:
            c1, c2, c3 = st.columns(3)
            with c1:
                name = st.text_input("Naam scenario", d["name"], key=f"name{i}")
            with c2:
                mtype = st.selectbox("Hypotheekvorm", list(TYPES), index=list(TYPES).index(d["typ"]),
                                     format_func=lambda k: TYPES[k], key=f"type{i}")
            with c3:
                if i == 1:  # Scenario B follows Scenario A's cash automatically.
                    down = ab_down
                    st.number_input("Eigen geld inbrengen", value=int(ab_down), disabled=True)
                    st.caption("Gelijk aan scenario A.")
                else:
                    down = st.number_input(
                        "Eigen geld inbrengen", 0, int(price), d["down"], 5_000,
                        key=f"down{i}", help="Je eigen geld. De hypotheek is de koopsom minus dit bedrag.")
                    if i == 0:
                        ab_down = float(down)

            loan = max(0.0, price - down)
            st.caption(
                f"→ **{name}**: eigen geld **{euro(down)}**  ·  hypotheek **{euro(loan)}**  ·  "
                f"vorm **{TYPES[mtype]}**  ·  verkoop na **{eff_horizon} jaar**  ·  "
                f"geld buiten de woning: **{CASH_VEHICLES[vehicle]}**"
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

calc_clicked = calc_slot.button("📊 Bereken", type="primary", use_container_width=True,
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

# --------------------------------------------------------------------------- #
# THE RECOMMENDATION — primary output
# --------------------------------------------------------------------------- #
def reason_line(ws, wr, rs, rr) -> str:
    """One plain-language sentence on why the winner wins, vs the runner-up."""
    net_rate = alt.net_rate()
    dp_diff = ws.down_payment - rs.down_payment
    if abs(dp_diff) > 1_000:
        if dp_diff > 0:
            return (f"Meer eigen geld in de woning ({euro(ws.down_payment)} tegenover "
                    f"{euro(rs.down_payment)}) pakt hier het beste uit: je hypotheekrente van {pct(ws.interest_rate)} "
                    f"is hoger dan het rendement van {pct(net_rate)} op geld buiten de woning, na kosten en box 3.")
        return (f"Minder eigen geld in de woning en meer geld buiten de woning werkt hier beter: dat geld levert "
                f"{pct(net_rate)} op na kosten en box 3, meer dan je hypotheekrente van {pct(ws.interest_rate)}.")
    if ws.mortgage_type != rs.mortgage_type:
        if ws.mortgage_type == "linear":
            return ("De lineaire hypotheek wint: je lost sneller af en betaalt minder rente. "
                    "De vergelijking houdt rekening met de hogere maandlasten in het begin.")
        return ("De annuïteitenhypotheek wint: de lagere maandlasten in het begin laten meer ruimte over "
                "voor geld buiten de woning, wat hier gunstig uitpakt.")
    return f"Dit scenario houdt na belasting, rente en kosten het meeste over na {ws.horizon_years} jaar."


def build_reco_html() -> str:
    ws, wr = ranked[0]
    profit_sign = "winst" if wr.net_result >= 0 else "verlies"

    if len(ranked) == 1:
        verdict = ("een positief resultaat" if wr.net_result > 0 else
                   "een negatief resultaat. Kijk nog eens naar koopsom, looptijd of rente")
        title = f"Na {ws.horizon_years} jaar geeft <b>{ws.name}</b> {verdict}"
        sub = (f"Bij verkoop houd je naar schatting <b>{euro(wr.net_worth_end)}</b> over: een netto {profit_sign} van "
               f"{euro(wr.net_result)}, oftewel {pct(wr.annual_return)} per jaar.")
        stats = [
            ("Over bij verkoop", euro(wr.net_worth_end), "overwaarde + pot buiten woning"),
            ("Netto resultaat", euro(wr.net_result), "na je totale inleg"),
            ("Rendement per jaar", pct(wr.annual_return), "kasstroomgewogen"),
            ("Maandlast start", euro(wr.monthly_payment_start), "eerste maand"),
        ]
    else:
        rs, rr = ranked[1]
        margin = wr.net_result - rr.net_result
        if margin < 2_500:
            title = f"Het ligt dicht bij elkaar: <b>{ws.name}</b> komt net bovenaan"
            sub = (f"<b>{ws.name}</b> is over {ws.horizon_years} jaar maar {euro(margin)} beter dan "
                   f"<b>{rr.name}</b>. Betalingszekerheid en flexibiliteit mogen hier dus zwaar meewegen. "
                   + reason_line(ws, wr, rs, rr))
        else:
            title = f"Op basis van je invoer is <b>{ws.name}</b> financieel het sterkst"
            sub = reason_line(ws, wr, rs, rr)
        stats = [
            ("Over bij verkoop", euro(wr.net_worth_end), "overwaarde + pot buiten woning"),
            ("Netto resultaat", euro(wr.net_result), f"{pct(wr.annual_return)} per jaar"),
            (f"Voorsprong op {rr.name}", f"+{euro(margin)}", "extra netto resultaat"),
            ("Maandlast start", euro(wr.monthly_payment_start), "eerste maand"),
        ]

    chips = "".join(
        f'<div class="reco-stat"><div class="l">{l}</div>'
        f'<div class="v">{v}</div><div class="d">{d}</div></div>'
        for l, v, d in stats
    )
    return (
        '<div class="reco">'
        '<span class="reco-badge">Advies op basis van je invoer</span>'
        f'<div class="reco-title">{title}</div>'
        f'<div class="reco-sub">{sub}</div>'
        f'<div class="reco-stats">{chips}</div>'
        '</div>'
    )


st.markdown(build_reco_html(), unsafe_allow_html=True)

if alt.invests:
    st.caption(
        f"Eerlijke vergelijking: elk scenario krijgt dezelfde **{euro(ref_cash)}** eigen middelen en "
        f"hetzelfde maandbudget van **{euro(budget)}**. Geld dat niet in de woning gaat, gaat naar "
        f"**{CASH_VEHICLES[vehicle].lower()}** na kosten en box 3. Rangschikking op netto resultaat."
    )
else:
    st.caption("Geld buiten de woning wordt niet meegenomen. Elk scenario wordt beoordeeld op de eigen inbreng "
               "en maandlast. Rangschikking op netto resultaat.")

# --------------------------------------------------------------------------- #
# KPI cards — winner highlighted
# --------------------------------------------------------------------------- #
st.markdown("#### In een oogopslag")
cols = st.columns(len(results))
for i, (col, s, r) in enumerate(zip(cols, scenarios, results)):
    accent = ACCENTS[i % len(ACCENTS)]
    is_win = r.name == winner_name
    badge = '<span class="sc-win">Beste keuze</span>' if is_win else ""
    with col:
        st.markdown(f'<div class="sc-head" style="border-color:{accent}">{r.name}{badge}</div>',
                    unsafe_allow_html=True)
        st.metric("Vermogen bij verkoop", euro(r.net_worth_end),
                  help="Wat je overhoudt: overwaarde na verkoopkosten en resterende hypotheek, plus het geld buiten de woning.")
        st.metric("Netto resultaat", euro(r.net_result), delta=f"{pct(r.annual_return)} / jaar",
                  help="Vermogen bij verkoop min alles wat je zelf hebt ingelegd.")
        if alt.invests:
            st.metric("Pot buiten woning", euro(r.side_pot_end),
                      help=f"Eigen geld dat niet is ingelegd ({euro(r.invested_cash_start)}) plus vrij maandbudget "
                           f"({euro(r.spare_invested_total)}), gegroeid na kosten en box 3.")
        st.metric("Maandlast start", euro(r.monthly_payment_start))

# --------------------------------------------------------------------------- #
# Detailed analysis — everything in tabs
# --------------------------------------------------------------------------- #
st.markdown("#### Verdieping")
t_optim, t_time, t_wealth, t_profit, t_bank, t_table, t_year = st.tabs([
    "💡 Eigen geld", "📈 Vermogen door de tijd", "🧱 Opbouw vermogen",
    "💸 Waar resultaat vandaan komt", "🏦 Naar de bank", "📋 Vergelijking", "🔎 Jaaroverzicht",
])

# --- Optimal down payment sweep --- #
with t_optim:
    st.caption(
        "**Hoeveel eigen geld kun je het best in de woning stoppen?** De grafiek houdt je totale "
        "geld en maandbudget gelijk. Wat je niet inlegt, gaat naar sparen, deposito of beleggen. "
        "Elke lijn toont een combinatie van hypotheekvorm en bestemming voor geld buiten de woning. "
        "De gemarkeerde punten zijn jouw ingestelde scenario's."
    )
    oc1, oc2 = st.columns([2, 3])
    with oc1:
        cash_avail = st.number_input(
            "Totaal beschikbaar eigen geld", 0, int(price),
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
                hovertemplate=f"{label}<br>Eigen geld €%{{x:,.0f}}<br>{metric}: %{{customdata}}<extra></extra>",
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
        hovertemplate=f"Beste keuze<br>Eigen geld €%{{x:,.0f}}<extra></extra>"))

    # Overlay the configured scenarios as labelled dots, using your configured vehicle.
    for dot_name, dot_down, dot_y in dots:
        fig5.add_trace(go.Scatter(
            x=[dot_down], y=[dot_y], mode="markers+text",
            marker=dict(size=10, color="#0f172a", line=dict(width=2, color="#fff")),
            text=[f" {dot_name}"], textposition="bottom center",
            textfont=dict(size=11, color=MUTED), showlegend=False,
            hovertemplate=f"{dot_name}<br>Eigen geld €%{{x:,.0f}}<extra></extra>"))

    fig5.update_layout(xaxis_title="Eigen geld in de woning bij aankoop",
                       legend=dict(orientation="h", yanchor="bottom", y=-0.30, x=0))
    style_fig(fig5, 460, metric, money_y=not is_pct)
    fig5.update_xaxes(tickprefix="€", tickformat="~s")
    fig5.update_layout(showlegend=True)
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
                return "is het hoogst bij €0 eigen inbreng. Geld buiten de woning werkt hier beter"
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
                   "Als rendement na belasting hoger is dan de netto hypotheekkosten, loont geld buiten de woning; "
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
    st.caption("Overwaarde na verkoopkosten en resterende hypotheek, plus het geld buiten de woning per jaar.")
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
               "plus geld buiten de woning.")
    names = [r.name for r in results]
    fig3 = go.Figure()
    fig3.add_trace(go.Bar(name="Netto verkoopopbrengst", x=names,
                          y=[r.home_value_end - r.selling_costs for r in results], marker_color=GREEN))
    fig3.add_trace(go.Bar(name="- Resterende hypotheek", x=names,
                          y=[-r.remaining_balance for r in results], marker_color=RED))
    fig3.add_trace(go.Bar(name="Pot buiten woning", x=names,
                          y=[r.side_pot_end for r in results], marker_color=ACCENTS[0]))
    fig3.update_layout(barmode="relative")
    st.plotly_chart(style_fig(fig3, 380), width="stretch")

# --- Where profit comes from --- #
with t_profit:
    st.caption("Aflossen is geen winst: je zet geld om in overwaarde. Het netto resultaat komt vooral uit "
               "waardestijging, rente, aankoop- en verkoopkosten, belastingvoordeel en rendement op geld buiten de woning.")
    pcols = st.columns(len(results))
    for col, s, r in zip(pcols, scenarios, results):
        appreciation = r.home_value_end - s.house_price
        invest_gain = r.side_pot_end - r.invested_cash_start - r.spare_invested_total
        labels = ["Waarde-<br>stijging", "Betaalde<br>rente", "Aankoop-<br>kosten",
                  "Verkoop-<br>kosten", "Belasting-<br>voordeel", "Rendement<br>buiten woning", "Netto<br>resultaat"]
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
            "Startbedrag buiten woning": euro(r.invested_cash_start),
            "Maandruimte buiten woning": euro(r.spare_invested_total),
            "Woningwaarde bij verkoop": euro(r.home_value_end),
            "Resterende hypotheek": euro(r.remaining_balance),
            "Verkoopkosten": euro(r.selling_costs),
            "Pot buiten woning bij verkoop": euro(r.side_pot_end),
            "Totale rente": euro(r.total_interest),
            "Totale aflossing": euro(r.total_principal_repaid),
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
                "invested_pot": "Pot buiten woning",
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
