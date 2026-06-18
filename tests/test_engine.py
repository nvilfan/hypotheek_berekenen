"""Sanity tests for the financial model."""

import pytest

from mortgage import (
    CashAlternative,
    ScenarioInput,
    TaxAssumptions,
    reference_budget,
    reference_cash,
    run_scenario,
)
from mortgage.engine import base_monthly_payment
from mortgage import tax as taxmod


def test_annuity_payment_matches_formula():
    s = ScenarioInput(name="a", house_price=300_000, down_payment=0,
                      interest_rate=0.04, mortgage_type="annuity",
                      mortgage_term_years=30)
    # Standard annuity payment for 300k @ 4% over 30y ~= 1432.25
    assert base_monthly_payment(s) == pytest.approx(1432.25, abs=1.0)


def test_linear_first_payment_is_highest():
    s = ScenarioInput(name="a", house_price=300_000, down_payment=0,
                      interest_rate=0.04, mortgage_type="linear",
                      mortgage_term_years=30)
    # 300k/360 + 300k*0.04/12 = 833.33 + 1000 = 1833.33
    assert base_monthly_payment(s) == pytest.approx(1833.33, abs=1.0)


def test_marginal_rate_brackets():
    tax = TaxAssumptions()
    assert taxmod.marginal_rate(30_000, tax) == pytest.approx(0.3582)
    assert taxmod.marginal_rate(60_000, tax) == pytest.approx(0.3748)
    assert taxmod.marginal_rate(120_000, tax) == pytest.approx(0.4950)


def test_deduction_rate_is_capped():
    tax = TaxAssumptions()
    # High earner: marginal 49.5% but deduction capped at 37.48%.
    assert taxmod.deduction_rate(120_000, tax) == pytest.approx(0.3748)


def test_starters_exemption_removes_transfer_tax():
    s = ScenarioInput(name="a", house_price=400_000, down_payment=50_000)
    tax = TaxAssumptions(starters_exemption=True)
    assert taxmod.purchase_costs(s, tax)["transfer_tax"] == 0.0
    tax2 = TaxAssumptions(starters_exemption=False)
    assert taxmod.purchase_costs(s, tax2)["transfer_tax"] == pytest.approx(8_000)


def test_appreciation_drives_net_worth():
    tax = TaxAssumptions()
    s = ScenarioInput(name="a", house_price=400_000, down_payment=80_000,
                      interest_rate=0.039, horizon_years=10, appreciation_rate=0.03)
    res = run_scenario(s, tax)
    # Home should be worth ~400k * 1.03^10 ~= 537.6k
    assert res.home_value_end == pytest.approx(400_000 * 1.03 ** 10, rel=1e-3)
    assert res.net_worth_end > 0


def test_linear_repays_more_principal_than_annuity_early():
    tax = TaxAssumptions()
    common = dict(house_price=400_000, down_payment=40_000, interest_rate=0.039,
                  horizon_years=5)
    ann = run_scenario(ScenarioInput(name="a", mortgage_type="annuity", **common), tax)
    lin = run_scenario(ScenarioInput(name="l", mortgage_type="linear", **common), tax)
    # Over the first years linear pays down the loan faster than annuity.
    assert lin.total_principal_repaid > ann.total_principal_repaid
    assert lin.remaining_balance < ann.remaining_balance


def test_profit_bridge_reconciles():
    # net_result = appreciation - interest - costs - selling + tax + invested-cash gain,
    # where the invested-cash gain is on BOTH the lump and the spare monthly cash.
    tax = TaxAssumptions()
    s = ScenarioInput(name="a", house_price=241_000, down_payment=0,
                      interest_rate=0.0138, mortgage_type="annuity", horizon_years=5,
                      appreciation_rate=0.025)
    r = run_scenario(s, tax, alt=CashAlternative(vehicle="investment"),
                     invested_cash=50_000, budget_monthly=1_700)
    appreciation = r.home_value_end - s.house_price
    invest_gain = r.side_pot_end - r.invested_cash_start - r.spare_invested_total
    bridge = (appreciation - r.total_interest - r.purchase_costs["net"]
              - r.selling_costs + r.total_tax_benefit + invest_gain)
    assert bridge == pytest.approx(r.net_result, abs=1.0)


def test_net_worth_is_equity_plus_pot():
    tax = TaxAssumptions()
    s = ScenarioInput(name="a", house_price=300_000, down_payment=60_000, horizon_years=7)
    r = run_scenario(s, tax, alt=CashAlternative(vehicle="deposit"),
                     invested_cash=20_000, budget_monthly=1_800)
    equity = r.home_value_end - r.selling_costs - r.remaining_balance
    assert r.net_worth_end == pytest.approx(equity + r.side_pot_end)


def test_reference_helpers():
    a = ScenarioInput(name="a", down_payment=0, mortgage_type="annuity")
    b = ScenarioInput(name="b", down_payment=0, mortgage_type="linear")
    c = ScenarioInput(name="c", down_payment=50_000, mortgage_type="annuity")
    assert reference_cash([a, b, c]) == 50_000
    # Linear's first payment is the highest -> sets the shared budget.
    assert reference_budget([a, b, c]) == pytest.approx(base_monthly_payment(b))


def test_lump_and_spare_both_feed_the_pot():
    tax = TaxAssumptions()
    s = ScenarioInput(name="a", house_price=280_000, down_payment=0,
                      mortgage_type="annuity", horizon_years=10)
    alt = CashAlternative(vehicle="investment")
    budget = base_monthly_payment(s) + 300  # leave headroom so spare > 0
    r = run_scenario(s, tax, alt=alt, invested_cash=40_000, budget_monthly=budget)
    assert r.invested_cash_start == 40_000
    assert r.spare_invested_total > 0
    # Pot exceeds the sum of contributions thanks to growth.
    assert r.side_pot_end > r.invested_cash_start + r.spare_invested_total


def test_nowhere_disables_the_pot():
    tax = TaxAssumptions()
    s = ScenarioInput(name="a", house_price=280_000, down_payment=0, horizon_years=5)
    r = run_scenario(s, tax, alt=CashAlternative(vehicle="nowhere"),
                     invested_cash=50_000, budget_monthly=2_000)
    assert r.side_pot_end == 0.0
    assert r.invested_cash_start == 0.0
    assert r.spare_invested_total == 0.0
    assert r.total_box3_tax == 0.0
    # Net worth is then pure equity.
    assert r.net_worth_end == pytest.approx(r.home_value_end - r.selling_costs - r.remaining_balance)


def test_invested_pot_grows_net_of_fee():
    # 20k lump at 6% - 0.3% fee, no spare; stays under the box 3 allowance.
    tax = TaxAssumptions()
    s = ScenarioInput(name="a", house_price=280_000, down_payment=0, horizon_years=5)
    alt = CashAlternative(vehicle="investment", investment_return=0.06, annual_fee=0.003)
    r = run_scenario(s, tax, alt=alt, invested_cash=20_000, budget_monthly=0.0)
    assert r.total_box3_tax == 0.0  # 20k -> ~26k, under the ~57.7k allowance
    assert r.spare_invested_total == 0.0
    # Grows at the net (after-fee) rate of 5.7%, not the gross 6%.
    assert r.side_pot_end == pytest.approx(20_000 * 1.057 ** 5, rel=1e-3)
    assert r.side_pot_end < 20_000 * 1.06 ** 5


def test_box3_charged_above_allowance():
    tax = TaxAssumptions()
    s = ScenarioInput(name="a", house_price=280_000, down_payment=0, horizon_years=5)
    alt = CashAlternative(vehicle="investment")
    big = run_scenario(s, tax, alt=alt, invested_cash=150_000)
    assert big.total_box3_tax > 0.0


def test_savings_vehicle_uses_savings_deemed_return():
    tax = TaxAssumptions()
    s = ScenarioInput(name="a", house_price=280_000, down_payment=0, horizon_years=5)
    inv = run_scenario(s, tax, alt=CashAlternative(vehicle="investment"), invested_cash=150_000)
    sav = run_scenario(s, tax, alt=CashAlternative(vehicle="savings"), invested_cash=150_000)
    # Investments are taxed on a higher deemed return than savings.
    assert inv.total_box3_tax > sav.total_box3_tax


# --- Extra repayments -------------------------------------------------------

def _bridge(r, s):
    appreciation = r.home_value_end - s.house_price
    return (appreciation - r.total_interest - r.purchase_costs["net"]
            - r.selling_costs + r.total_tax_benefit + r.side_pot_gain)


def test_one_off_extra_repayment_cuts_balance_and_interest():
    tax = TaxAssumptions()
    common = dict(house_price=300_000, down_payment=0, interest_rate=0.04,
                  mortgage_type="annuity", horizon_years=10)
    base = run_scenario(ScenarioInput(name="b", **common), tax)
    extra = run_scenario(ScenarioInput(name="e", extra_repay_once=25_000, **common), tax)
    # A lump repayment at the start lowers the remaining debt and the interest paid.
    assert extra.remaining_balance < base.remaining_balance
    assert extra.total_interest < base.total_interest
    assert extra.extra_repaid == pytest.approx(25_000)
    # That €25k is out of pocket -> contributed more than the no-extra case.
    assert extra.total_contributed > base.total_contributed
    # It sits as equity, so net worth rises by at least the €25k repaid — and more,
    # because the fixed-payment annuity then amortises faster (interest saved).
    assert extra.net_worth_end - base.net_worth_end > 25_000


def test_annual_extra_repayment_accumulates():
    tax = TaxAssumptions()
    common = dict(house_price=300_000, down_payment=0, interest_rate=0.04,
                  mortgage_type="annuity", horizon_years=5)
    r = run_scenario(ScenarioInput(name="e", extra_repay_annual=10_000, **common), tax)
    # 5 yearly payments of 10k applied to the loan.
    assert r.extra_repaid == pytest.approx(50_000)
    assert r.remaining_balance < run_scenario(ScenarioInput(name="b", **common), tax).remaining_balance


def test_profit_bridge_reconciles_with_extra_repayment():
    tax = TaxAssumptions()
    s = ScenarioInput(name="a", house_price=241_000, down_payment=10_000,
                      interest_rate=0.038, mortgage_type="annuity", horizon_years=6,
                      appreciation_rate=0.025, extra_repay_annual=3_000, extra_repay_once=8_000)
    for veh in ("investment", "savings", "nowhere"):
        r = run_scenario(s, tax, alt=CashAlternative(vehicle=veh),
                         invested_cash=45_000, budget_monthly=1_500)
        assert _bridge(r, s) == pytest.approx(r.net_result, abs=1.0), veh
