import os
from pathlib import Path
from functools import lru_cache

import gradio as gr
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tsfm_public.toolkit.get_model import get_model
from tsfm_public.toolkit.time_series_forecasting_pipeline import (
    TimeSeriesForecastingPipeline,
)

DATA_FILE = os.getenv("CAPACITY_DATA_FILE", "dynatrace_all_hosts.csv")
MODEL_PATH = "ibm-granite/granite-timeseries-ttm-r3"
CONTEXT_LENGTH = 90
PREDICTION_LENGTH = 30

CPU_THRESHOLD_DEFAULT = 80
MEM_THRESHOLD_DEFAULT = 80
DISK_THRESHOLD_DEFAULT = 85


def pct_to_num(s):
    return pd.to_numeric(
        s.astype(str).str.replace("%", "", regex=False),
        errors="coerce",
    )


@lru_cache(maxsize=4)
def load_raw(path_str):
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(
            f"Dataset not found: {path.resolve()}. "
            "Put dynatrace_all_hosts.csv in the same folder as this app."
        )
    df = pd.read_csv(path)
    if "Date" not in df.columns:
        raise ValueError("Expected a Dynatrace export containing a 'Date' column.")
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def get_maps(df):
    def collect(prefix):
        mapping = {}
        columns = set(df.columns)
        for column in df.columns:
            if not column.startswith(prefix):
                continue
            source_name = column
            # pandas suffixes duplicate CSV headers with .1, .2, etc. Treat
            # those as repeated telemetry for the same host, not new hosts.
            base, separator, suffix = column.rpartition(".")
            if separator and suffix.isdigit() and base in columns:
                source_name = base
            mapping[source_name[len(prefix):]] = column
        return mapping

    return (
        collect("CPU usage % - "),
        collect("Memory used % - "),
        collect("Disk used % - "),
    )


def all_hosts(df):
    cpu, mem, disk = get_maps(df)
    return sorted(set(cpu) | set(mem) | set(disk), key=str.lower)


def metric_series(df, col):
    s = pd.Series(pct_to_num(df[col]).to_numpy(), index=df["Date"])
    return s.sort_index()


def latest_value(s):
    s = s.dropna()
    if s.empty:
        return None, None
    return float(s.iloc[-1]), pd.Timestamp(s.index[-1])


def daily_features(s):
    hourly = s.sort_index()
    out = pd.DataFrame(
        {
            "avg": hourly.resample("1D").mean(),
            "p95": hourly.resample("1D").quantile(0.95),
            "samples": hourly.resample("1D").count(),
        }
    )

    # Permit only very short telemetry gaps.
    out[["avg", "p95"]] = out[["avg", "p95"]].interpolate(
        method="linear",
        limit=2,
        limit_area="inside",
    )
    return out


def latest_context(daily, n=CONTEXT_LENGTH):
    values = daily[["avg", "p95"]].copy()

    # Find the latest block with no remaining gaps.
    good = values.notna().all(axis=1)
    if not good.any():
        return None

    # Walk backwards from the latest valid day until a missing day.
    end_pos = np.where(good.to_numpy())[0][-1]
    start_pos = end_pos
    while start_pos >= 0 and good.iloc[start_pos]:
        start_pos -= 1
    block = values.iloc[start_pos + 1 : end_pos + 1]

    if len(block) < n:
        return None

    context = block.tail(n).reset_index()
    # The source index inherits the Dynatrace column name ("Date"). The
    # Granite pipeline requires the timestamp column to have this exact name.
    context = context.rename(columns={context.columns[0]: "timestamp"})
    return context


_PIPELINE = None


def get_pipeline():
    global _PIPELINE
    if _PIPELINE is None:
        model = get_model(
            model_path=MODEL_PATH,
            context_length=CONTEXT_LENGTH,
            prediction_length=PREDICTION_LENGTH,
            freq="1d",
        )
        _PIPELINE = TimeSeriesForecastingPipeline(
            model=model,
            timestamp_column="timestamp",
            id_columns=[],
            target_columns=["avg", "p95"],
            freq="1d",
            context_length=CONTEXT_LENGTH,
            prediction_length=PREDICTION_LENGTH,
            device="cpu",
            explode_forecasts=True,
        )
    return _PIPELINE


def forecast_metric(s):
    daily = daily_features(s)
    context = latest_context(daily)

    if context is None:
        observed_days = int(daily["avg"].notna().sum())
        return None, daily, (
            f"Insufficient recent continuous history "
            f"({observed_days} observed daily points; need ~{CONTEXT_LENGTH})."
        )

    fc = get_pipeline()(context).copy()
    fc = fc.rename(
        columns={
            "avg_prediction": "avg",
            "p95_prediction": "p95",
        }
    )

    required = ["timestamp", "avg", "p95"]
    missing = [c for c in required if c not in fc.columns]
    if missing:
        raise ValueError(
            f"Unexpected Granite output; missing {missing}. "
            f"Available columns: {list(fc.columns)}"
        )

    fc = fc[required].copy()
    fc["timestamp"] = pd.to_datetime(fc["timestamp"])
    fc["avg"] = fc["avg"].clip(0, 100)
    fc["p95"] = fc["p95"].clip(0, 100)

    return fc, daily, None


def snapshot_for_host(host):
    df = load_raw(DATA_FILE)
    cpu_map, mem_map, disk_map = get_maps(df)

    rows = []

    cpu_val = cpu_ts = None
    if host in cpu_map:
        cpu_val, cpu_ts = latest_value(metric_series(df, cpu_map[host]))
        if cpu_val is not None:
            rows.append(["CPU utilization", round(cpu_val, 2), cpu_ts])
            rows.append(["CPU idle (derived)", round(100 - cpu_val, 2), cpu_ts])

    if host in mem_map:
        v, ts = latest_value(metric_series(df, mem_map[host]))
        if v is not None:
            rows.append(["Memory used", round(v, 2), ts])

    if host in disk_map:
        v, ts = latest_value(metric_series(df, disk_map[host]))
        if v is not None:
            rows.append(["Disk used", round(v, 2), ts])

    snap = pd.DataFrame(rows, columns=["Metric", "Latest %", "Latest sample"])
    if not snap.empty:
        snap["Latest sample"] = pd.to_datetime(snap["Latest sample"]).dt.strftime(
            "%Y-%m-%d %H:%M"
        )

    return snap


def all_hosts_snapshot(sort_by="Host", sort_order="Ascending"):
    """Return the latest CPU/memory values for every host in the export."""
    df = load_raw(DATA_FILE)
    cpu_map, mem_map, disk_map = get_maps(df)
    rows = []

    for host in all_hosts(df):
        cpu = memory = None
        timestamps = []
        if host in cpu_map:
            cpu, ts = latest_value(metric_series(df, cpu_map[host]))
            if ts is not None:
                timestamps.append(ts)
        if host in mem_map:
            memory, ts = latest_value(metric_series(df, mem_map[host]))
            if ts is not None:
                timestamps.append(ts)
        if host in disk_map:
            _, ts = latest_value(metric_series(df, disk_map[host]))
            if ts is not None:
                timestamps.append(ts)
        rows.append({
            "Host": host,
            "CPU Utilization %": cpu,
            "CPU Idle %": None if cpu is None else 100 - cpu,
            "Memory Utilization %": memory,
            "Latest Sample": max(timestamps) if timestamps else pd.NaT,
        })

    table = pd.DataFrame(rows)
    if table.empty:
        return table
    numeric = ["CPU Utilization %", "CPU Idle %", "Memory Utilization %"]
    table[numeric] = table[numeric].round(2)
    ascending = sort_order == "Ascending"
    if sort_by in table.columns:
        table = table.sort_values(
            sort_by,
            ascending=ascending,
            na_position="last",
            key=(lambda s: s.str.lower()) if sort_by == "Host" else None,
        )
    table["Latest Sample"] = pd.to_datetime(table["Latest Sample"]).dt.strftime(
        "%Y-%m-%d %H:%M"
    )
    return table.reset_index(drop=True)


def top_current_cpu(limit=5):
    table = all_hosts_snapshot("CPU Utilization %", "Descending")
    if table.empty:
        return table
    columns = ["Host", "CPU Utilization %", "CPU Idle %", "Latest Sample"]
    return table.dropna(subset=["CPU Utilization %"])[columns].head(limit).reset_index(drop=True)


@lru_cache(maxsize=1)
def forecast_cpu_fleet():
    """Forecast all eligible CPU hosts and rank them by maximum forecast P95."""
    df = load_raw(DATA_FILE)
    cpu_map, _, _ = get_maps(df)
    rows = []
    skipped = 0

    for host in sorted(cpu_map, key=str.lower):
        series = metric_series(df, cpu_map[host])
        latest, _ = latest_value(series)
        try:
            forecast, _, error = forecast_metric(series)
        except Exception:
            skipped += 1
            continue
        if error or forecast is None:
            skipped += 1
            continue

        peak_row = forecast.loc[forecast["p95"].idxmax()]
        rows.append({
            "Host": host,
            "Current CPU %": latest,
            "Max Forecast CPU Avg %": float(forecast["avg"].max()),
            "Max Forecast CPU P95 %": float(peak_row["p95"]),
            "Forecast Peak Date": pd.Timestamp(peak_row["timestamp"]),
        })

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(
            "Max Forecast CPU P95 %", ascending=False
        ).reset_index(drop=True)
    return result, skipped, len(cpu_map)


def top_forecast_cpu(limit=5):
    table, skipped, total = forecast_cpu_fleet()
    if table.empty:
        message = (
            "**No fleet CPU forecasts are available.** Hosts need approximately "
            f"{CONTEXT_LENGTH} continuous daily observations."
        )
        return message, table

    output = table.head(limit).copy()
    numeric = output.select_dtypes(include="number").columns
    output[numeric] = output[numeric].round(2)
    output["Forecast Peak Date"] = pd.to_datetime(
        output["Forecast Peak Date"]
    ).dt.strftime("%Y-%m-%d")
    message = (
        f"Forecasted **{len(table)} of {total}** CPU hosts; "
        f"**{skipped}** lacked sufficient continuous history or could not be forecast. "
        "Ranked by maximum forecast CPU P95."
    )
    return message, output


def historical_plot(host, aggregation="Daily", periods=60):
    df = load_raw(DATA_FILE)
    cpu_map, mem_map, disk_map = get_maps(df)

    fig, ax = plt.subplots(figsize=(11, 5))
    plotted = False

    def trend(series):
        daily = daily_features(series)["avg"]
        if aggregation == "Monthly":
            return daily.resample("MS").mean().tail(int(periods))
        return daily.tail(int(periods))

    if host in cpu_map:
        d = trend(metric_series(df, cpu_map[host]))
        ax.plot(d.index, d, marker="o", markersize=3, label="CPU utilization")
        ax.plot(d.index, 100 - d, marker="o", markersize=3, label="CPU idle")
        plotted = True

    if host in mem_map:
        d = trend(metric_series(df, mem_map[host]))
        ax.plot(d.index, d, marker="o", markersize=3, label="Memory utilization")
        plotted = True

    if host in disk_map:
        d = trend(metric_series(df, disk_map[host]))
        ax.plot(d.index, d, alpha=0.65, label="Disk utilization")
        plotted = True

    ax.set_title(f"{host} — {aggregation.lower()} utilization trend")
    ax.set_ylabel("Percent")
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.2)
    if plotted:
        ax.legend()
    else:
        ax.text(0.5, 0.5, "No utilization data for this host", ha="center")
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def host_overview(host, aggregation, periods):
    return snapshot_for_host(host), historical_plot(host, aggregation, periods)


def trend_plot(host, aggregation, periods):
    return historical_plot(host, aggregation, periods)


def first_crossing(fc, col, threshold):
    hits = fc.loc[fc[col] >= threshold, "timestamp"]
    return None if hits.empty else pd.Timestamp(hits.iloc[0])


def run_forecast(host, cpu_threshold, mem_threshold, disk_threshold):
    df = load_raw(DATA_FILE)
    cpu_map, mem_map, disk_map = get_maps(df)

    forecasts = {}
    histories = {}
    notes = []

    metric_specs = [
        ("CPU", cpu_map, cpu_threshold),
        ("Memory", mem_map, mem_threshold),
        ("Disk", disk_map, disk_threshold),
    ]

    for label, mapping, threshold in metric_specs:
        if host not in mapping:
            notes.append(f"- **{label}:** not present in this Dynatrace export for this host.")
            continue

        series = metric_series(df, mapping[host])
        fc, daily, err = forecast_metric(series)
        histories[label] = daily

        if err:
            notes.append(f"- **{label}:** {err}")
            continue

        forecasts[label] = fc
        crossing = first_crossing(fc, "p95", threshold)
        if crossing is None:
            notes.append(
                f"- **{label}:** max forecast P95 **{fc['p95'].max():.1f}%**; "
                f"{threshold:.0f}% threshold not crossed in 30 days."
            )
        else:
            notes.append(
                f"- **{label}:** max forecast P95 **{fc['p95'].max():.1f}%**; "
                f"crosses {threshold:.0f}% on **{crossing.date()}**."
            )

    # Overall risk
    red = False
    amber = False
    for label, threshold in [
        ("CPU", cpu_threshold),
        ("Memory", mem_threshold),
        ("Disk", disk_threshold),
    ]:
        if label not in forecasts:
            continue
        mx = float(forecasts[label]["p95"].max())
        if mx >= threshold:
            red = True
        elif mx >= threshold - 10:
            amber = True

    risk = "RED" if red else ("AMBER" if amber else "GREEN")

    summary = (
        f"### {host} — 30-day capacity forecast: **{risk}**\n\n"
        + "\n".join(notes)
        + "\n\n"
        + "**CPU idle forecast is derived as `100 − forecast CPU average`; "
          "it is not a separately exported Dynatrace metric.**"
    )

    # Forecast plot
    fig, ax = plt.subplots(figsize=(11, 5))
    for label, fc in forecasts.items():
        hist = histories[label].dropna(subset=["avg"]).tail(45)
        ax.plot(hist.index, hist["avg"], label=f"{label} historical avg")
        ax.plot(
            fc["timestamp"],
            fc["avg"],
            linestyle="--",
            label=f"{label} forecast avg",
        )

        if label == "CPU":
            ax.plot(
                fc["timestamp"],
                100 - fc["avg"],
                linestyle=":",
                label="CPU idle forecast",
            )

    ax.axhline(cpu_threshold, linestyle=":", alpha=0.5, label=f"CPU threshold {cpu_threshold:.0f}%")
    ax.set_title(f"{host} — Granite TTM forecast")
    ax.set_ylabel("Percent")
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.2)
    if forecasts:
        ax.legend()
    else:
        ax.text(
            0.5,
            0.5,
            "No metric has enough recent history for a 90→30 forecast.",
            ha="center",
        )
    fig.autofmt_xdate()
    fig.tight_layout()

    # Forecast table
    all_dates = None
    table = None

    for label, fc in forecasts.items():
        part = fc.copy()
        part = part.rename(
            columns={
                "avg": f"{label} Avg %",
                "p95": f"{label} P95 %",
            }
        )

        keep = ["timestamp", f"{label} Avg %", f"{label} P95 %"]
        part = part[keep]

        if label == "CPU":
            part["CPU Idle Avg %"] = 100 - part["CPU Avg %"]

        if table is None:
            table = part
        else:
            table = pd.merge(table, part, on="timestamp", how="outer")

    if table is None:
        table = pd.DataFrame(
            {"Message": ["No 30-day forecast available for this host."]}
        )
    else:
        table = table.sort_values("timestamp")
        table["timestamp"] = pd.to_datetime(table["timestamp"]).dt.strftime("%Y-%m-%d")
        numeric_cols = table.select_dtypes(include="number").columns
        table[numeric_cols] = table[numeric_cols].round(2)

    return summary, fig, table


# Load host list at startup.
_initial_df = load_raw(DATA_FILE)
_HOSTS = all_hosts(_initial_df)
_DEFAULT_HOST = "HDS033008_LAB-AG" if "HDS033008_LAB-AG" in _HOSTS else _HOSTS[0]


with gr.Blocks(title="Infrastructure Capacity Predictor") as demo:
    gr.Markdown(
        """
# Infrastructure Capacity Predictor
**Dynatrace host metrics → IBM Granite TTM-R3 → host-level utilization and 30-day capacity forecast**

Review every host, then select one to see its daily or monthly trend and forecast.
"""
    )

    gr.Markdown("## Fleet CPU summary")
    with gr.Row():
        with gr.Column():
            gr.Markdown("### Top 5 — current CPU utilization")
            current_cpu_summary = gr.Dataframe(
                value=top_current_cpu(),
                interactive=False,
                label="Highest current CPU",
            )
        with gr.Column():
            gr.Markdown("### Top 5 — forecast CPU utilization")
            fleet_forecast_button = gr.Button(
                "Run fleet CPU forecast",
                variant="primary",
            )
            fleet_forecast_status = gr.Markdown(
                "Run once to rank every host with sufficient CPU history."
            )
            forecast_cpu_summary = gr.Dataframe(
                interactive=False,
                label="Highest forecast CPU P95",
            )

    fleet_forecast_button.click(
        fn=top_forecast_cpu,
        outputs=[fleet_forecast_status, forecast_cpu_summary],
    )

    gr.Markdown("## All hosts — latest utilization")
    with gr.Row():
        overview_sort = gr.Dropdown(
            choices=["Host", "CPU Utilization %", "CPU Idle %", "Memory Utilization %", "Latest Sample"],
            value="Host",
            label="Sort by",
        )
        overview_order = gr.Radio(
            choices=["Ascending", "Descending"],
            value="Ascending",
            label="Sort order",
        )
    overview_table = gr.Dataframe(
        value=all_hosts_snapshot(),
        interactive=False,
        label="All monitored hosts",
    )
    overview_sort.change(
        fn=all_hosts_snapshot,
        inputs=[overview_sort, overview_order],
        outputs=overview_table,
    )
    overview_order.change(
        fn=all_hosts_snapshot,
        inputs=[overview_sort, overview_order],
        outputs=overview_table,
    )

    gr.Markdown("## Host details")

    host = gr.Dropdown(
        choices=_HOSTS,
        value=_DEFAULT_HOST,
        label="Dynatrace host",
        interactive=True,
    )

    with gr.Row():
        cpu_threshold = gr.Slider(
            50, 95, value=CPU_THRESHOLD_DEFAULT, step=1,
            label="CPU P95 threshold (%)",
        )
        mem_threshold = gr.Slider(
            50, 95, value=MEM_THRESHOLD_DEFAULT, step=1,
            label="Memory P95 threshold (%)",
        )
        disk_threshold = gr.Slider(
            60, 99, value=DISK_THRESHOLD_DEFAULT, step=1,
            label="Disk P95 threshold (%)",
        )

    gr.Markdown("## Current host utilization")
    snapshot = gr.Dataframe(
        value=snapshot_for_host(_DEFAULT_HOST),
        interactive=False,
        label="Latest Dynatrace values",
    )

    gr.Markdown("## Historical utilization")
    with gr.Row():
        aggregation = gr.Radio(
            choices=["Daily", "Monthly"],
            value="Daily",
            label="Trend aggregation",
        )
        periods = gr.Slider(
            1, 120, value=60, step=1,
            label="Number of days/months",
        )
    history_chart = gr.Plot(value=historical_plot(_DEFAULT_HOST, "Daily", 60))

    run_button = gr.Button("Run 30-day Granite forecast", variant="primary")

    gr.Markdown("## Forecast")
    summary = gr.Markdown()
    forecast_chart = gr.Plot()
    forecast_table = gr.Dataframe(interactive=False, label="30-day forecast")

    host.change(
        fn=host_overview,
        inputs=[host, aggregation, periods],
        outputs=[snapshot, history_chart],
    )
    aggregation.change(
        fn=trend_plot,
        inputs=[host, aggregation, periods],
        outputs=history_chart,
    )
    periods.change(
        fn=trend_plot,
        inputs=[host, aggregation, periods],
        outputs=history_chart,
    )

    run_button.click(
        fn=run_forecast,
        inputs=[host, cpu_threshold, mem_threshold, disk_threshold],
        outputs=[summary, forecast_chart, forecast_table],
    )


if __name__ == "__main__":
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=True,
        show_error=True,
    )
