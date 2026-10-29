# Demo-environment gap analysis (verified against the live database)

Measured by querying `valet_db` after `python utils/scripts/init_db.py --seed`.

| Requested | Actual | Verdict |
| --- | --- | --- |
| 3 customers | **2** (`customer.aisha@`, `customer.rohit@`) | MISSING |
| 3 vehicles | 3 (`UP 78 AB 1234`, `UP 78 CD 5678`, `DL 01 CA 9012`) | met, but Aisha owns 2 / Rohit 1 / third customer none |
| 2 valet operators | 2 (`valet.suresh@`, `valet.priya@`) | met |
| 1 manager | 1 (`manager.karan@`) | met |
| 2 parking zones | 3 (ZONE-A 40, ZONE-B 60, ZONE-C 25) | exceeded |
| Historical retrieval records | `retrieval_predictions` = **1** in the curated seed | thin; bulk comes from the history generator |
| GPS telemetry samples | **8 fixes, one custody session only** | thin |
| Normal movement samples | 6 `NORMAL` | thin |
| Suspicious movement samples | 2 `BREACH` | thin |
| Sample inspection records | 4 inspections / 6 detections | thin |
| Sample security alerts | **1** | thin |
| Sample retrieval predictions | **1** | thin |
| DEMO MODE | no global flag; opt-in per request | see note |
| Label simulated GPS | `TelemetrySource.SIMULATED` stored per row | met |
| Label synthetic ML data | `retrieval_history.json` carries `source` + `disclaimer` | met |
| Reset/reseed command | `init_db.py --reset --seed`, history generator separate | two commands, not one |

## Findings that contradict earlier notes

1. **`AI_DEMO_MODE` does not exist.** `grep -rn AI_DEMO_MODE` over the whole repo returns
   zero hits. Demo behaviour is opt-in *per request* via `POST /inspections/demo` and
   `POST /gps/simulate`, plus the `TELEMETRY_SOURCE=simulated` setting.
2. **`generate_operational_history.py` writes zero GPS events.** `grep -ci gps` on that
   906-line file returns `0`. So 90 days of generated history has no telemetry at all —
   the tracking map and movement history are empty for every generated session.
3. **`retrieval_episodes` is not a database table.** `generate_retrieval_history.py`
   writes `storage/data/retrieval_history.json` for model training, not DB rows.
