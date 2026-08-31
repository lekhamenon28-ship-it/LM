import os
import re
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
MODEL_CHOICES = [
    "ibm-granite/granite-timeseries-ttm-r3",
    "ibm-granite/granite-timeseries-ttm-r2",
    "ibm-granite/granite-timeseries-ttm-r1",
]
CONTEXT_LENGTH = 90
PREDICTION_LENGTH = 30

CPU_THRESHOLD_DEFAULT = 80
MEM_THRESHOLD_DEFAULT = 80
DISK_THRESHOLD_DEFAULT = 85
LOW_UTILIZATION_THRESHOLD = 30


def pct_to_num(s):
    return pd.to_numeric(
        s.astype(str).str.replace("%", "", regex=False),
        errors="coerce",
    )


TIMESTAMP_NAMES = {
    "date", "timestamp", "datetime", "time", "recorded_at", "event_time",
    "sample_time", "collection_time", "start_time", "end_time", "created_at",
    "observed_at", "time_stamp", "date_time", "period", "day",
}
HOST_NAMES = {
    "host", "hostname", "host_name", "server", "server_name", "node", "node_name",
    "instance", "instance_name", "machine", "machine_name", "device", "device_name",
    "resource", "resource_name", "system", "system_name", "asset", "asset_name",
    "vm", "vm_name",
}
METRIC_NAMES = {"metric", "metric_name", "measure", "measurement", "type"}
VALUE_NAMES = {"value", "metric_value", "utilization", "usage", "percentage", "percent"}
RESOURCE_TYPE_NAMES = {
    "device_type", "resource_type", "host_type", "system_type", "asset_type",
    "category", "role",
}


def metric_kind(name):
    """Map a broadly named infrastructure metric to a canonical category."""
    normalized = re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()
    words = set(normalized.split())
    # Capacity charts use utilization/used values; idle/free/available values
    # are complementary metrics and must not overwrite utilization.
    if words & {"idle", "free", "available", "availability"}:
        return None
    if "cpu" in words or "processor" in words:
        return "CPU"
    if "memory" in words or "mem" in words or "ram" in words:
        return "Memory"
    if "disk" in words or "storage" in words or "filesystem" in words:
        return "Disk"
    return None


def find_named_column(columns, aliases):
    for column in columns:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(column).lower()).strip("_")
        if normalized in aliases:
            return column
    return None


def find_timestamp_column(source):
    """Find a time column by name, then safely infer it from its values."""
    exact = find_named_column(source.columns, TIMESTAMP_NAMES)
    if exact is not None:
        return exact

    # Accept decorated headers such as "Timestamp (UTC)" or "Start Time GMT".
    for column in source.columns:
        normalized = re.sub(r"[^a-z0-9]+", " ", str(column).lower()).strip()
        words = set(normalized.split())
        if "timestamp" in words or "datetime" in words:
            return column
        if words & {"date", "time"} and words & {
            "start", "end", "sample", "event", "recorded", "observed", "utc", "gmt",
        }:
            return column

    # Last resort: infer from non-numeric values, avoiding utilization columns.
    for column in source.columns:
        values = source[column].dropna()
        if values.empty or pd.api.types.is_numeric_dtype(values):
            continue
        parsed = pd.to_datetime(values, errors="coerce")
        if parsed.notna().mean() >= 0.8 and parsed.nunique() >= min(2, len(parsed)):
            return column
    return None


def find_host_column(columns):
    exact = find_named_column(columns, HOST_NAMES)
    if exact is not None:
        return exact
    for column in columns:
        normalized = re.sub(r"[^a-z0-9]+", " ", str(column).lower()).strip()
        words = set(normalized.split())
        if words & {"host", "hostname", "server", "node", "instance", "machine", "device", "resource", "asset", "vm"}:
            if words & {"name", "id", "identifier"}:
                return column
    return None


def canonical_prefix(kind):
    return {
        "CPU": "CPU usage % - ",
        "Memory": "Memory used % - ",
        "Disk": "Disk used % - ",
    }[kind]


def finalize_normalized_data(frame):
    """Clean canonical utilization data and attach an audit summary."""
    frame = frame.copy()
    input_rows = len(frame)
    metric_columns = [
        column for column in frame.columns
        if column.startswith(("CPU usage % - ", "Memory used % - ", "Disk used % - "))
    ]
    ratio_columns = []
    invalid_values = 0

    for column in metric_columns:
        values = pct_to_num(frame[column])
        observed = values.dropna()
        # Monitoring exports commonly use either 0-1 ratios or 0-100 percentages.
        if not observed.empty and observed.max() <= 1 and observed.min() >= 0 and observed.max() > 0:
            values = values * 100
            ratio_columns.append(column)
        invalid = values.notna() & ~values.between(0, 100)
        invalid_values += int(invalid.sum())
        frame[column] = values.mask(invalid)

    frame = frame.sort_values("Date")
    duplicate_rows = int(frame.duplicated(subset=["Date"], keep=False).sum())
    if frame["Date"].duplicated().any():
        aggregations = {
            column: "mean" if column in metric_columns else "first"
            for column in frame.columns if column != "Date"
        }
        frame = frame.groupby("Date", as_index=False).agg(aggregations)

    frame.attrs["normalization"] = {
        "input_rows": input_rows,
        "output_rows": len(frame),
        "metric_columns": len(metric_columns),
        "ratio_columns": len(ratio_columns),
        "duplicate_rows": duplicate_rows,
        "invalid_values": invalid_values,
    }
    return frame.reset_index(drop=True)


def normalize_infrastructure_data(source):
    """Normalize common wide, host-column, and long metric CSV layouts."""
    source = source.copy()
    source.columns = [str(column).strip() for column in source.columns]
    timestamp_col = find_timestamp_column(source)
    if timestamp_col is None:
        raise ValueError(
            "No time column could be detected. Name it Date, Timestamp, Datetime, "
            "Time, Start Time, Recorded At, Event Time, Sample Time, or Collection Time."
        )

    source[timestamp_col] = pd.to_datetime(source[timestamp_col], errors="coerce")
    source = source.dropna(subset=[timestamp_col])
    if source.empty:
        raise ValueError(f"The '{timestamp_col}' column contains no valid timestamps.")

    # Preserve an existing Dynatrace-style wide export.
    dynatrace_columns = [
        c for c in source.columns
        if c.startswith(("CPU usage % - ", "Memory used % - ", "Disk used % - "))
    ]
    if dynatrace_columns:
        return finalize_normalized_data(source.rename(columns={timestamp_col: "Date"}))

    host_col = find_host_column(source.columns)
    resource_type_col = find_named_column(source.columns, RESOURCE_TYPE_NAMES)
    metric_col = find_named_column(source.columns, METRIC_NAMES)
    value_col = find_named_column(source.columns, VALUE_NAMES)
    result = pd.DataFrame({"Date": sorted(source[timestamp_col].unique())})
    result = result.set_index("Date")

    def add_series(frame, kind, host, values):
        name = canonical_prefix(kind) + str(host).strip()
        series = pd.Series(values.to_numpy(), index=frame[timestamp_col])
        series = pct_to_num(series).groupby(level=0).mean()
        result[name] = series

    if host_col is not None and metric_col is not None and value_col is not None:
        # Long layout: timestamp, host, metric, value.
        working = source[[timestamp_col, host_col, metric_col, value_col]].copy()
        working["_kind"] = working[metric_col].map(metric_kind)
        working = working.dropna(subset=[host_col, "_kind"])
        for (host_name, kind), frame in working.groupby([host_col, "_kind"]):
            add_series(frame, kind, host_name, frame[value_col])
    elif host_col is not None:
        # Row layout: timestamp, host, cpu, memory, disk.
        metric_columns = [(c, metric_kind(c)) for c in source.columns]
        metric_columns = [(c, kind) for c, kind in metric_columns if kind]
        for host_name, frame in source.dropna(subset=[host_col]).groupby(host_col):
            for column, kind in metric_columns:
                add_series(frame, kind, host_name, frame[column])
    else:
        # Wide layout: timestamp plus columns such as server01_cpu_usage.
        for column in source.columns:
            kind = metric_kind(column)
            if not kind:
                continue
            host_name = re.sub(
                r"(?i)(cpu|processor|memory|mem|ram|disk|storage|filesystem|usage|used|utilization|util|percent|pct)",
                " ",
                column,
            )
            host_name = re.sub(r"[^A-Za-z0-9._-]+", " ", host_name).strip(" ._-")
            add_series(source, kind, host_name or "Infrastructure", source[column])

    if host_col is not None and resource_type_col is not None:
        for host_name, frame in source.dropna(subset=[host_col]).groupby(host_col):
            types = frame[resource_type_col].dropna().astype(str)
            if not types.empty:
                result[f"Resource type - {str(host_name).strip()}"] = types.iloc[0]

    result = result.sort_index().reset_index()
    if len(result.columns) == 1:
        raise ValueError(
            "No CPU, memory, or disk utilization columns were recognized. "
            "Use metric names containing CPU, memory/RAM, or disk/storage."
        )
    return finalize_normalized_data(result)


def normalization_summary(df):
    """Describe the transformations performed on an uploaded dataset."""
    info = df.attrs.get("normalization", {})
    return (
        "**Normalization complete** — converted the upload to canonical `Date`, "
        "CPU, memory, and disk percentage fields; "
        f"produced **{info.get('output_rows', len(df)):,} rows** and "
        f"**{info.get('metric_columns', 0)} metric columns**. "
        f"Ratio-scaled columns: **{info.get('ratio_columns', 0)}**; "
        f"duplicate timestamp rows consolidated: **{info.get('duplicate_rows', 0)}**; "
        f"out-of-range values removed: **{info.get('invalid_values', 0)}**."
    )


def normalized_preview(df, rows=10):
    preview = df.head(rows).copy()
    preview["Date"] = pd.to_datetime(preview["Date"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    return preview


@lru_cache(maxsize=4)
def load_raw(path_str):
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(
            f"Dataset not found: {path.resolve()}. "
            "Upload an infrastructure metrics file or configure CAPACITY_DATA_FILE."
        )
    suffix = path.suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xls"}:
        raise ValueError("Supported file types are CSV, XLSX, and XLS.")

    errors = []
    # Some monitoring exports include report-title or metadata rows before headers.
    for header_row in range(0, 11):
        try:
            if suffix == ".csv":
                frame = pd.read_csv(path, sep=None, engine="python", header=header_row)
            else:
                frame = pd.read_excel(path, header=header_row)
            return normalize_infrastructure_data(frame)
        except ImportError as exc:
            raise ValueError(
                "Excel support requires openpyxl. Install the project requirements "
                "or upload the sheet as CSV."
            ) from exc
        except (ValueError, pd.errors.ParserError) as exc:
            errors.append(str(exc))

    raise ValueError(
        f"Could not recognize a metrics table in the first 11 rows. {errors[0]}"
    )


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


def get_resource_types(df):
    prefix = "Resource type - "
    resource_types = {}
    for column in df.columns:
        if column.startswith(prefix):
            values = df[column].dropna().astype(str)
            if not values.empty:
                resource_types[column[len(prefix):]] = values.iloc[0]
    return resource_types


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


def dataset_path(uploaded_file=None):
    """Use an uploaded CSV when supplied, otherwise use the configured default."""
    return str(uploaded_file) if uploaded_file else DATA_FILE


_PIPELINES = {}


def get_pipeline(model_path=MODEL_PATH):
    if model_path not in _PIPELINES:
        model = get_model(
            model_path=model_path,
            context_length=CONTEXT_LENGTH,
            prediction_length=PREDICTION_LENGTH,
            freq="1d",
        )
        _PIPELINES[model_path] = TimeSeriesForecastingPipeline(
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
    return _PIPELINES[model_path]


def forecast_metric(s, model_path=MODEL_PATH):
    daily = daily_features(s)
    context = latest_context(daily)

    if context is None:
        observed_days = int(daily["avg"].notna().sum())
        return None, daily, (
            f"Insufficient recent continuous history "
            f"({observed_days} observed daily points; need ~{CONTEXT_LENGTH})."
        )

    fc = get_pipeline(model_path)(context).copy()
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


def snapshot_for_host(host, uploaded_file=None):
    df = load_raw(dataset_path(uploaded_file))
    cpu_map, mem_map, disk_map = get_maps(df)
    resource_type = get_resource_types(df).get(host, "Unspecified")

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
        snap.insert(0, "Resource type", resource_type)
    if not snap.empty:
        snap["Latest sample"] = pd.to_datetime(snap["Latest sample"]).dt.strftime(
            "%Y-%m-%d %H:%M"
        )

    return snap


def all_hosts_snapshot(sort_by="Host", sort_order="Ascending", uploaded_file=None):
    """Return the latest CPU/memory values for every host in the export."""
    df = load_raw(dataset_path(uploaded_file))
    cpu_map, mem_map, disk_map = get_maps(df)
    resource_types = get_resource_types(df)
    rows = []

    for host in all_hosts(df):
        cpu = memory = disk = None
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
            disk, ts = latest_value(metric_series(df, disk_map[host]))
            if ts is not None:
                timestamps.append(ts)
        rows.append({
            "Host": host,
            "Resource Type": resource_types.get(host, "Unspecified"),
            "CPU Utilization %": cpu,
            "CPU Idle %": None if cpu is None else 100 - cpu,
            "Memory Utilization %": memory,
            "Disk/Storage Utilization %": disk,
            "Latest Sample": max(timestamps) if timestamps else pd.NaT,
        })

    table = pd.DataFrame(rows)
    if table.empty:
        return table
    numeric = [
        "CPU Utilization %", "CPU Idle %", "Memory Utilization %",
        "Disk/Storage Utilization %",
    ]
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


def top_current_cpu(uploaded_file=None, limit=5):
    table = all_hosts_snapshot("Host", "Ascending", uploaded_file)
    if table.empty:
        return table
    utilization_columns = [
        "CPU Utilization %", "Memory Utilization %", "Disk/Storage Utilization %"
    ]
    table = table.copy()
    table["_Peak Utilization"] = table[utilization_columns].max(axis=1)
    table = table.sort_values("_Peak Utilization", ascending=False, na_position="last")
    columns = [
        "Host", "Resource Type", "CPU Utilization %", "Memory Utilization %",
        "Disk/Storage Utilization %", "Latest Sample",
    ]
    return table[columns].head(limit).reset_index(drop=True)


def resource_health_tables(uploaded_file=None):
    """Classify resources from their latest available utilization values."""
    table = all_hosts_snapshot("Host", "Ascending", uploaded_file)
    if table.empty:
        return {name: table for name in ("Highly utilized", "Low utilized", "Healthy")}

    metric_columns = [
        "CPU Utilization %", "Memory Utilization %", "Disk/Storage Utilization %"
    ]
    thresholds = pd.Series({
        "CPU Utilization %": CPU_THRESHOLD_DEFAULT,
        "Memory Utilization %": MEM_THRESHOLD_DEFAULT,
        "Disk/Storage Utilization %": DISK_THRESHOLD_DEFAULT,
    })
    metrics = table[metric_columns]
    highly_utilized = metrics.ge(thresholds).any(axis=1)
    low_utilized = metrics.notna().any(axis=1) & (
        metrics.le(LOW_UTILIZATION_THRESHOLD) | metrics.isna()
    ).all(axis=1)
    healthy = ~(highly_utilized | low_utilized)
    return {
        "Highly utilized": table.loc[highly_utilized].reset_index(drop=True),
        "Low utilized": table.loc[low_utilized].reset_index(drop=True),
        "Healthy": table.loc[healthy].reset_index(drop=True),
    }


def forecast_risk_resources(forecast_table):
    if forecast_table is None or len(forecast_table) == 0:
        return pd.DataFrame()
    table = forecast_table.copy()
    thresholds = table["Metric"].map({
        "CPU": CPU_THRESHOLD_DEFAULT,
        "Memory": MEM_THRESHOLD_DEFAULT,
        "Disk/Storage": DISK_THRESHOLD_DEFAULT,
    })
    return table.loc[table["Max Forecast P95 %"].ge(thresholds)].reset_index(drop=True)


def dashboard_html(uploaded_file=None, forecast_table=None):
    groups = resource_health_tables(uploaded_file)
    counts = {
        "Highly utilized": len(groups["Highly utilized"]),
        "Low utilized": len(groups["Low utilized"]),
        "Forecast risk": len(forecast_risk_resources(forecast_table)),
        "Healthy": len(groups["Healthy"]),
    }
    cards = [
        ("high", "Highly utilized", counts["Highly utilized"], "At or above a capacity threshold"),
        ("low", "Low utilized", counts["Low utilized"], f"All available metrics at or below {LOW_UTILIZATION_THRESHOLD}%"),
        ("risk", "Forecast risk", counts["Forecast risk"], "Predicted P95 exceeds a threshold"),
        ("healthy", "Healthy", counts["Healthy"], "Within the current healthy range"),
    ]
    return '<div class="health-grid">' + "".join(
        f'<div class="health-card {kind}"><div class="health-label">{label}</div>'
        f'<div class="health-count">{count}</div><div class="health-help">{help_text}</div></div>'
        for kind, label, count, help_text in cards
    ) + "</div>"


def dashboard_button_updates(uploaded_file=None, forecast_table=None):
    """Return clickable dashboard-card labels with refreshed category counts."""
    groups = resource_health_tables(uploaded_file)
    labels = [
        f"Highly utilized\n{len(groups['Highly utilized'])}\nAt or above a capacity threshold",
        f"Low utilized\n{len(groups['Low utilized'])}\nAll available metrics at or below {LOW_UTILIZATION_THRESHOLD}%",
        f"Forecast risk\n{len(forecast_risk_resources(forecast_table))}\nPredicted P95 exceeds a threshold",
        f"Healthy\n{len(groups['Healthy'])}\nWithin the current healthy range",
    ]
    return tuple(gr.update(value=label) for label in labels)


def top_priorities(uploaded_file=None, forecast_table=None, limit=10):
    """Rank actionable current and forecast findings with concrete recommendations."""
    current = all_hosts_snapshot("Host", "Ascending", uploaded_file)
    rows = []
    metric_config = [
        ("CPU Utilization %", "CPU", CPU_THRESHOLD_DEFAULT, "Review busy processes and add or rebalance compute capacity."),
        ("Memory Utilization %", "Memory", MEM_THRESHOLD_DEFAULT, "Check memory growth and leaks; increase memory or tune workloads."),
        ("Disk/Storage Utilization %", "Disk/Storage", DISK_THRESHOLD_DEFAULT, "Clean or archive data and expand storage before capacity is exhausted."),
    ]
    for _, resource in current.iterrows():
        for column, metric, threshold, recommendation in metric_config:
            value = resource.get(column)
            if pd.notna(value) and value >= threshold:
                rows.append({
                    "_score": 300 + float(value) - threshold,
                    "Priority": "Critical" if value >= 95 else "High",
                    "Host": resource["Host"],
                    "Resource Type": resource["Resource Type"],
                    "Finding": f"Current {metric} utilization is {value:.1f}% (threshold {threshold}%).",
                    "Recommendation": recommendation,
                    "Basis": "Current utilization",
                })

    risks = forecast_risk_resources(forecast_table)
    for _, risk in risks.iterrows():
        threshold = {
            "CPU": CPU_THRESHOLD_DEFAULT,
            "Memory": MEM_THRESHOLD_DEFAULT,
            "Disk/Storage": DISK_THRESHOLD_DEFAULT,
        }[risk["Metric"]]
        recommendation = {
            "CPU": "Schedule compute scaling or workload redistribution before the forecast peak.",
            "Memory": "Plan a memory increase or workload tuning before the forecast peak.",
            "Disk/Storage": "Schedule cleanup, archival, or storage expansion before the forecast peak.",
        }[risk["Metric"]]
        peak_date = pd.Timestamp(risk["Forecast Peak Date"]).strftime("%Y-%m-%d")
        rows.append({
            "_score": 400 + float(risk["Max Forecast P95 %"]) - threshold,
            "Priority": "Critical" if risk["Max Forecast P95 %"] >= 95 else "High",
            "Host": risk["Host"],
            "Resource Type": risk["Resource Type"],
            "Finding": f"{risk['Metric']} P95 is forecast to reach {risk['Max Forecast P95 %']:.1f}% by {peak_date}.",
            "Recommendation": recommendation,
            "Basis": "Granite forecast",
        })

    groups = resource_health_tables(uploaded_file)
    for _, resource in groups["Low utilized"].iterrows():
        available = [
            resource[column] for column, _, _, _ in metric_config if pd.notna(resource.get(column))
        ]
        peak = max(available) if available else 0
        rows.append({
            "_score": 100 - float(peak),
            "Priority": "Optimization",
            "Host": resource["Host"],
            "Resource Type": resource["Resource Type"],
            "Finding": f"All available utilization metrics are at or below {LOW_UTILIZATION_THRESHOLD}%.",
            "Recommendation": "Review for rightsizing, consolidation, scheduling, or retirement to reduce waste.",
            "Basis": "Low utilization",
        })

    columns = ["Priority", "Host", "Resource Type", "Finding", "Recommendation", "Basis"]
    if not rows:
        return pd.DataFrame([{
            "Priority": "Monitor", "Host": "Fleet", "Resource Type": "All",
            "Finding": "No immediate capacity or optimization priorities were detected.",
            "Recommendation": "Continue monitoring and run the fleet forecast for forward-looking risk.",
            "Basis": "Current utilization",
        }], columns=columns)
    return pd.DataFrame(rows).sort_values("_score", ascending=False)[columns].head(limit).reset_index(drop=True)


def current_health_drilldown(category, uploaded_file=None):
    return f"### {category} resources", resource_health_tables(uploaded_file)[category]


def forecast_health_drilldown(forecast_table):
    return "### Forecast-risk resources", forecast_risk_resources(forecast_table)


def open_resource_from_dashboard(table, uploaded_file, evt: gr.SelectData):
    """Open host details when a user selects any row in the drill-down table."""
    if table is None or len(table) == 0:
        raise gr.Error("Select a dashboard category containing resources first.")
    row_index = evt.index[0] if isinstance(evt.index, (tuple, list)) else evt.index
    frame = pd.DataFrame(table)
    if "Host" not in frame.columns or row_index >= len(frame):
        raise gr.Error("The selected row does not contain a resource.")
    selected = str(frame.iloc[row_index]["Host"])
    return (
        gr.update(value=selected),
        snapshot_for_host(selected, uploaded_file),
        historical_plot(selected, "Daily", 60, uploaded_file),
    )


@lru_cache(maxsize=12)
def forecast_cpu_fleet(path_str, model_path):
    """Forecast every eligible infrastructure metric and rank peak utilization."""
    df = load_raw(path_str)
    cpu_map, mem_map, disk_map = get_maps(df)
    resource_types = get_resource_types(df)
    rows = []
    skipped = 0
    total = 0

    for metric_name, mapping in [
        ("CPU", cpu_map), ("Memory", mem_map), ("Disk/Storage", disk_map)
    ]:
        for host in sorted(mapping, key=str.lower):
            total += 1
            series = metric_series(df, mapping[host])
            latest, _ = latest_value(series)
            try:
                forecast, _, error = forecast_metric(series, model_path)
            except Exception:
                skipped += 1
                continue
            if error or forecast is None:
                skipped += 1
                continue

            peak_row = forecast.loc[forecast["p95"].idxmax()]
            rows.append({
                "Host": host,
                "Resource Type": resource_types.get(host, "Unspecified"),
                "Metric": metric_name,
                "Current Utilization %": latest,
                "Max Forecast Avg %": float(forecast["avg"].max()),
                "Max Forecast P95 %": float(peak_row["p95"]),
                "Forecast Peak Date": pd.Timestamp(peak_row["timestamp"]),
            })

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(
            "Max Forecast P95 %", ascending=False
        ).reset_index(drop=True)
    return result, skipped, total


def top_forecast_cpu(uploaded_file=None, model_path=MODEL_PATH, limit=5):
    table, skipped, total = forecast_cpu_fleet(
        dataset_path(uploaded_file), model_path
    )
    if table.empty:
        message = (
            "**No infrastructure forecasts are available.** Metrics need approximately "
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
        f"Forecasted **{len(table)} of {total}** host metrics; "
        f"**{skipped}** lacked sufficient continuous history or could not be forecast. "
        "Ranked by maximum forecast P95 across CPU, memory, and disk/storage."
    )
    return message, output


def run_fleet_forecast(uploaded_file=None, model_path=MODEL_PATH, limit=5):
    """Refresh forecast results, dashboard risk count, and drill-down state."""
    full_table, skipped, total = forecast_cpu_fleet(dataset_path(uploaded_file), model_path)
    if full_table.empty:
        message = (
            "**No infrastructure forecasts are available.** Metrics need approximately "
            f"{CONTEXT_LENGTH} continuous daily observations."
        )
        return message, full_table, dashboard_html(uploaded_file), top_priorities(uploaded_file), full_table
    output = full_table.head(limit).copy()
    numeric = output.select_dtypes(include="number").columns
    output[numeric] = output[numeric].round(2)
    output["Forecast Peak Date"] = pd.to_datetime(output["Forecast Peak Date"]).dt.strftime("%Y-%m-%d")
    message = (
        f"Forecasted **{len(full_table)} of {total}** host metrics; **{skipped}** skipped. "
        "The forecast-risk dashboard card is now updated."
    )
    return message, output, dashboard_html(uploaded_file, full_table), top_priorities(uploaded_file, full_table), full_table


def historical_plot(host, aggregation="Daily", periods=60, uploaded_file=None):
    df = load_raw(dataset_path(uploaded_file))
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


def host_overview(host, aggregation, periods, uploaded_file=None):
    return (
        snapshot_for_host(host, uploaded_file),
        historical_plot(host, aggregation, periods, uploaded_file),
    )


def trend_plot(host, aggregation, periods, uploaded_file=None):
    return historical_plot(host, aggregation, periods, uploaded_file)


def first_crossing(fc, col, threshold):
    hits = fc.loc[fc[col] >= threshold, "timestamp"]
    return None if hits.empty else pd.Timestamp(hits.iloc[0])


def run_forecast(
    host, cpu_threshold, mem_threshold, disk_threshold,
    uploaded_file=None, model_path=MODEL_PATH,
):
    df = load_raw(dataset_path(uploaded_file))
    cpu_map, mem_map, disk_map = get_maps(df)

    forecasts = {}
    histories = {}
    notes = []

    metric_specs = [
        ("CPU", cpu_map, cpu_threshold),
        ("Memory", mem_map, mem_threshold),
        ("Disk/Storage", disk_map, disk_threshold),
    ]

    for label, mapping, threshold in metric_specs:
        if host not in mapping:
            notes.append(f"- **{label}:** not present in this infrastructure dataset for this host.")
            continue

        series = metric_series(df, mapping[host])
        fc, daily, err = forecast_metric(series, model_path)
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
        ("Disk/Storage", disk_threshold),
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
        + f"Model: `{model_path}`\n\n"
        + "\n".join(notes)
        + "\n\n"
        + "**CPU idle forecast is derived as `100 − forecast CPU average`.**"
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
    ax.set_title(f"{host} — {model_path.rsplit('/', 1)[-1]} forecast")
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


def load_dataset(uploaded_file):
    """Validate a selected dataset and refresh every dataset-level view."""
    path = dataset_path(uploaded_file)
    try:
        df = load_raw(path)
    except Exception as exc:
        raise gr.Error(f"Could not load dataset: {exc}") from exc
    hosts = all_hosts(df)
    if not hosts:
        raise gr.Error(
            "No hosts were found. Expected columns beginning with "
            "'CPU usage % - ', 'Memory used % - ', or 'Disk used % - '."
        )
    selected = "HDS033008_LAB-AG" if "HDS033008_LAB-AG" in hosts else hosts[0]
    source = Path(path).name
    status = (
        f"Loaded **{source}** — **{len(df):,} timestamp rows**, "
        f"**{len(hosts)} hosts**. Schema detected automatically."
    )
    return (
        status,
        normalization_summary(df),
        normalized_preview(df),
        dashboard_html(uploaded_file),
        top_priorities(uploaded_file),
        pd.DataFrame(),
        "### Select a dashboard category to view its resources",
        pd.DataFrame(),
        gr.update(choices=hosts, value=selected),
        top_current_cpu(uploaded_file),
        all_hosts_snapshot("Host", "Ascending", uploaded_file),
        snapshot_for_host(selected, uploaded_file),
        historical_plot(selected, "Daily", 60, uploaded_file),
        "Run once to rank every host with sufficient CPU history.",
        pd.DataFrame(),
    )


APP_CSS = """
.health-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:8px 0 12px; }
.health-card { border-radius:14px; padding:18px; color:#fff; min-height:128px; box-shadow:0 5px 16px rgba(0,0,0,.12); }
.health-card.high { background:linear-gradient(135deg,#991b1b,#dc2626); }
.health-card.low { background:linear-gradient(135deg,#92400e,#f59e0b); }
.health-card.risk { background:linear-gradient(135deg,#6b21a8,#a855f7); }
.health-card.healthy { background:linear-gradient(135deg,#166534,#22c55e); }
.health-label { font-size:16px; font-weight:700; }
.health-count { font-size:38px; font-weight:800; line-height:1.2; }
.health-help { font-size:12px; opacity:.9; }
.dashboard-card button { min-height:128px; border:0 !important; border-radius:14px !important; color:#fff !important; font-size:16px !important; font-weight:750 !important; white-space:pre-line !important; line-height:1.55 !important; box-shadow:0 5px 16px rgba(0,0,0,.12); }
.dashboard-card button:hover { filter:brightness(1.08); transform:translateY(-1px); }
.dashboard-action button { color:#fff !important; font-weight:750 !important; border:0 !important; }
.dashboard-high button { background:linear-gradient(135deg,#991b1b,#dc2626) !important; }
.dashboard-low button { background:linear-gradient(135deg,#92400e,#f59e0b) !important; }
.dashboard-risk button { background:linear-gradient(135deg,#6b21a8,#a855f7) !important; }
.dashboard-healthy button { background:linear-gradient(135deg,#166534,#22c55e) !important; }
.preview-link > .label-wrap { color:#2563eb !important; text-decoration:underline; cursor:pointer; }
@media (max-width:900px) { .health-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
"""


with gr.Blocks(title="Infrastructure Capacity Predictor", css=APP_CSS) as demo:
    gr.Markdown(
        """
# Infrastructure Capacity Predictor
**Infrastructure monitoring metrics → IBM Granite TTM → resource utilization, trends, and 30-day forecasts**

Review compute, storage, backup, and network resources, then select one to see
available CPU, memory, and disk/storage trends and forecasts.
"""
    )

    gr.Markdown("## Data and model")
    with gr.Row():
        dataset_upload = gr.File(
            label="Upload infrastructure metrics (CSV or Excel)",
            file_types=[".csv", ".xlsx", ".xls"],
            type="filepath",
        )
        model_choice = gr.Dropdown(
            choices=MODEL_CHOICES,
            value=MODEL_PATH,
            label="Granite forecasting model",
            interactive=True,
        )
    dataset_status = gr.Markdown(
        f"Using default dataset **{Path(DATA_FILE).name}** — "
        f"**{len(_initial_df):,} rows**, **{len(_HOSTS)} hosts**."
    )
    normalization_status = gr.Markdown(normalization_summary(_initial_df))
    with gr.Accordion(
        "View canonical data preview",
        open=False,
        elem_classes=["preview-link"],
    ):
        normalization_preview = gr.Dataframe(
            value=normalized_preview(_initial_df),
            interactive=False,
            label="Canonical data preview (first 10 rows)",
        )

    gr.Markdown("## Fleet health dashboard")
    health_dashboard = gr.HTML(dashboard_html())
    with gr.Row():
        high_button = gr.Button("Drill down: highly utilized", elem_classes=["dashboard-action", "dashboard-high"])
        low_button = gr.Button("Drill down: low utilized", elem_classes=["dashboard-action", "dashboard-low"])
        risk_button = gr.Button("Drill down: forecast risk", elem_classes=["dashboard-action", "dashboard-risk"])
        healthy_button = gr.Button("Drill down: healthy", elem_classes=["dashboard-action", "dashboard-healthy"])
    gr.Markdown("### Top priorities and recommendations")
    priority_table = gr.Dataframe(
        value=top_priorities(),
        interactive=False,
        label="Ranked actions — select a row to open the resource",
    )
    forecast_state = gr.State(pd.DataFrame())
    drilldown_title = gr.Markdown("### Select a dashboard category to view its resources")
    dashboard_drilldown = gr.Dataframe(interactive=False, label="Dashboard drill-down")

    high_button.click(
        fn=lambda uploaded: current_health_drilldown("Highly utilized", uploaded),
        inputs=dataset_upload,
        outputs=[drilldown_title, dashboard_drilldown],
    )
    low_button.click(
        fn=lambda uploaded: current_health_drilldown("Low utilized", uploaded),
        inputs=dataset_upload,
        outputs=[drilldown_title, dashboard_drilldown],
    )
    healthy_button.click(
        fn=lambda uploaded: current_health_drilldown("Healthy", uploaded),
        inputs=dataset_upload,
        outputs=[drilldown_title, dashboard_drilldown],
    )
    risk_button.click(
        fn=forecast_health_drilldown,
        inputs=forecast_state,
        outputs=[drilldown_title, dashboard_drilldown],
    )

    gr.Markdown("## Infrastructure utilization summary")
    with gr.Row():
        with gr.Column():
            gr.Markdown("### Top 5 resources — current utilization")
            current_cpu_summary = gr.Dataframe(
                value=top_current_cpu(),
                interactive=False,
                label="Current CPU, memory, and disk/storage utilization",
            )
        with gr.Column():
            gr.Markdown("### Top 5 forecast utilization risks")
            fleet_forecast_button = gr.Button(
                "Run infrastructure forecast",
                variant="primary",
            )
            fleet_forecast_status = gr.Markdown(
                "Run once to rank every host with sufficient CPU history."
            )
            forecast_cpu_summary = gr.Dataframe(
                interactive=False,
                label="Highest forecast P95 across available metrics",
            )

    fleet_forecast_button.click(
        fn=run_fleet_forecast,
        inputs=[dataset_upload, model_choice],
        outputs=[
            fleet_forecast_status, forecast_cpu_summary, health_dashboard,
            priority_table, forecast_state,
        ],
    )

    gr.Markdown("## All hosts — latest utilization")
    with gr.Row():
        overview_sort = gr.Dropdown(
            choices=[
                "Host", "Resource Type", "CPU Utilization %", "CPU Idle %",
                "Memory Utilization %", "Disk/Storage Utilization %", "Latest Sample",
            ],
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
        inputs=[overview_sort, overview_order, dataset_upload],
        outputs=overview_table,
    )
    overview_order.change(
        fn=all_hosts_snapshot,
        inputs=[overview_sort, overview_order, dataset_upload],
        outputs=overview_table,
    )

    gr.Markdown("## Host details")

    host = gr.Dropdown(
        choices=_HOSTS,
        value=_DEFAULT_HOST,
        label="Infrastructure resource",
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
        label="Latest infrastructure monitoring values",
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

    dashboard_drilldown.select(
        fn=open_resource_from_dashboard,
        inputs=[dashboard_drilldown, dataset_upload],
        outputs=[host, snapshot, history_chart],
    )
    priority_table.select(
        fn=open_resource_from_dashboard,
        inputs=[priority_table, dataset_upload],
        outputs=[host, snapshot, history_chart],
    )

    run_button = gr.Button("Run 30-day Granite forecast", variant="primary")

    gr.Markdown("## Forecast")
    summary = gr.Markdown()
    forecast_chart = gr.Plot()
    forecast_table = gr.Dataframe(interactive=False, label="30-day forecast")

    host.change(
        fn=host_overview,
        inputs=[host, aggregation, periods, dataset_upload],
        outputs=[snapshot, history_chart],
    )
    aggregation.change(
        fn=trend_plot,
        inputs=[host, aggregation, periods, dataset_upload],
        outputs=history_chart,
    )
    periods.change(
        fn=trend_plot,
        inputs=[host, aggregation, periods, dataset_upload],
        outputs=history_chart,
    )

    run_button.click(
        fn=run_forecast,
        inputs=[
            host, cpu_threshold, mem_threshold, disk_threshold,
            dataset_upload, model_choice,
        ],
        outputs=[summary, forecast_chart, forecast_table],
    )

    dataset_upload.change(
        fn=load_dataset,
        inputs=dataset_upload,
        outputs=[
            dataset_status,
            normalization_status,
            normalization_preview,
            health_dashboard,
            priority_table,
            forecast_state,
            drilldown_title,
            dashboard_drilldown,
            host,
            current_cpu_summary,
            overview_table,
            snapshot,
            history_chart,
            fleet_forecast_status,
            forecast_cpu_summary,
        ],
    )


if __name__ == "__main__":
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        show_error=True,
    )
