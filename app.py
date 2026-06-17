"""Huis & Hypotheek — Dutch first-home buy-vs-invest comparison dashboard.

Run with:  streamlit run app.py
"""

from __future__ import annotations

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
# Page setup & styling
# --------------------------------------------------------------------------- #
st.set_page_config(
    page_title="Huis & Hypotheek — Investment Comparison",
    page_icon="🏠",
    layout="wide",
)

ACCENTS = ["#2563eb", "#16a34a", "#db2777"]  # blue, green, pink

st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem; max-width: 1400px;}
      h1, h2, h3 {letter-spacing: -0.01em;}
      div[data-testid="stMetric"] {
          background: linear-gradient(180deg,#ffffff,#f7f9fc);
          border: 1px solid #e8edf3; border-radius: 16px; padding: 14px 16px;
          box-shadow: 0 1px 2px rgba(16,24,40,.04);
      }
      div[data-testid="stMetricLabel"] {opacity:.65; font-weight:600;}
      .hero {background: linear-gradient(120deg,#1e3a8a,#2563eb 55%,#7c3aed);
             color:#fff; padding: 26px 30px; border-radius: 20px; margin-bottom: 18px;}
      .hero h1 {color:#fff; margin:0 0 6px 0; font-size: 1.9rem;}
      .hero p {margin:0; opacity:.9; font-size:1.02rem;}
      .pill {display:inline-block; background:rgba(255,255,255,.16); color:#fff;
             padding:3px 12px; border-radius:999px; font-size:.78rem; margin-right:6px;}
      .stTabs [data-baseweb="tab"] {font-weight:600;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="hero">
      <h1>🏠 Huis &amp; Hypotheek — Investment Comparison</h1>
      <p>Compare up to three home-buying scenarios for a Dutch first-time, solo buyer —
      with mortgage-interest deduction, eigenwoningforfait, NHG, and the cash you
      don't put into the house invested instead (after fees and box 3).</p>
      <div style="margin-top:12px">
        <span class="pill">Annuïteit &amp; Lineair</span>
        <span class="pill">Hypotheekrenteaftrek</span>
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


# --------------------------------------------------------------------------- #
# Sidebar — shared assumptions
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("⚙️ Comparison settings")
    n_scenarios = st.radio("Number of scenarios", [1, 2, 3], index=2, horizontal=True)

    vehicle = st.radio(
        "Cash not put into the house (down payment + monthly savings) goes into:",
        options=list(CASH_VEHICLES.keys()),
        format_func=lambda k: CASH_VEHICLES[k],
        index=2,
        help=("Every scenario is put on the same upfront cash (the largest down payment) and "
              "the same monthly budget (the highest first-month payment). Whatever a scenario "
              "doesn't put into the house — the down-payment difference as a lump, plus the "
              "monthly mortgage saving — is invested here, growing net of fees and taxed yearly "
              "in box 3, then added to net worth. Choose 'Nowhere' to ignore all spare cash."),
    )

    st.divider()
    st.subheader("🇳🇱 Tax assumptions (2025)")
    st.caption("Editable — update as the rules change each year.")
    with st.expander("Box 1 — owner-occupied home"):
        ewf_rate = st.number_input("Eigenwoningforfait rate %", 0.0, 2.0, 0.35, 0.01) / 100
        max_ded = st.number_input("Max interest-deduction rate %", 0.0, 60.0, 37.48, 0.1) / 100
        hillen = st.number_input("Wet Hillen factor %", 0.0, 100.0, 76.67, 0.5) / 100
    with st.expander("One-off purchase taxes"):
        starters = st.checkbox("Starters' exemption (0% transfer tax)", value=True)
        transfer = st.number_input("Transfer tax (if no exemption) %", 0.0, 10.0, 2.0, 0.1) / 100
        nhg_rate = st.number_input("NHG premium %", 0.0, 5.0, 0.6, 0.05) / 100
    with st.expander("Box 3 — wealth tax on invested cash"):
        b3_rate = st.number_input("Box 3 tax rate %", 0.0, 60.0, 36.0, 0.5) / 100
        b3_save = st.number_input("Deemed return — savings/deposit %", 0.0, 15.0, 1.44, 0.1) / 100
        b3_inv = st.number_input("Deemed return — investments %", 0.0, 15.0, 5.88, 0.1) / 100
        b3_allow = st.number_input("Tax-free allowance (single) €", 0, 200_000, 57_684, 1_000)

tax = TaxAssumptions(
    ewf_rate=ewf_rate,
    hillen_pct=hillen,
    max_deduction_rate=max_ded,
    starters_exemption=starters,
    transfer_tax_rate=transfer,
    nhg_premium_rate=nhg_rate,
    box3_tax_rate=b3_rate,
    box3_return_savings=b3_save,
    box3_return_investments=b3_inv,
    box3_allowance=float(b3_allow),
)

TYPES = {"annuity": "Annuïteit (annuity)", "linear": "Lineair (linear)"}

# --------------------------------------------------------------------------- #
# Shared inputs — the same for every scenario, so you enter them only once.
# --------------------------------------------------------------------------- #
st.subheader("🏡 Your situation")
st.caption("These apply to **every** scenario — fill them in once.")

with st.container(border=True):
    g1, g2, g3 = st.columns(3)
    with g1:
        st.markdown("**Property & buyer**")
        price = st.number_input("House price €", 50_000, 2_000_000, 280_000, 5_000)
        horizon = st.slider(
            "Holding period before selling (years)", 1, 30, 10,
            help="How long you keep the house before selling. The comparison runs over this period.",
        )
        income = st.number_input("Gross annual income €", 0, 1_000_000, 60_000, 1_000)
        nhg = st.checkbox("NHG (mortgage guarantee)", value=True)
    with g2:
        st.markdown("**Mortgage**")
        rate = st.number_input("Mortgage interest (hypotheekrente) %", 0.0, 15.0, 3.9, 0.05) / 100
        term = st.slider("Mortgage duration (years)", 5, 30, 30)
        fixed = st.selectbox("Fixed-interest period (years)", [1, 5, 10, 20, 30], index=2)
    with g3:
        st.markdown("**Market assumptions**")
        appr = st.number_input(
            "Expected house value growth %/yr", -10.0, 20.0, 3.0, 0.25,
            help=("Average yearly rise in the home's value, compounded. Sets the resale price — "
                  "the single biggest driver of whether buying pays off."),
        ) / 100
        st.caption("Return on spare monthly cash (vehicle chosen in the sidebar):")
        sav_rate = st.number_input("Savings account rente %/yr", 0.0, 15.0, 1.5, 0.1) / 100
        dep_rate = st.number_input("Deposit rente %/yr", 0.0, 15.0, 2.5, 0.1) / 100
        inv_ret = st.number_input("Investment return %/yr", -10.0, 25.0, 6.0, 0.5) / 100
        fee = st.number_input("Investment fee %/yr", 0.0, 5.0, 0.3, 0.05,
                              help="Annual cost of an investment portfolio (e.g. fund/ETF fees). "
                                   "Applied to the investment portfolio only.") / 100

    c5, c6 = st.columns(2)
    with c5:
        other = st.number_input("Other purchase costs € (notary, valuation, advice)", 0, 50_000, 4_000, 250)
    with c6:
        sell = st.number_input("Selling costs % (makelaar etc.)", 0.0, 10.0, 1.5, 0.1) / 100

alt = CashAlternative(
    vehicle=vehicle, savings_rate=sav_rate, deposit_rate=dep_rate,
    investment_return=inv_ret, annual_fee=fee,
)

# --------------------------------------------------------------------------- #
# Scenarios — only what differs between the cases you compare.
# --------------------------------------------------------------------------- #
DEFAULTS = [
    dict(name="Annuity mortgage", down=0, typ="annuity"),
    dict(name="Linear mortgage", down=0, typ="linear"),
    dict(name="Bigger down payment", down=50_000, typ="annuity"),
]

eff_horizon = min(horizon, term)
if horizon > term:
    st.warning(f"Holding period ({horizon}y) capped to the mortgage duration ({term}y).")

st.subheader("📊 Scenarios")
st.caption(
    "Everything above is shared. Per scenario you set the **mortgage type** and **cash brought in**. "
    "Scenario B always uses the same cash as Scenario A."
)

scenarios: list[ScenarioInput] = []
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
                    # No key: a keyed value would stick in session state and ignore A's changes.
                    st.number_input("Cash brought in (eigen inbreng) €", value=int(ab_down),
                                    disabled=True)
                    st.caption("Automatically equal to Scenario A.")
                else:
                    down = st.number_input(
                        "Cash brought in (eigen inbreng) €", 0, int(price), d["down"], 5_000,
                        key=f"down{i}", help="Your own money. The mortgage is house price minus this.")
                    if i == 0:
                        ab_down = float(down)

            loan = max(0.0, price - down)
            st.caption(f"→ Loan: **{euro(loan)}**  ·  mortgage: **{TYPES[mtype]}**")

            scenarios.append(ScenarioInput(
                name=name, house_price=float(price), down_payment=float(down),
                interest_rate=rate, mortgage_type=mtype, mortgage_term_years=term,
                fixed_period_years=fixed, horizon_years=eff_horizon, appreciation_rate=appr,
                gross_income=float(income),
                other_purchase_costs=float(other), selling_cost_rate=sell, nhg=nhg,
            ))

# --------------------------------------------------------------------------- #
# Run the model — down-payment lump + spare monthly cash go into the vehicle.
# --------------------------------------------------------------------------- #
budget = reference_budget(scenarios)
ref_cash = reference_cash(scenarios)
results = [
    run_scenario(s, tax, alt=alt, invested_cash=ref_cash - s.down_payment, budget_monthly=budget)
    for s in scenarios
]

if alt.invests:
    st.caption(
        f"Shared budget for a fair comparison: **{euro(ref_cash)}** upfront cash + **{euro(budget)}/mo**. "
        f"Each scenario invests whatever it doesn't put into the house (down payment **plus** the monthly "
        f"mortgage saving) in a **{CASH_VEHICLES[vehicle].lower()}** — after fees & box 3."
    )
else:
    st.caption("Spare cash is ignored — each scenario is evaluated at its own down payment and monthly payment.")

# --------------------------------------------------------------------------- #
# KPI cards
# --------------------------------------------------------------------------- #
st.subheader("📊 Results")
cols = st.columns(len(results))
for col, s, r in zip(cols, scenarios, results):
    with col:
        st.markdown(f"#### {r.name}")
        st.metric(
            "Net worth at sale", euro(r.net_worth_end),
            help=("What you walk away with = home equity (home value − selling costs − "
                  "remaining mortgage) plus the invested-cash pot (after fees & box 3)."),
        )
        st.metric(
            "Net result (profit)", euro(r.net_result), delta=f"{pct(r.annual_return)} / yr",
            help="Net worth at sale minus everything you put in (down payment, purchase costs, mortgage payments and invested spare cash, net of tax relief).",
        )
        st.metric("Invested-cash pot", euro(r.side_pot_end),
                  help=f"Down-payment lump ({euro(r.invested_cash_start)}) + spare monthly cash "
                       f"({euro(r.spare_invested_total)}), grown in a "
                       f"{CASH_VEHICLES[vehicle].lower()} after fees & box 3.")
        st.metric("Monthly mortgage (start)", euro(r.monthly_payment_start))
        st.metric("Total interest paid", euro(r.total_interest))

# --------------------------------------------------------------------------- #
# Comparison table
# --------------------------------------------------------------------------- #
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
    for s, r in zip(scenarios, results)
})
st.dataframe(table, width="stretch")

# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
st.markdown("##### Net worth over time")
fig = go.Figure()
for i, (s, r) in enumerate(zip(scenarios, results)):
    years = [row["year"] for row in r.yearly]
    nw = [
        row["home_value"] * (1 - s.selling_cost_rate) - row["remaining_balance"] + row["invested_pot"]
        for row in r.yearly
    ]
    fig.add_trace(go.Scatter(
        x=years, y=nw, mode="lines+markers", name=r.name,
        line=dict(width=3, color=ACCENTS[i % len(ACCENTS)]),
    ))
fig.update_layout(
    height=380, margin=dict(l=10, r=10, t=10, b=10),
    xaxis_title="Year", yaxis_title="Net worth (€)",
    legend=dict(orientation="h", y=-0.2), plot_bgcolor="#fff",
)
st.plotly_chart(fig, width="stretch")

# --------------------------------------------------------------------------- #
# Wealth breakdown (waterfall-style stacked bar) at sale
# --------------------------------------------------------------------------- #
st.markdown("##### How the net worth at sale is built up")
fig3 = go.Figure()
names = [r.name for r in results]
fig3.add_trace(go.Bar(name="Net sale proceeds", x=names,
                      y=[r.home_value_end - r.selling_costs for r in results], marker_color="#16a34a"))
fig3.add_trace(go.Bar(name="− Remaining mortgage", x=names,
                      y=[-r.remaining_balance for r in results], marker_color="#ef4444"))
fig3.add_trace(go.Bar(name="Invested-cash pot", x=names,
                      y=[r.side_pot_end for r in results], marker_color="#2563eb"))
fig3.update_layout(barmode="relative", height=340, margin=dict(l=10, r=10, t=10, b=10),
                   yaxis_title="€", legend=dict(orientation="h", y=-0.25), plot_bgcolor="#fff")
st.plotly_chart(fig3, width="stretch")

# --------------------------------------------------------------------------- #
# Profit bridge — where the net result actually comes from
# --------------------------------------------------------------------------- #
st.markdown("##### Where the profit comes from")
st.caption(
    "Repaying principal is **not** profit — it just turns your cash into home equity. "
    "Your real gain is the house's appreciation, minus the cost of owning (interest, "
    "buying & selling costs), plus tax relief, plus the gain on the invested cash "
    "(net of fees & box 3). The bars below add up to the net result."
)
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
            increasing={"marker": {"color": "#16a34a"}},
            decreasing={"marker": {"color": "#ef4444"}},
            totals={"marker": {"color": "#2563eb"}},
        ))
        wf.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10),
                         yaxis_title="€", plot_bgcolor="#fff", showlegend=False)
        wf.update_xaxes(tickfont=dict(size=10))
        st.plotly_chart(wf, width="stretch")

# --------------------------------------------------------------------------- #
# Paid to the bank per year (interest vs principal repayment)
# --------------------------------------------------------------------------- #
st.markdown("##### Paid to the bank each year — interest vs repayment")
st.caption(
    "Each bar is one year's payments to the bank. The solid part is **principal** "
    "(repaying the loan), the hatched part is **interest**. Annuity keeps the total "
    "level (interest shrinks, repayment grows); linear keeps repayment flat so the total falls."
)
fig4 = go.Figure()
for i, r in enumerate(results):
    years = [row["year"] for row in r.yearly]
    principal = [row["principal_paid"] for row in r.yearly]
    interest = [row["interest_paid"] for row in r.yearly]
    base = ACCENTS[i % len(ACCENTS)]
    fig4.add_trace(go.Bar(
        x=years, y=principal, name=f"{r.name} · repayment", offsetgroup=str(i),
        legendgroup=r.name, marker_color=base,
        hovertemplate="Year %{x}<br>Repayment %{y:,.0f}<extra></extra>",
    ))
    fig4.add_trace(go.Bar(
        x=years, y=interest, name=f"{r.name} · interest", offsetgroup=str(i),
        legendgroup=r.name, marker_color=base, opacity=0.5,
        marker_pattern_shape="/",
        hovertemplate="Year %{x}<br>Interest %{y:,.0f}<extra></extra>",
    ))
fig4.update_layout(
    barmode="stack", height=380, margin=dict(l=10, r=10, t=10, b=10),
    xaxis_title="Year", yaxis_title="Paid to the bank (€)",
    legend=dict(orientation="h", y=-0.25), plot_bgcolor="#fff",
)
st.plotly_chart(fig4, width="stretch")

# --------------------------------------------------------------------------- #
# Per-scenario yearly detail
# --------------------------------------------------------------------------- #
st.subheader("🔎 Yearly detail")
for s, r in zip(scenarios, results):
    with st.expander(f"{r.name} — purchase costs & yearly breakdown"):
        pc = r.purchase_costs
        st.write(
            f"**Purchase costs (kosten koper):** transfer tax {euro(pc['transfer_tax'])} · "
            f"NHG premium {euro(pc['nhg_premium'])} · other {euro(pc['other'])} · "
            f"financing deduction −{euro(pc['financing_deduction'])} → **net {euro(pc['net'])}**"
        )
        df = pd.DataFrame(r.yearly)
        df = df.rename(columns={
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

st.caption(
    "ℹ️ This tool is a simplified, transparent model for comparison purposes — "
    "not tax or financial advice. Tax rules (renteaftrek, eigenwoningforfait, "
    "box 3, NHG, startersvrijstelling) change yearly; verify figures in the sidebar."
)
