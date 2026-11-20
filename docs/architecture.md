# Architecture notes

## Module map

| Module | Responsibility | Depends on web stack? |
| --- | --- | --- |
| `app/normalize.py` | Phone (E.164), email, text cleanup, idempotency keys | No |
| `app/urgency.py` | Rule-based triage plus optional LLM refinement | No |
| `app/hours.py` | Business hours in the shop's time zone | No |
| `app/templates.py` | Customer and owner message copy | No |
| `app/store.py` | SQLite persistence, audit log, dead letters | No |
| `app/delivery.py` | Twilio and HubSpot clients, retry with backoff | No |
| `app/service.py` | The intake pipeline and lead lifecycle | No |
| `app/metrics.py` | Response-time and leakage report | No |
| `app/security.py` | HMAC and Twilio signature checks | No |
| `app/main.py` | FastAPI routes | Yes |
| `app/dashboard.py` | Server-rendered HTML | No |

Only `main.py` imports FastAPI. Everything else can be reused from a worker, a CLI, or a different framework.

## Decisions

**Persist before side effects.** `ingest()` commits the lead inside a transaction, and only then sends SMS or calls the CRM. A slow or failing provider delays a reply; it cannot lose a lead or roll back the record.

**Two kinds of "same lead".** A *replay* is the identical webhook delivered again (provider retry). A *duplicate* is the same person reaching out again, possibly through another channel. Replays return the original result and do nothing. Duplicates increment a counter and add an audit event, but the customer is not texted twice.

**Retry only what can succeed.** `DeliveryError` carries a `retryable` flag. 429 and 5xx retry with exponential backoff; other 4xx fail immediately into `dead_letters`, because sending the same bad request three times wastes time during an emergency.

**Escalation is a claim, not a scan.** `UPDATE leads SET escalated_at = ? WHERE ... RETURNING *` marks and returns stale leads in one statement. Running the job twice, or on two machines, pages the owner once per lead.

**Rules first, AI second.** Safety cases (gas, carbon monoxide) are matched by deterministic rules and get fixed wording that tells the customer to leave and call 911 or the utility. An LLM can be plugged in to refine borderline cases, but it is not allowed to downgrade a rule hit, and any exception falls back to the rule result.

**Measure the human, not the bot.** An instant SMS is easy. What wins the job is a person calling back. The report tracks auto-reply time and human callback time separately, and the headline number is "% of leads called back within 5 minutes".

**SQLite for the demo, Postgres next.** All SQL lives in `store.py`. SQLite runs in WAL mode with one locked connection, which is fine for one process. A Postgres implementation of the same methods is the first roadmap item.

## Metric definitions

| Metric | Definition |
| --- | --- |
| Auto-reply median | Median of `auto_replied_at - received_at` over leads that received an SMS |
| Callback median | Median of `human_touched_at - received_at` over leads that were contacted |
| Called < 5 min | Leads contacted within 300 s divided by **all** leads in the window (uncontacted leads count against it) |
| Waiting > N min | Leads never contacted whose age exceeds the escalation SLA |
| Duplicates caught | Repeat enquiries absorbed into existing leads |
