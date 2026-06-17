"""Dutch tax helpers: income tax brackets, owner-occupied home (box 1)
and wealth tax (box 3) on the invested cash alternative.

These are deliberately simple, transparent approximations of the real rules so
the comparison is fair and explainable, not an official tax return. Every rate
lives in :class:`mortgage.models.TaxAssumptions` and is editable in the UI.
"""

from __future__ import annotations

from .models import TaxAssumptions

# Box 1 income-tax brackets (2025, below state-pension age). Tuples are
# (upper_bound, rate); the last bound is infinity.
_BRACKETS_2025: list[tuple[float, float]] = [
    (38_441.0, 0.3582),
    (76_817.0, 0.3748),
    (float("inf"), 0.4950),
]


def marginal_rate(gross_income: float, tax: TaxAssumptions) -> float:
    """Marginal box 1 rate the buyer pays on the top euro of income.

    Used for the eigenwoningforfait (which is *added* to income). An explicit
    override on the assumptions wins when set.
    """
    if tax.marginal_rate_override is not None:
        return tax.marginal_rate_override
    for upper, rate in _BRACKETS_2025:
        if gross_income <= upper:
            return rate
    return _BRACKETS_2025[-1][1]


def deduction_rate(gross_income: float, tax: TaxAssumptions) -> float:
    """Rate at which mortgage interest is actually deducted.

    It is the buyer's marginal rate, capped at the statutory aftrektarief.
    """
    return min(marginal_rate(gross_income, tax), tax.max_deduction_rate)


def owner_home_tax_benefit(
    *,
    year_interest: float,
    home_value: float,
    gross_income: float,
    tax: TaxAssumptions,
) -> dict[str, float]:
    """Net box 1 cash benefit of owning the home for one year.

    Combines mortgage interest deduction (hypotheekrenteaftrek), the
    eigenwoningforfait that is added to taxable income, and the Wet Hillen
    deduction when the forfait exceeds the deductible interest.

    Returns a breakdown; ``net_benefit`` is positive when owning saves tax.
    """
    ewf = home_value * tax.ewf_rate
    ded_rate = deduction_rate(gross_income, tax)
    marg = marginal_rate(gross_income, tax)

    interest_relief = year_interest * ded_rate
    ewf_cost = ewf * marg

    # Wet Hillen: when the forfait is larger than the deductible interest, the
    # excess is (partly) deductible again, softening the EWF cost.
    hillen_extra = max(0.0, ewf - year_interest) * tax.hillen_pct
    hillen_relief = hillen_extra * marg

    net = interest_relief - ewf_cost + hillen_relief
    return {
        "ewf": ewf,
        "interest_relief": interest_relief,
        "ewf_cost": ewf_cost,
        "hillen_relief": hillen_relief,
        "net_benefit": net,
    }


def box3_tax(balance_start_of_year: float, *, is_investment: bool, tax: TaxAssumptions) -> float:
    """Yearly box 3 wealth tax on the invested cash pot.

    Assessed on the balance at the start of the year (Dutch peildatum is 1 Jan)
    above the tax-free allowance, using the deemed return for the asset class.
    """
    taxable = max(0.0, balance_start_of_year - tax.box3_allowance)
    if taxable <= 0:
        return 0.0
    deemed_return = tax.box3_return_investments if is_investment else tax.box3_return_savings
    return taxable * deemed_return * tax.box3_tax_rate


def purchase_costs(scenario, tax: TaxAssumptions) -> dict[str, float]:
    """One-off costs to buy the home (kosten koper), with deductible share.

    Transfer tax is waived under the starters' exemption; NHG premium applies
    when NHG is used. Financing costs are (partly) deductible in box 1 in the
    purchase year, which we credit back at the deduction rate.
    """
    loan = scenario.loan_amount
    transfer_tax = 0.0 if tax.starters_exemption else scenario.house_price * tax.transfer_tax_rate
    nhg_premium = loan * tax.nhg_premium_rate if scenario.nhg else 0.0

    # NHG premium + advice/mortgage-deed share of other costs are deductible.
    deductible_base = nhg_premium + scenario.other_purchase_costs * tax.financing_cost_deductible_share
    ded_rate = deduction_rate(scenario.gross_income, tax)
    financing_deduction = deductible_base * ded_rate

    gross = transfer_tax + nhg_premium + scenario.other_purchase_costs
    return {
        "transfer_tax": transfer_tax,
        "nhg_premium": nhg_premium,
        "other": scenario.other_purchase_costs,
        "gross": gross,
        "financing_deduction": financing_deduction,
        "net": gross - financing_deduction,
    }
