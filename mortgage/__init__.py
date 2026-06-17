"""Dutch first-home mortgage & investment comparison engine.

Pure-Python financial model (no Streamlit dependency) so it can be unit-tested
and reused. The Streamlit dashboard in ``app.py`` is only a thin UI on top of
:func:`mortgage.engine.run_scenario`.
"""

from .models import ScenarioInput, TaxAssumptions, CashAlternative, CASH_VEHICLES
from .engine import run_scenario, reference_cash, reference_budget, ScenarioResult

__all__ = [
    "ScenarioInput",
    "TaxAssumptions",
    "CashAlternative",
    "CASH_VEHICLES",
    "run_scenario",
    "reference_cash",
    "reference_budget",
    "ScenarioResult",
]
