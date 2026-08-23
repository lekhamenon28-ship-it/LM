# Infrastructure Capacity Predictor

A Gradio application for reviewing Dynatrace host utilization and producing
30-day capacity forecasts with IBM Granite Timeseries TTM-R3.

## Features

- Sortable all-host CPU, CPU idle, and memory utilization table
- Top-five current and forecast CPU summaries
- Daily and monthly utilization trends
- Host-level CPU, memory, and disk forecasts
- Configurable capacity thresholds

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements_vm_granite.txt
python app_granite_capacity_v2.py
```

Place the Dynatrace export at `dynatrace_all_hosts.csv`, or set
`CAPACITY_DATA_FILE` to its location. Dynatrace CSV exports are intentionally
excluded from version control because they can contain private host telemetry.

