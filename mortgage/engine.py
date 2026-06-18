"""Scenario simulation engine.

Runs a month-by-month simulation of buying, holding and selling a home.

For a fair comparison, every scenario can be put on the same upfront cash and
the same monthly budget:

* the cash a scenario does not use as down payment (vs the largest down payment)
  is invested as a lump sum, and
* the monthly mortgage saving (vs the highest first-month payment) is invested
  each month on top.

Both go into the same pot — a savings account, deposit or investment portfolio
— growing net of fees and taxed yearly in box 3, then added to net worth.
Choosing the ``"nowhere"`` vehicle disables this: each scenario is then simply
evaluated at its own down payment and monthly payment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from .models import ScenarioInput, TaxAssumptions, CashAlternative
from . import tax as taxmod


def irr(cashflows: Sequence[float]) -> float:
    """Internal rate of return per period for a series of cash flows.

    ``numpy_financial.irr`` finds the IRR as a root of the cash-flow polynomial
    via ``np.roots`` — an O(n^3) eigenvalue solve in the number of periods, which
    becomes very slow for long monthly horizons (e.g. 360 months). A 1-D root
    finder on the NPV is exact and ~1000x faster.

    The flows are *not* strictly conventional — year-end tax refunds add positive
    amounts mid-stream, so there are many sign changes. But the NPV is dominated by
    the large initial outflow and final inflow: it is positive at low rates and
    negative at high rates, so the economically meaningful root is bracketed by a
    safe range and found by bisection (Newton first for speed). Returns ``nan`` if
    no sign change exists (no real IRR).
    """
    cf = np.asarray(cashflows, dtype=float)
    if cf.size < 2 or not (np.any(cf > 0) and np.any(cf < 0)):
        return float("nan")
    t = np.arange(cf.size)
    # Normalise by the largest flow so the NPV stays well-scaled at any horizon.
    cfn = cf / np.max(np.abs(cf))

    def npv(r: float) -> float:
        v = float(np.sum(cfn / (1.0 + r) ** t))
        return v if np.isfinite(v) else float("nan")

    # Overflow-safe bracket: (1+r)^-t stays finite for r >= -0.8 even at t=360
    # (≈ -100% per year, far below any realistic IRR). NPV(lo) > 0 (final inflow
    # dominates), NPV(hi) < 0 (initial outflow dominates) -> guaranteed root.
    lo, hi = -0.8, 1.0
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        flo, fhi = npv(lo), npv(hi)
        if not (flo == flo and fhi == fhi) or flo * fhi > 0.0:
            return float("nan")

        # Newton from a central guess; accept only if it lands inside the bracket.
        r = 0.01
        for _ in range(40):
            f = npv(r)
            if f == f and abs(f) < 1e-12:
                if lo < r < hi:
                    return r
                break
            deriv = float(np.sum(-t * cfn / (1.0 + r) ** (t + 1)))
            if not np.isfinite(deriv) or deriv == 0.0:
                break
            r_new = r - f / deriv
            if not (lo < r_new < hi):      # leave the bracket -> hand off to bisection
                break
            if abs(r_new - r) < 1e-13:
                return r_new
            r = r_new

        # Robust bisection on the guaranteed bracket.
        for _ in range(200):
            mid = (lo + hi) / 2.0
            fm = npv(mid)
            if abs(fm) < 1e-12 or (hi - lo) < 1e-14:
                return mid
            if flo * fm < 0.0:
                hi = mid
            else:
                lo, flo = mid, fm
    return (lo + hi) / 2.0


@dataclass
class ScenarioResult:
    """Outputs for one scenario."""

    name: str
    yearly: list[dict]                  # per-year breakdown rows
    monthly_payment_start: float        # gross mortgage payment in month 1
    purchase_costs: dict[str, float]

    # End-of-horizon position
    home_value_end: float
    remaining_balance: float
    selling_costs: float
    invested_cash_start: float          # lump (down-payment difference) put in at t=0
    spare_invested_total: float         # spare monthly cash added over time
    side_pot_end: float                 # pot value at sale, after fees & box 3
    net_worth_end: float                # equity + pot walked away with

    # Cash accounting over the horizon
    total_contributed: float            # everything paid in from your pocket
    total_interest: float
    total_principal_repaid: float
    total_tax_benefit: float            # box 1 relief (renteaftrek minus EWF)
    total_box3_tax: float               # box 3 paid on the invested pot
    net_result: float                   # net_worth_end - total_contributed
    annual_return: float                # money-weighted (IRR), annualised


def base_monthly_payment(scenario: ScenarioInput) -> float:
    """Gross payment in the first month (annuity is level; linear starts high)."""
    loan = scenario.loan_amount
    r = scenario.interest_rate / 12.0
    n = scenario.mortgage_term_years * 12
    if loan <= 0:
        return 0.0
    if scenario.mortgage_type == "annuity":
        if r == 0:
            return loan / n
        return loan * r / (1 - (1 + r) ** -n)
    # linear: principal/n + interest on full balance in month 1
    return loan / n + loan * r


def reference_cash(scenarios: Iterable[ScenarioInput]) -> float:
    """Shared upfront cash = the largest down payment among scenarios."""
    return max((s.down_payment for s in scenarios), default=0.0)


def reference_budget(scenarios: Iterable[ScenarioInput]) -> float:
    """Shared monthly budget = the highest first-month payment among scenarios."""
    return max((base_monthly_payment(s) for s in scenarios), default=0.0)


def run_scenario(
    scenario: ScenarioInput,
    tax: TaxAssumptions,
    *,
    alt: CashAlternative | None = None,
    invested_cash: float = 0.0,
    budget_monthly: float = 0.0,
) -> ScenarioResult:
    """Simulate one scenario; invest the down-payment lump + spare monthly cash."""
    if alt is None:
        alt = CashAlternative(vehicle="nowhere")
    invests = alt.invests

    loan = scenario.loan_amount
    r = scenario.interest_rate / 12.0
    term_m = scenario.mortgage_term_years * 12
    horizon_m = scenario.horizon_years * 12
    monthly_growth = (1 + scenario.appreciation_rate) ** (1 / 12) - 1
    pot_growth = (1 + alt.net_rate()) ** (1 / 12) - 1

    annuity_payment = base_monthly_payment(scenario) if scenario.mortgage_type == "annuity" else 0.0
    linear_principal = loan / term_m if scenario.mortgage_type == "linear" else 0.0

    costs = taxmod.purchase_costs(scenario, tax)

    lump = max(0.0, invested_cash) if invests else 0.0
    balance = loan
    home_value = scenario.house_price
    pot = lump

    # Monthly cash flows for IRR: month 0 = initial cash out (house + lump).
    cashflows = [-(scenario.down_payment + lump + costs["net"])]

    total_interest = 0.0
    total_principal = 0.0
    total_tax_benefit = 0.0
    total_box3 = 0.0
    total_spare = 0.0

    yearly: list[dict] = []
    year_interest = 0.0
    year_principal = 0.0
    year_spare = 0.0
    home_value_year_start = home_value
    pot_year_start = pot

    for m in range(1, horizon_m + 1):
        interest = balance * r
        if scenario.mortgage_type == "annuity":
            sched_principal = min(annuity_payment - interest, balance)
        else:
            sched_principal = min(linear_principal, balance)
        sched_principal = max(0.0, sched_principal)
        gross_payment = interest + sched_principal

        spare = max(0.0, budget_monthly - gross_payment) if invests else 0.0
        pot += spare
        pot *= 1 + pot_growth

        balance -= sched_principal
        home_value *= 1 + monthly_growth

        total_interest += interest
        total_principal += sched_principal
        total_spare += spare
        year_interest += interest
        year_principal += sched_principal
        year_spare += spare

        # Cash out of pocket each month: the full budget if investing the spare,
        # otherwise just the mortgage payment.
        month_cf = -(budget_monthly if invests else gross_payment)

        if m % 12 == 0:
            benefit = taxmod.owner_home_tax_benefit(
                year_interest=year_interest,
                home_value=home_value_year_start,
                gross_income=scenario.gross_income,
                tax=tax,
            )
            total_tax_benefit += benefit["net_benefit"]
            month_cf += benefit["net_benefit"]  # tax relief refunded at year-end

            b3 = 0.0
            if invests:
                b3 = taxmod.box3_tax(pot_year_start, is_investment=alt.is_investment, tax=tax)
                pot -= b3
                total_box3 += b3

            yearly.append(
                {
                    "year": m // 12,
                    "interest_paid": year_interest,
                    "principal_paid": year_principal,
                    "paid_to_bank": year_interest + year_principal,
                    "remaining_balance": balance,
                    "home_value": home_value,
                    "ewf": benefit["ewf"],
                    "net_tax_benefit": benefit["net_benefit"],
                    "spare_invested": year_spare,
                    "box3_tax": b3,
                    "invested_pot": pot,
                }
            )
            year_interest = 0.0
            year_principal = 0.0
            year_spare = 0.0
            home_value_year_start = home_value
            pot_year_start = pot

        cashflows.append(month_cf)

    selling_costs = home_value * scenario.selling_cost_rate
    equity = home_value - selling_costs - balance
    net_worth_end = equity + pot
    cashflows[-1] += net_worth_end  # liquidate home and pot at the end

    cash_out = budget_monthly * horizon_m if invests else (total_interest + total_principal)
    total_contributed = scenario.down_payment + lump + costs["net"] + cash_out - total_tax_benefit

    try:
        irr_m = irr(cashflows)
        annual_return = (1 + irr_m) ** 12 - 1 if irr_m == irr_m else 0.0  # nan-check
    except Exception:
        annual_return = 0.0

    return ScenarioResult(
        name=scenario.name,
        yearly=yearly,
        monthly_payment_start=base_monthly_payment(scenario),
        purchase_costs=costs,
        home_value_end=home_value,
        remaining_balance=balance,
        selling_costs=selling_costs,
        invested_cash_start=lump,
        spare_invested_total=total_spare,
        side_pot_end=pot,
        net_worth_end=net_worth_end,
        total_contributed=total_contributed,
        total_interest=total_interest,
        total_principal_repaid=total_principal,
        total_tax_benefit=total_tax_benefit,
        total_box3_tax=total_box3,
        net_result=net_worth_end - total_contributed,
        annual_return=annual_return,
    )
