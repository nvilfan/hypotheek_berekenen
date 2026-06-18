# 🏠 Huis & Hypotheek — Nederlandse hypotheekvergelijker

Een Streamlit-dashboard dat **maximaal 3 hypotheekscenario's** vergelijkt voor
een koper in Nederland. De tool laat zien welke keuze financieel het sterkst is
over de periode waarin je de woning wilt houden, inclusief belasting, kosten en
geld dat je eventueel buiten de woning laat groeien.

De interface is gericht op de uitkomst: de belangrijkste invoer staat links, de
pagina begint met een helder advies en daarna volgen de KPI's, grafieken en
details in overzichtelijke tabs.

De berekening neemt de belangrijkste Nederlandse factoren mee:

- **Hypotheekvormen:** annuïteitenhypotheek en lineaire hypotheek
- **Hypotheekrenteaftrek:** afgetopt op het wettelijke aftrekpercentage en
  gebaseerd op het ingevoerde inkomen
- **Eigenwoningforfait (EWF)** en de **Wet Hillen**
- **NHG**, NHG-premie en **startersvrijstelling** voor overdrachtsbelasting
- Waardestijging van de woning, verkoopkosten en aankoopkosten
- Box 3 voor geld dat buiten de woning wordt gespaard, vastgezet of belegd

## Hoe scenario's worden vergeleken

Voor een eerlijke vergelijking krijgt elk scenario dezelfde beschikbare eigen
middelen en hetzelfde maandbudget. Wat een scenario niet in de woning stopt,
wordt meegenomen als geld buiten de woning: het verschil in eigen inbreng plus
eventuele maandruimte doordat de hypotheeklast lager is.

Per scenario toont de tool:

- **Vermogen bij verkoop:** overwaarde na verkoopkosten en resterende hypotheek,
  plus de pot buiten de woning
- **Netto resultaat:** vermogen bij verkoop minus je totale eigen inleg
- **Jaarlijks rendement:** kasstroomgewogen rendement over de gekozen periode

De belangrijkste gedachte: aflossen is op zichzelf geen winst. Je zet geld om in
overwaarde. Het netto resultaat komt vooral uit:

```text
netto resultaat =
waardestijging woning - rente - aankoopkosten - verkoopkosten
+ belastingvoordeel + rendement op geld buiten de woning
```

## Lokaal draaien

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

Open daarna de URL die Streamlit toont, standaard `http://localhost:8501`.

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Projectstructuur

```text
app.py                 # Streamlit-dashboard
mortgage/
  models.py            # ScenarioInput en TaxAssumptions dataclasses
  tax.py               # inkomstenbelasting, box 1, box 3 en aankoopkosten
  engine.py            # maandelijkse simulatie naar ScenarioResult
tests/test_engine.py   # sanity tests voor het rekenmodel
```

Het pakket `mortgage` heeft geen Streamlit-afhankelijkheid. Het financiële model
kan dus ook los van de interface worden geïmporteerd en hergebruikt.

## Aannames

- De standaardwaarden zijn gebaseerd op **2025** en zijn aanpasbaar in de
  zijbalk.
- Dit is een vereenvoudigd en transparant model om scenario's te vergelijken.
  Het is **geen belasting- of financieel advies**.
- Het EWF wordt benaderd op basis van de marktwaarde van de woning; in de
  praktijk is de WOZ-waarde leidend. De belastingschijven zijn 2025 en worden
  alleen toegepast op het ingevoerde bruto inkomen.
