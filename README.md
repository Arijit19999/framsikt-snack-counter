# Snack Counter Automation

Framsikt Product Engineer take-home. Core logic for automating the snack-box refills in the Oslo and Bengaluru offices, with simulated stand-ins for the chat tool and the HR system.

**Design document (2 pages):** [`docs/design.pdf`](docs/design.pdf) (source: `docs/design.html`)

## The idea

Automate the coordination, not the shopping. Every empty box gets exactly one named owner at any moment, with a deadline. If they don't act, the system moves on automatically. Budget, HR claim and the monthly report all hang off that one refill record. People stay in the chat tool they already use.

```
reported empty -> OFFERED (1 person, 3 h) -> COMMITTED (cap reserved, 24 h) -> PURCHASED -> CLAIM_SUBMITTED -> APPROVED -> REIMBURSED
                    | decline / timeout: next person      | deadline missed: re-offer
                    | 3 misses / nobody in: OPEN_TO_ALL   | receipt > cap: NEEDS_REVIEW (coordinator)
  budget too low: BLOCKED_BUDGET (coordinator approves)
```

## Run it

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
pytest                          # 46 tests (the race test runs 10x, so pytest shows 55)
python demo.py                  # plays a full Oslo + Bengaluru month and prints the chat log + report
```

`tzdata` is only needed because Windows has no built-in time-zone database.

## Code map

| File | What it does |
|---|---|
| `snack_counter/refill_service.py` | **The refill lifecycle.** State machine, all commands (`report_empty`, `accept`, `decline`, `submit_receipt`, coordinator approvals) and `tick()` for every time-based rule |
| `snack_counter/selection.py` | **Choosing who buys.** Pure function: in that office today, not excluded, not owed money, fewest refills in 30 days, longest since last refill |
| `snack_counter/budget.py` | **Budget check.** Per office and local month: `remaining = budget - spent - reserved`, idempotent per refill |
| `snack_counter/money.py` | Exact money in integer minor units; mixing currencies or using floats raises |
| `snack_counter/report.py` | Monthly report: each office in its own currency + one NOK total at a stored monthly rate |
| `snack_counter/models.py`, `config.py` | Domain types and the two offices from the brief |
| `snack_counter/chat.py`, `hr.py`, `presence.py`, `clock.py` | Interfaces + **fakes** for the chat tool, HR system, who-is-where, and time |

## Important rules and the tests that prove them

| Rule | Test(s) |
|---|---|
| Two people cannot both commit to the same refill | `test_two_people_cannot_both_commit_to_the_same_refill`, `test_simultaneous_accepts_only_one_wins` (threaded; verified to fail if the lock is removed) |
| A second "empty" report doesn't start a second refill | `test_second_empty_report_does_not_start_a_second_refill` |
| A refill nobody completes is handed to someone else | `test_refill_nobody_completes_is_handed_to_someone_else`, `test_unanswered_offer_expires_and_moves_on`, `test_decline_passes_the_refill_to_the_next_volunteer` |
| Nobody responds: escalate | `test_after_three_failed_offers_it_is_opened_to_everyone_and_coordinator_told`, `test_nobody_in_the_office_goes_straight_to_the_channel` |
| Fair rotation, presence and visitors | `test_selection.py` (all), especially `test_refills_rotate_across_volunteers_over_a_month`, `test_visitor_counts_for_the_office_they_are_visiting` |
| Budget check: cap, review, blocked, override | `test_accepting_reserves_the_spend_cap`, `test_receipt_above_cap_waits_for_coordinator_before_going_to_hr`, `test_cap_shrinks_to_what_is_left_in_the_budget`, `test_exhausted_budget_blocks_the_refill_until_coordinator_approves` |
| Budgets and totals correct across both currencies | `test_each_office_is_budgeted_in_its_own_currency`, `test_visitor_buys_in_local_currency_and_it_hits_the_visited_office_budget`, `test_combined_total_converts_each_office_once_into_nok`, `test_report_refuses_to_guess_a_missing_exchange_rate` |
| Budget month follows each office's local time | `test_budget_month_follows_each_office_local_time`, `test_commit_in_one_month_and_purchase_in_the_next_is_counted_at_purchase` |
| HR claims: correct payload, no duplicates, status tracked | `test_receipt_within_cap_is_spent_and_claimed_with_the_right_details`, `test_hr_outage_is_retried_without_creating_duplicate_claims`, `test_full_path_from_empty_to_reimbursed`, `test_hr_rejection_gives_the_money_back_to_the_budget`, `test_coordinator_is_nudged_once_when_hr_is_slow` |

## Assumptions

I asked these questions but had no answers before the deadline, so I made assumptions. Each one is a single config value or a small change.

1. The management report is in **NOK**, using one stored exchange rate per month.
2. A purchase counts against the **office whose box was refilled**, in that office's currency. A visitor's claim is sent in the receipt currency, and HR pays them in their payroll currency.
3. When the budget is used up, refills pause until the **office coordinator** approves one more.
4. HR offers a **claim-status lookup** and accepts an **idempotency key**.
5. The chat tool's **status** shows office / remote / leave. Visitors set their location with a command.
6. Each office has **one coordinator** for escalations and approvals.

Timings (3 h to answer an offer, 24 h to buy, reminder 4 h before the deadline, 3 direct offers, 30-day fairness window, HR nudge after 5 days) are all in `Settings`.

## Not built on purpose

The chat-bot command parser, a database, a web UI, receipt OCR, sensors and live FX feeds. Reasons are in the design doc. The service methods are exactly what a thin chat-bot adapter would call. In production the lock becomes a conditional `UPDATE ... WHERE state IN (...)` in Postgres.
