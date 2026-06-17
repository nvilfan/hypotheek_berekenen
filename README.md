# 🏠 Huis & Hypotheek — Dutch first-home investment comparison

A Streamlit dashboard that compares **up to 3 house-buying scenarios** for a
first-time, solo buyer in the Netherlands over a chosen holding period, and
shows whether buying the home pays off once you account for tax and costs.

It models the things that actually move the answer in NL:

- **Mortgage types:** annuïteit (annuity) and lineair (linear) amortization
- **Hypotheekrenteaftrek** — mortgage-interest deduction, capped at the
  statutory rate (aftrektarief), derived from the buyer's income bracket
- **Eigenwoningforfait (EWF)** added to box 1 income, with the **Wet Hillen**
  reduction
- **NHG** borgtochtprovisie and the **startersvrijstelling** (0% transfer tax)
- House appreciation, selling costs, and purchase costs (kosten koper)

## How scenarios are compared

Each scenario is evaluated at its **own actual monthly mortgage payment** — no
shared budget. For every scenario the tool reports the **net worth at sale**
(your equity = home value − selling costs − remaining mortgage), the **net
result / profit** (net worth minus everything you put in), and the
money-weighted annualised return (IRR).

The key idea the dashboard makes explicit: repaying principal is **not** profit
— it just turns cash into home equity. Your real gain is

```
profit = house appreciation − interest − purchase costs − selling costs + tax relief
```

shown as a per-scenario waterfall that reconciles exactly to the net result.

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

Then open the URL Streamlit prints (default http://localhost:8501).

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Project layout

```
app.py                 # Streamlit dashboard (thin UI layer)
mortgage/
  models.py            # ScenarioInput + TaxAssumptions dataclasses
  tax.py               # income brackets, box 1 home benefit, purchase costs
  engine.py            # month-by-month simulation -> ScenarioResult
tests/test_engine.py   # sanity tests for the model
```

The `mortgage` package has **no Streamlit dependency**, so the financial model
can be imported and reused independently of the UI.

## Assumptions & caveats

- Defaults reflect **2025** figures and are all editable in the sidebar — update
  them each tax year.
- This is a simplified, transparent model for comparison, **not tax or
  financial advice**. The EWF is approximated on the home's market value (the
  real basis is the lagging WOZ value); income-tax brackets are 2025 and applied
  to the entered gross income only.
