import os
from pathlib import Path

import gradio as gr
import matplotlib.pyplot as plt
import pandas as pd

from tsfm_public.toolkit.get_model import get_model
from tsfm_public.toolkit.time_series_forecasting_pipeline import (
    TimeSeriesForecastingPipeline,
)

DATA_FILE = os.getenv("CAPACITY_DATA_FILE", "dynatrace_hds033008_daily_ttm_120d.csv")

MODEL_PATH = "ibm-granite/granite-timeseries-ttm-r3"
CONTEXT_LENGTH = 90
PREDICTION_LENGTH = 30
TARGETS = ["cpu_avg", "cpu_p95", "memory_avg", "memory_p95"]

_pipeline = None


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        model = get_model(
            model_path=MODEL_PATH,
            context_length=CONTEXT_LENGTH,
            prediction_length=PREDICTION_LENGTH,
            freq="1d",
        )

        _pipeline = TimeSeriesForecastingPipeline(
            model=model,
            timestamp_column="timestamp",
            id_columns=[],
            target_columns=TARGETS,
            freq="1d",
            context_length=CONTEXT_LENGTH,
            prediction_length=PREDICTION_LENGTH,
            device="cpu",
            explode_forecasts=True,
        )
    return _pipeline


def load_data():
    path = Path(DATA_FILE)
    if not path.exists():
        raise FileNotFoundError(
            f"Could not find {path.resolve()}. Put the cleaned Dynatrace CSV "
            "in the same directory as this app."
        )

    df = pd.read_csv(path, parse_dates=["timestamp"]).sort_values("timestamp")
    df = df.dropna(subset=TARGETS).reset_index(drop=True)

    if len(df) < CONTEXT_LENGTH:
        raise ValueError(f"Need at least {CONTEXT_LENGTH} daily rows, found {len(df)}.")
    return df


def first_crossing(df, column, threshold):
    hits = df.loc[df[column] >= threshold, "timestamp"]
    return None if hits.empty else pd.Timestamp(hits.iloc[0])


def run_forecast(cpu_threshold, memory_threshold):
    data = load_data()
    context = data.tail(CONTEXT_LENGTH).copy()

    forecast = get_pipeline()(context).copy()

    forecast = forecast.rename(
        columns={
            "cpu_avg_prediction": "cpu_avg",
            "cpu_p95_prediction": "cpu_p95",
            "memory_avg_prediction": "memory_avg",
            "memory_p95_prediction": "memory_p95",
        }
    )

    required = ["timestamp"] + TARGETS
    missing = [c for c in required if c not in forecast.columns]
    if missing:
        raise ValueError(
            f"Unexpected Granite output. Missing {missing}. "
            f"Available columns: {list(forecast.columns)}"
        )

    forecast = forecast[required].copy()
    forecast["timestamp"] = pd.to_datetime(forecast["timestamp"])

    for c in TARGETS:
        forecast[c] = forecast[c].clip(0, 100)

    cpu_cross = first_crossing(forecast, "cpu_p95", cpu_threshold)
    mem_cross = first_crossing(forecast, "memory_p95", memory_threshold)

    risk = "GREEN"
    if cpu_cross is not None or mem_cross is not None:
        risk = "RED"
    elif (
        forecast["cpu_p95"].max() >= cpu_threshold - 10
        or forecast["memory_p95"].max() >= memory_threshold - 10
    ):
        risk = "AMBER"

    cpu_text = cpu_cross.strftime("%Y-%m-%d") if cpu_cross is not None else "Not within 30 days"
    mem_text = mem_cross.strftime("%Y-%m-%d") if mem_cross is not None else "Not within 30 days"

    summary = f"""
### Capacity forecast: **{risk}**

- Forecast window: **{forecast['timestamp'].min().date()} → {forecast['timestamp'].max().date()}**
- Maximum forecast CPU P95: **{forecast['cpu_p95'].max():.1f}%**
- Maximum forecast Memory P95: **{forecast['memory_p95'].max():.1f}%**
- CPU {cpu_threshold:.0f}% threshold: **{cpu_text}**
- Memory {memory_threshold:.0f}% threshold: **{mem_text}**
"""

    fig, ax = plt.subplots(figsize=(11, 5))
    hist = data.tail(60)

    ax.plot(hist["timestamp"], hist["cpu_p95"], label="Historical CPU P95")
    ax.plot(
        forecast["timestamp"],
        forecast["cpu_p95"],
        linestyle="--",
        label="Forecast CPU P95",
    )
    ax.plot(hist["timestamp"], hist["memory_p95"], label="Historical Memory P95")
    ax.plot(
        forecast["timestamp"],
        forecast["memory_p95"],
        linestyle="--",
        label="Forecast Memory P95",
    )

    ax.axhline(cpu_threshold, linestyle=":", label=f"CPU threshold {cpu_threshold:.0f}%")
    ax.axhline(
        memory_threshold,
        linestyle="-.",
        label=f"Memory threshold {memory_threshold:.0f}%",
    )
    ax.set_ylim(0, 100)
    ax.set_ylabel("Utilization (%)")
    ax.set_title("Dynatrace Capacity Forecast — Granite TTM-R3")
    ax.grid(True, alpha=0.2)
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()

    table = forecast.copy()
    table["timestamp"] = table["timestamp"].dt.strftime("%Y-%m-%d")
    for c in TARGETS:
        table[c] = table[c].round(2)

    return summary, fig, table


with gr.Blocks(title="Infrastructure Capacity Predictor") as demo:
    gr.Markdown(
        """
# Infrastructure Capacity Predictor
**Dynatrace historical data → IBM Granite TTM-R3 → 30-day capacity forecast**

POC host: `HDS033008_LAB-AG`
"""
    )

    with gr.Row():
        cpu_threshold = gr.Slider(50, 95, value=80, step=1, label="CPU P95 threshold (%)")
        memory_threshold = gr.Slider(50, 95, value=80, step=1, label="Memory P95 threshold (%)")

    run_button = gr.Button("Run 30-day forecast", variant="primary")
    summary = gr.Markdown()
    chart = gr.Plot(label="Forecast")
    table = gr.Dataframe(label="30-day forecast", interactive=False)

    run_button.click(
        fn=run_forecast,
        inputs=[cpu_threshold, memory_threshold],
        outputs=[summary, chart, table],
    )


if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860, show_error=True)
