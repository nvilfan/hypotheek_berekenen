"""Input dataclasses and default Dutch tax assumptions (2025 figures).

All percentages are stored as fractions (e.g. ``0.0035`` for 0.35%).
Every default is editable from the dashboard so the model can be kept current
as the rules change each tax year.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaxAssumptions:
    """Dutch tax parameters for an owner-occupied first home (solo buyer).

    Defaults reflect 2025 rules. Income tax / deduction rates are derived from
    income in :mod:`mortgage.tax`, but can be overridden here.
    """

    # --- Box 1: owner-occupied home -------------------------------------
    ewf_rate: float = 0.0035            # eigenwoningforfait (homes 75k–1.2M, 2025)
    hillen_pct: float = 0.7667          # Wet Hillen phase-out factor (2025)
    # Max rate at which mortgage interest may be deducted (aftrektarief 2025).
    max_deduction_rate: float = 0.3748
    # Override the income-derived marginal rate (None = derive from income).
    marginal_rate_override: float | None = None

    # --- One-off purchase taxes/costs -----------------------------------
    # First-time buyers (<35, price under the cap) pay 0% transfer tax.
    starters_exemption: bool = True
    transfer_tax_rate: float = 0.02     # overdrachtsbelasting for own home otherwise
    nhg_premium_rate: float = 0.006     # NHG borgtochtprovisie (2025: 0.6% of loan)
    # Share of one-off financing costs (NHG, advice, mortgage deed) deductible.
    financing_cost_deductible_share: float = 1.0

    # --- Box 3: wealth tax on the cash that is invested instead ----------
    box3_tax_rate: float = 0.36
    box3_return_savings: float = 0.0144      # deemed return on bank savings (2025)
    box3_return_investments: float = 0.0588  # deemed return on investments (2025)
    box3_allowance: float = 57_684.0         # heffingsvrij vermogen, single (2025)

    def __post_init__(self) -> None:
        for name in (
            "ewf_rate", "hillen_pct", "max_deduction_rate",
            "transfer_tax_rate", "nhg_premium_rate",
            "financing_cost_deductible_share", "box3_tax_rate",
            "box3_return_savings", "box3_return_investments",
        ):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative, got {value}")


# Where the free cash (lump + spare monthly cash vs the priciest scenario) goes.
# "nowhere" disables the mechanism entirely. Extra mortgage repayment is a
# separate input on the scenario (extra_repay_once / extra_repay_annual).
CASH_VEHICLES: dict[str, str] = {
    "savings": "Spaarrekening",
    "deposit": "Deposito",
    "investment": "Beleggingsportefeuille",
    "nowhere": "Niet meenemen",
}


@dataclass
class CashAlternative:
    """Where the spare monthly cash is parked, and how it grows.

    The pot grows at the chosen vehicle's rate minus annual fees, and is taxed
    yearly in box 3. Savings and deposits use the box 3 savings deemed return;
    an investment portfolio uses the (higher) investments deemed return.
    ``"nowhere"`` means spare cash is not taken into account at all.
    """

    vehicle: str = "investment"
    savings_rate: float = 0.015
    deposit_rate: float = 0.025
    investment_return: float = 0.06
    annual_fee: float = 0.003           # charged on an investment portfolio only

    def __post_init__(self) -> None:
        if self.vehicle not in CASH_VEHICLES:
            raise ValueError(f"vehicle must be one of {list(CASH_VEHICLES)}")

    @property
    def invests(self) -> bool:
        """True when free cash is put to work in a growing, box 3-taxed pot."""
        return self.vehicle != "nowhere"

    @property
    def is_investment(self) -> bool:
        return self.vehicle == "investment"

    def gross_rate(self) -> float:
        return {
            "savings": self.savings_rate,
            "deposit": self.deposit_rate,
            "investment": self.investment_return,
            "nowhere": 0.0,
        }[self.vehicle]

    def net_rate(self) -> float:
        """Return after fees (fees apply to the investment portfolio only)."""
        fee = self.annual_fee if self.is_investment else 0.0
        return self.gross_rate() - fee


@dataclass
class ScenarioInput:
    """One buy-a-house scenario to evaluate over a holding period."""

    name: str

    # --- Property & mortgage -------------------------------------------
    house_price: float = 400_000.0
    down_payment: float = 50_000.0       # eigen inbreng (cash brought in)
    interest_rate: float = 0.039         # hypotheekrente (annual nominal)
    mortgage_type: str = "annuity"       # "annuity" | "linear"
    mortgage_term_years: int = 30        # repayment term (max 30 for deduction)
    fixed_period_years: int = 10         # rentevaste periode (1/5/10/20/30)

    # --- Horizon & market assumptions ----------------------------------
    horizon_years: int = 10              # years before selling again
    appreciation_rate: float = 0.02      # avg house value growth per year

    # --- Buyer ----------------------------------------------------------
    gross_income: float = 60_000.0       # box 1 income (sets marginal rate)

    # --- One-off costs --------------------------------------------------
    other_purchase_costs: float = 4_000.0   # notary, valuation, advice, etc.
    selling_cost_rate: float = 0.015        # makelaar + costs on sale

    nhg: bool = True

    # --- Extra repayments (out-of-pocket, on top of the schedule) -------
    # A one-off lump at the start and/or a recurring yearly amount. Both are
    # applied to the principal and reduce the interest paid from then on.
    extra_repay_once: float = 0.0           # one-time extra repayment at t=0
    extra_repay_annual: float = 0.0         # recurring extra repayment per year

    def __post_init__(self) -> None:
        if self.mortgage_type not in ("annuity", "linear"):
            raise ValueError("mortgage_type must be 'annuity' or 'linear'")
        if self.down_payment > self.house_price:
            raise ValueError("down_payment cannot exceed house_price")
        if self.extra_repay_once < 0 or self.extra_repay_annual < 0:
            raise ValueError("extra repayments must be non-negative")
        if self.horizon_years > self.mortgage_term_years:
            raise ValueError("horizon cannot exceed mortgage term")
        if self.horizon_years < 1:
            raise ValueError("horizon must be at least 1 year")

    @property
    def loan_amount(self) -> float:
        return max(0.0, self.house_price - self.down_payment)
