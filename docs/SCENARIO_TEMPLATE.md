# Scenario template — the thinking part (Kingsley)

`data/synthetic/scenario.yaml` is the single place where a scenario is defined. The pipeline reads it;
people read it. This page explains each block and the questions to answer before filling it in. Write
the answers down in the file — interviewers ask "why that number?" far more than "how did you code it?".

## 1. Framing

**Who is the user?** A county logistics coordinator (or food-bank dispatcher, or Red Cross shelter
manager) on the morning after landfall with a list of shelters, a few trucks and not enough of anything.

**What decision does the tool support?** *Which truck takes what, from which warehouse, to which shelter,
in what order, given which roads are out right now — and why.* Not "predict the flood". Not "route one van".

**What does a good answer look like?** A plan a human can read aloud and argue with. Every row has an
explanation string. That is the interface contract in `src/ianrelief/allocate.py`.

## 2. `decision_time_utc` and `planning_horizon_hours`

- The decision time is the instant the planner is standing at. Inventory, demand and closures are all
  evaluated "as of" this moment (latest row with `valid_from/as_of <= T`; closures with `start <= T < end`).
- Try several: `2022-09-29T12:00Z` (first morning), `2022-10-01T15:00Z` (I-75 closed at the Myakka
  River, Sanibel/Pine Island still cut off), `2022-10-06T12:00Z` (Pine Island road reopened).
- Horizon: one shift? one day? If a truck can do two round trips in the horizon, the engine must model that.

## 3. Commodities

Water, shelf-stable meals, tarps, ice are the FEMA/Red Cross staples in the first 72 h. Keep `unit_kg`
honest — a case of water is ~15 kg and dominates truck capacity; meals are light and bulky (pallets, not kg).
Question: do you model **weight**, **pallets**, or both? The current schema has both columns.

## 4. Objective

Pick ONE primary objective and write it as a sentence, then as a formula. Candidates:

- minimise priority-weighted unmet demand at the end of the horizon
- maximise the number of shelters that receive *any* water within N hours
- minimise total truck-kilometres subject to serving all priority-1 demand

Then a secondary objective and a tie-break. If you cannot say which of two plans is better, the
optimiser cannot either.

## 5. Constraints

Hard constraints (never violated): truck capacity, truck availability time, no route through an active
closure, warehouse stock. Soft constraints (penalised): late delivery to priority-1 shelters, very long
routes, a single shelter receiving from three different trucks.

Write down the ones you are *not* modelling yet (driver hours, fuel, loading dock throughput, curfews).

## 6. Assumptions

Copy the honest list from `scenario.yaml` and extend it. In particular:

- **Closures are reconstructed.** Each row in `closures.csv` has `time_confidence` and a `source_note`.
  Upgrade `assumed` rows to `approximate`/`reported` with a citation, or delete them.
- **Demand is synthetic.** Per-shelter populations for Ian were not published in a machine-readable form.
  If you find county EOC situation reports with shelter counts, use them and cite them.
- **Straight-line distance is not travel time.** The demo uses it; the engine must not.

## 7. Success criteria and user testing

How will you know the tool helped? Suggested: put the plan in front of one practitioner (food-bank
logistics coordinator, Red Cross volunteer, university emergency-management staff) and ask three things:
"Which row would you change first?", "What information is missing from the explanation?", "What would you
never let a computer decide here?". Write the answers into `docs/DECISIONS.md`.

## 8. Checklist before you change `scenario.yaml`

- [ ] every `TODO(Kingsley)` replaced or consciously deferred (say so in a comment)
- [ ] `commodities` matches `inventory.csv` and `demand.csv`
- [ ] `decision_time_utc` is UTC with a `Z`
- [ ] `make test` passes
- [ ] `make demo-offline` (and `make demo` if Docker is up) prints what you expect
