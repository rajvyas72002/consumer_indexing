"""
app.py -- Enhanced Streamlit dashboard for the DT <-> Consumer matching engine.

Thin UI layer over engine.py -- same load_state / ingest_new_day /
compute_matches / save_state functions used by run_tn.py.

RUN:
    pip install streamlit
    streamlit run app.py
"""

import os
import tempfile
from datetime import datetime

import pandas as pd
import streamlit as st

import engine

st.set_page_config(
    page_title="Grid Topology Tracker",
    layout="wide",
    page_icon="⚡",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Theme state
# ---------------------------------------------------------------------------
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = True


def toggle_theme():
    st.session_state.dark_mode = not st.session_state.dark_mode


is_dark = st.session_state.dark_mode

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
if is_dark:
    BG          = "#0f1318"
    PANEL       = "#1a1f26"
    PANEL_HOVER = "#1f252e"
    BORDER      = "#2a313b"
    TEXT_PRI    = "#e8ecf0"
    TEXT_SEC    = "#8b94a0"
    TEXT_MUT    = "#5a6270"
    ACCENT      = "#4a9d6f"       # teal-green for HIGH confidence
    ACCENT_MED  = "#cba65a"       # amber for MEDIUM
    ACCENT_LOW  = "#c0594a"       # red for LOW / alerts
    ACCENT_BLUE = "#4a7fa5"       # steel-blue for info
    SIDEBAR_BG  = "#13171c"
    INPUT_BG    = "#1f252e"
    BTN_PRI_BG  = "#4a9d6f"
    BTN_PRI_FG  = "#ffffff"
    BTN_SEC_BG  = "#2a313b"
    BTN_SEC_FG  = "#e8ecf0"
    CHART_GRID  = "#2a313b"
    BADGE_HIGH  = ("#1a3327", "#4a9d6f")
    BADGE_MED   = ("#2e2510", "#cba65a")
    BADGE_LOW   = ("#2e1410", "#c0594a")
    ICON_THEME  = "☀️"
    THEME_LABEL = "Light mode"
else:
    BG          = "#f0f2f5"
    PANEL       = "#ffffff"
    PANEL_HOVER = "#f7f9fb"
    BORDER      = "#d1d8e0"
    TEXT_PRI    = "#1a1f26"
    TEXT_SEC    = "#4a5568"
    TEXT_MUT    = "#8b94a0"
    ACCENT      = "#2d7a52"
    ACCENT_MED  = "#a07a20"
    ACCENT_LOW  = "#b03a2e"
    ACCENT_BLUE = "#2c5f82"
    SIDEBAR_BG  = "#e4e8ee"
    INPUT_BG    = "#ffffff"
    BTN_PRI_BG  = "#2d7a52"
    BTN_PRI_FG  = "#ffffff"
    BTN_SEC_BG  = "#d1d8e0"
    BTN_SEC_FG  = "#1a1f26"
    CHART_GRID  = "#d1d8e0"
    BADGE_HIGH  = ("#dcf5ea", "#2d7a52")
    BADGE_MED   = ("#fef3d8", "#a07a20")
    BADGE_LOW   = ("#fde8e6", "#b03a2e")
    ICON_THEME  = "🌙"
    THEME_LABEL = "Dark mode"

# ---------------------------------------------------------------------------
# Injected CSS -- scoped to current theme
# ---------------------------------------------------------------------------
st.markdown(f"""
<style>
    /* ---- Global ---- */
    html, body, [class*="css"], .stApp {{
        background-color: {BG} !important;
        color: {TEXT_PRI} !important;
        font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
    }}
    .block-container {{
        padding: 1.5rem 2rem 2rem 2rem !important;
        max-width: 1600px !important;
    }}

    /* ---- Sidebar ---- */
    [data-testid="stSidebar"] > div:first-child {{
        background-color: {SIDEBAR_BG} !important;
        border-right: 1px solid {BORDER};
        padding: 1rem 0.75rem;
    }}
    [data-testid="stSidebar"] * {{
        color: {TEXT_PRI} !important;
    }}
    [data-testid="stSidebarNav"] {{
        display: none;
    }}

    /* ---- Selectbox & text inputs ---- */
    .stSelectbox > div > div,
    .stTextInput > div > div > input,
    .stFileUploader > div {{
        background-color: {INPUT_BG} !important;
        border: 1px solid {BORDER} !important;
        border-radius: 8px !important;
        color: {TEXT_PRI} !important;
    }}
    .stSelectbox svg {{ fill: {TEXT_SEC} !important; }}
    .stSelectbox [data-baseweb="select"] * {{ color: {TEXT_PRI} !important; }}
    .stSelectbox [data-baseweb="select"] {{ background-color: {INPUT_BG} !important; }}
    [data-baseweb="popover"] > div,
    [data-baseweb="menu"] {{
        background-color: {PANEL} !important;
        border: 1px solid {BORDER} !important;
        color: {TEXT_PRI} !important;
    }}
    [data-baseweb="option"]:hover {{ background-color: {PANEL_HOVER} !important; }}

    /* ---- Metric cards ---- */
    div[data-testid="stMetric"] {{
        background-color: {PANEL};
        border: 1px solid {BORDER};
        border-radius: 12px;
        padding: 16px 20px;
        transition: box-shadow 0.2s;
    }}
    div[data-testid="stMetric"]:hover {{
        box-shadow: 0 4px 16px rgba(0,0,0,0.15);
    }}
    [data-testid="stMetricLabel"] {{
        color: {TEXT_SEC} !important;
        font-size: 0.8rem !important;
        font-weight: 600 !important;
        letter-spacing: 0.05em !important;
        text-transform: uppercase;
    }}
    [data-testid="stMetricValue"] {{
        color: {TEXT_PRI} !important;
        font-family: 'JetBrains Mono', 'Fira Code', monospace !important;
        font-size: 1.8rem !important;
        font-weight: 700 !important;
    }}
    [data-testid="stMetricDelta"] {{
        font-size: 0.85rem !important;
        font-weight: 600 !important;
    }}

    /* ---- Buttons ---- */
    .stButton > button[kind="primary"],
    .stButton > button[data-testid="baseButton-primary"] {{
        background-color: {BTN_PRI_BG} !important;
        color: {BTN_PRI_FG} !important;
        border: none !important;
        border-radius: 8px !important;
        font-weight: 600;
        padding: 0.5rem 1.25rem;
        transition: opacity 0.15s;
    }}
    .stButton > button[kind="primary"]:hover {{
        opacity: 0.88 !important;
    }}
    .stButton > button[kind="secondary"],
    .stButton > button[data-testid="baseButton-secondary"] {{
        background-color: {BTN_SEC_BG} !important;
        color: {BTN_SEC_FG} !important;
        border: 1px solid {BORDER} !important;
        border-radius: 8px !important;
        font-weight: 500;
    }}
    .stButton > button {{
        transition: all 0.15s !important;
    }}
    .stButton > button:hover {{ filter: brightness(1.1) !important; }}

    /* ---- Download button ---- */
    .stDownloadButton > button {{
        background-color: {BTN_SEC_BG} !important;
        color: {BTN_SEC_FG} !important;
        border: 1px solid {BORDER} !important;
        border-radius: 8px !important;
        font-weight: 500;
    }}

    /* ---- Dataframe ---- */
    .stDataFrame {{
        border: 1px solid {BORDER} !important;
        border-radius: 10px !important;
        overflow: hidden;
    }}
    .stDataFrame iframe {{
        border-radius: 10px;
    }}

    /* ---- Chart labels ---- */
    .stVegaLiteChart text,
    .stPlotlyChart text {{
        fill: {TEXT_PRI} !important;
        color: {TEXT_PRI} !important;
    }}
    .stLineChart text {{ fill: {TEXT_PRI} !important; }}

    /* ---- Info / warning boxes ---- */
    .stAlert {{
        border-radius: 10px !important;
        border: 1px solid {BORDER} !important;
    }}
    [data-testid="stInfo"] {{
        background-color: {"#0f2233" if is_dark else "#e8f4fd"} !important;
        color: {TEXT_PRI} !important;
    }}
    [data-testid="stWarning"] {{
        background-color: {"#2e250e" if is_dark else "#fdf6e3"} !important;
        color: {TEXT_PRI} !important;
    }}
    [data-testid="stSuccess"] {{
        background-color: {"#0e2619" if is_dark else "#e9f7ef"} !important;
        color: {TEXT_PRI} !important;
    }}

    /* ---- Tabs ---- */
    .stTabs [data-baseweb="tab-list"] {{
        background-color: transparent !important;
        border-bottom: 1px solid {BORDER};
        gap: 8px;
    }}
    .stTabs [data-baseweb="tab"] {{
        background-color: transparent !important;
        color: {TEXT_SEC} !important;
        border-radius: 6px 6px 0 0 !important;
        font-weight: 500;
        padding: 8px 16px !important;
    }}
    .stTabs [aria-selected="true"] {{
        color: {TEXT_PRI} !important;
        background-color: {PANEL} !important;
        border-bottom: 2px solid {ACCENT} !important;
    }}
    .stTabs [data-baseweb="tab-panel"] {{
        background-color: transparent !important;
        padding-top: 1rem !important;
    }}

    /* ---- Spinner ---- */
    .stSpinner > div {{ border-top-color: {ACCENT} !important; }}

    /* ---- Dividers ---- */
    hr {{ border-color: {BORDER} !important; opacity: 0.5; }}

    /* ---- Caption & labels ---- */
    .stCaption, .stCaption * {{
        color: {TEXT_SEC} !important;
    }}
    label, .stTextInput label, .stSelectbox label {{
        color: {TEXT_SEC} !important;
        font-weight: 500 !important;
    }}

    /* ---- Custom components ---- */
    .gt-header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding-bottom: 1rem;
        border-bottom: 1px solid {BORDER};
        margin-bottom: 1.25rem;
    }}
    .gt-title {{
        font-size: 1.5rem;
        font-weight: 700;
        color: {TEXT_PRI};
        letter-spacing: -0.02em;
    }}
    .gt-subtitle {{
        font-size: 0.85rem;
        color: {TEXT_SEC};
        margin-top: 2px;
        font-family: 'JetBrains Mono', monospace;
    }}
    .gt-tn-badge {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 6px 14px;
        font-family: monospace;
        font-size: 0.9rem;
        color: {ACCENT};
        font-weight: 600;
    }}
    .change-alert {{
        background-color: {"#2e1a14" if is_dark else "#fff0ed"} !important;
        border: 1px solid {ACCENT_LOW};
        border-left: 4px solid {ACCENT_LOW};
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 10px;
        font-size: 0.9rem;
        line-height: 1.5;
        color: {"#f3ece8" if is_dark else "#2a1410"} !important;
    }}
    .change-alert span, .change-alert div {{ color: {"#f3ece8" if is_dark else "#2a1410"}; }}
    .change-alert b {{ color: {"#ff9a85" if is_dark else "#a03028"} !important; font-weight: 700; }}
    .gt-section-label {{
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: {TEXT_MUT};
        margin-bottom: 0.5rem;
        margin-top: 1.25rem;
    }}
    .gt-stat-row {{
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        margin-bottom: 0.5rem;
    }}
    .gt-chip {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-radius: 20px;
        padding: 3px 12px;
        font-size: 0.8rem;
        color: {TEXT_SEC};
    }}
    .badge-high {{
        display: inline-block;
        background: {BADGE_HIGH[0]};
        color: {BADGE_HIGH[1]};
        border: 1px solid {BADGE_HIGH[1]};
        border-radius: 4px;
        padding: 1px 7px;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.04em;
    }}
    .badge-med {{
        display: inline-block;
        background: {BADGE_MED[0]};
        color: {BADGE_MED[1]};
        border: 1px solid {BADGE_MED[1]};
        border-radius: 4px;
        padding: 1px 7px;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.04em;
    }}
    .badge-low {{
        display: inline-block;
        background: {BADGE_LOW[0]};
        color: {BADGE_LOW[1]};
        border: 1px solid {BADGE_LOW[1]};
        border-radius: 4px;
        padding: 1px 7px;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.04em;
    }}
    .sidebar-section {{
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: {TEXT_MUT};
        margin: 1rem 0 0.4rem 0;
    }}
    .summary-card {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 14px 16px;
        margin-bottom: 10px;
    }}
    .summary-card-title {{
        font-size: 0.8rem;
        font-weight: 600;
        color: {TEXT_SEC};
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 6px;
    }}
    .summary-card-value {{
        font-size: 1.5rem;
        font-weight: 700;
        font-family: monospace;
        color: {TEXT_PRI};
    }}
    .info-row {{
        display: flex;
        justify-content: space-between;
        padding: 5px 0;
        border-bottom: 1px solid {BORDER};
        font-size: 0.85rem;
    }}
    .info-row:last-child {{ border-bottom: none; }}
    .info-label {{ color: {TEXT_SEC}; }}
    .info-value {{ color: {TEXT_PRI}; font-weight: 500; font-family: monospace; }}
</style>
""", unsafe_allow_html=True)

TN_FOLDER_BASE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_raw_consumer_csv(uploaded_file):
    """Read a consumer CSV (skiprows=4) and return a clean DataFrame."""
    df = pd.read_csv(uploaded_file, skiprows=4)
    return df


def build_dt_summary_from_state(state, matches_df):
    """
    Build the DT Explorer summary and consumer-detail lookup entirely from the
    engine's accumulated state + matches_df -- no extra CSV upload required.
    Groups consumers by their PREDICTED (matched) DT, enriched with dt_meta.
    """
    if matches_df is None or matches_df.empty:
        return pd.DataFrame(), {}

    dt_meta = state.get("dt_meta", {})
    consumer_meta = state.get("consumer_meta", {})
    dt_feeder = state.get("dt_feeder", {})

    rows = []
    for _, m in matches_df.iterrows():
        cons = str(m["consumer_id"])
        dt   = str(m["predicted_dt"])
        meta = consumer_meta.get(cons, {})
        dm   = dt_meta.get(dt, {})
        rows.append({
            "consumer_number":   cons,
            "consumer_name":     meta.get("name", ""),
            "consumer_category": meta.get("consumer_category", ""),
            "meter_id":          meta.get("meter_id", ""),
            "dt_code":           dt,
            "dt_name":           dm.get("dt_name", dt),
            "feeder_name":       dm.get("feeder_name", dt_feeder.get(dt, "")),
            "feeder_code":       dm.get("feeder_code", ""),
            "substation_name":   dm.get("substation_name", ""),
            "confidence":        m.get("confidence", ""),
        })
    cons_df = pd.DataFrame(rows)

    summary = (
        cons_df.groupby("dt_code", as_index=False)
        .agg(
            dt_name=("dt_name", "first"),
            feeder_name=("feeder_name", "first"),
            feeder_code=("feeder_code", "first"),
            substation_name=("substation_name", "first"),
            consumer_count=("consumer_number", "nunique"),
        )
        .sort_values("consumer_count", ascending=False)
        .reset_index(drop=True)
    )

    detail_cols = ["consumer_number", "consumer_name", "consumer_category",
                   "meter_id", "feeder_name", "dt_name", "dt_code", "confidence"]
    detail_lookup = {
        dt: grp[detail_cols].reset_index(drop=True)
        for dt, grp in cons_df.groupby("dt_code")
    }
    return summary, detail_lookup


def build_dt_summary(consumer_df):
    """
    From a consumer CSV DataFrame, build a per-DT summary table and a lookup
    dict mapping dt_code -> list of unique consumer dicts.
    """
    # one row per unique consumer_number (there are many event rows per consumer)
    cons_unique = consumer_df.drop_duplicates(subset=["consumer_number"])

    summary = (
        cons_unique.groupby("dt_code", as_index=False)
        .agg(
            dt_name=("dt_name", "first"),
            feeder_name=("feeder_name", "first"),
            feeder_code=("feeder_code", "first"),
            substation_name=("substation_name", "first"),
            consumer_count=("consumer_number", "nunique"),
        )
        .sort_values("consumer_count", ascending=False)
        .reset_index(drop=True)
    )

    # consumer detail lookup keyed by dt_code
    detail_cols = [
        "consumer_number", "consumer_name", "consumer_category",
        "meter_id", "feeder_name", "dt_name", "dt_code",
    ]
    avail = [c for c in detail_cols if c in cons_unique.columns]
    detail_lookup = {
        dt: grp[avail].reset_index(drop=True)
        for dt, grp in cons_unique[avail].groupby("dt_code")
    }

    return summary, detail_lookup

def get_tn_folder(tn_name):
    folder = os.path.join(TN_FOLDER_BASE, tn_name)
    os.makedirs(os.path.join(folder, "raw"), exist_ok=True)
    os.makedirs(os.path.join(folder, "state"), exist_ok=True)
    return folder


def save_uploaded_file_to_temp(uploaded_file):
    suffix = ".csv"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(uploaded_file.getvalue())
    tmp.close()
    return tmp.name


def sniff_kind(uploaded_file):
    text = uploaded_file.getvalue().decode("utf-8", errors="ignore")
    lines = text.splitlines()
    if len(lines) < 5:
        return None
    header = lines[4]
    if "consumer_number" in header:
        return "consumer"
    if "dt_code" in header:
        return "dt"
    return None


def confidence_badge(conf):
    if conf == "HIGH":
        return f'<span class="badge-high">HIGH</span>'
    elif conf == "MEDIUM":
        return f'<span class="badge-med">MED</span>'
    else:
        return f'<span class="badge-low">LOW</span>'


def state_file_size(tn_folder):
    p = os.path.join(tn_folder, "state", "evidence.json")
    return os.path.getsize(p) / 1e6 if os.path.exists(p) else 0.0


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    # Theme toggle at top
    col_logo, col_toggle = st.columns([3, 2])
    with col_logo:
        st.markdown(f"<div style='font-weight:700; font-size:1.05rem; color:{TEXT_PRI}; padding-top:4px;'>⚡ Grid Tracker</div>", unsafe_allow_html=True)
    with col_toggle:
        if st.button(f"{ICON_THEME} {THEME_LABEL}", key="theme_toggle", width='stretch'):
            toggle_theme()
            st.rerun()

    st.markdown(f"<div class='sidebar-section'>Network</div>", unsafe_allow_html=True)
    tn_name = st.selectbox("Transformer Network", ["tn_32", "tn_33", "tn_34"], label_visibility="collapsed")
    tn_folder = get_tn_folder(tn_name)

    # Quick TN stats while sidebar is loading
    _quick_state = engine.load_state(tn_folder)
    _days = len(_quick_state["days_ingested"])
    _frozen = len(_quick_state["frozen_consumers"])
    _pairs = len(_quick_state["pair_evidence"])
    st.markdown(
        f"<div class='gt-stat-row'>"
        f"<span class='gt-chip'>📅 {_days} days</span>"
        f"<span class='gt-chip'>🔒 {_frozen:,} frozen</span>"
        f"<span class='gt-chip'>📁 {state_file_size(tn_folder):.1f} MB</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown(f"<div class='sidebar-section'>Ingest new day(s)</div>", unsafe_allow_html=True)
    st.caption("Upload any number of consumer + DT CSV pairs at once — one pair per day.")
    uploaded_files = st.file_uploader(
        "Consumer CSVs + DT CSVs",
        type=["csv"],
        accept_multiple_files=True,
        key=f"uploader_{tn_name}",
        label_visibility="collapsed",
    )

    if uploaded_files:
        consumer_files, dt_files, unknown_files = [], [], []
        for f in uploaded_files:
            kind = sniff_kind(f)
            if kind == "consumer":
                consumer_files.append(f)
            elif kind == "dt":
                dt_files.append(f)
            else:
                unknown_files.append(f)

        # sort both lists by filename so day-order is consistent
        consumer_files.sort(key=lambda f: f.name)
        dt_files.sort(key=lambda f: f.name)

        if unknown_files:
            st.warning(f"⚠ Could not identify {len(unknown_files)} file(s): "
                       + ", ".join(f"`{f.name}`" for f in unknown_files))

        n_pairs = min(len(consumer_files), len(dt_files))
        n_unpaired = abs(len(consumer_files) - len(dt_files))

        if n_pairs == 0:
            missing = []
            if not consumer_files:
                missing.append("consumer file(s)")
            if not dt_files:
                missing.append("DT file(s)")
            st.info(f"Still waiting for: **{', '.join(missing)}**. Both types are required.")
        else:
            # show detected pairs
            pairs = list(zip(consumer_files[:n_pairs], dt_files[:n_pairs]))
            for i, (cf, df) in enumerate(pairs):
                st.markdown(
                    f"<div style='font-size:0.78rem; color:{TEXT_SEC}; "
                    f"padding:3px 0; font-family:monospace;'>"
                    f"<span style='color:{ACCENT}'>Day {i+1}</span> &nbsp;"
                    f"<span style='color:{TEXT_PRI}'>{cf.name}</span> + "
                    f"<span style='color:{TEXT_PRI}'>{df.name}</span></div>",
                    unsafe_allow_html=True,
                )
            if n_unpaired:
                st.warning(f"⚠ {n_unpaired} unmatched file(s) will be skipped "
                           f"(need equal numbers of consumer and DT files).")

            btn_label = f"⬆ Ingest {n_pairs} day{'s' if n_pairs > 1 else ''}"
            if st.button(btn_label, type="primary", width='stretch'):
                state = engine.load_state(tn_folder)
                days_before = len(state["days_ingested"])
                ingested, skipped = 0, 0

                with st.spinner(f"Processing {n_pairs} day(s)…"):
                    for cf, df in pairs:
                        consumer_path = save_uploaded_file_to_temp(cf)
                        dt_path = save_uploaded_file_to_temp(df)
                        days_before_this = len(state["days_ingested"])
                        try:
                            state = engine.ingest_new_day(state, consumer_path, dt_path)
                        finally:
                            os.unlink(consumer_path)
                            os.unlink(dt_path)
                        if len(state["days_ingested"]) > days_before_this:
                            ingested += 1
                        else:
                            skipped += 1

                engine.save_state(state, tn_folder)
                days_after = len(state["days_ingested"])

                msg = f"✓ Ingested {ingested} new day(s). Total days: {days_after}."
                if skipped:
                    msg += f" ({skipped} already seen — skipped.)"
                st.success(msg)
                st.rerun()

    st.markdown("---")
    st.markdown(f"<div class='sidebar-section'>Master roster (for coverage)</div>", unsafe_allow_html=True)
    st.caption("Upload ONCE — the full DT/consumer installation list for this TN. "
               "Used only to check how much of the real network your outage data covers, "
               "and to show installer approval/MDM status per consumer.")
    master_dt_upload = st.file_uploader("DT installation CSV", type=["csv"], key=f"master_dt_{tn_name}")
    master_cons_upload = st.file_uploader("Consumer installation CSV", type=["csv"], key=f"master_cons_{tn_name}")

    master_dt_path = os.path.join(tn_folder, "state", "master_dt.csv")
    master_cons_path = os.path.join(tn_folder, "state", "master_consumer.csv")

    if master_dt_upload is not None:
        with open(master_dt_path, "wb") as f:
            f.write(master_dt_upload.getvalue())
        st.success("DT roster saved.")
    if master_cons_upload is not None:
        with open(master_cons_path, "wb") as f:
            f.write(master_cons_upload.getvalue())
        st.success("Consumer roster saved.")

    st.markdown("---")
    st.markdown(f"<div class='sidebar-section'>Danger zone</div>", unsafe_allow_html=True)
    if st.button("🗑 Reset this TN's data", width='stretch'):
        st.session_state[f"confirm_reset_{tn_name}"] = True

    if st.session_state.get(f"confirm_reset_{tn_name}"):
        st.warning(f"This permanently erases all evidence for **{tn_name}**. Are you sure?")
        c1, c2 = st.columns(2)
        if c1.button("✓ Confirm", type="primary", width='stretch'):
            state_path = os.path.join(tn_folder, "state", "evidence.json")
            if os.path.exists(state_path):
                os.remove(state_path)
            st.session_state[f"confirm_reset_{tn_name}"] = False
            st.rerun()
        if c2.button("✕ Cancel", width='stretch'):
            st.session_state[f"confirm_reset_{tn_name}"] = False
            st.rerun()

# ---------------------------------------------------------------------------
# Main panel -- load state
# ---------------------------------------------------------------------------
state = engine.load_state(tn_folder)

# Header
st.markdown(
    f"""<div class="gt-header">
        <div>
            <div class="gt-title">Grid Topology Tracker</div>
            <div class="gt-subtitle">DT ↔ Consumer Matching Engine · Evidence Accumulation Mode</div>
        </div>
        <div class="gt-tn-badge">{tn_name}</div>
    </div>""",
    unsafe_allow_html=True,
)

if not state["days_ingested"]:
    st.markdown(
        f"""<div style="background:{PANEL}; border:1px solid {BORDER}; border-radius:12px;
            padding:40px 32px; text-align:center; margin-top:2rem;">
            <div style="font-size:2.5rem; margin-bottom:12px;">📂</div>
            <div style="font-size:1.1rem; font-weight:600; color:{TEXT_PRI}; margin-bottom:8px;">
                No data ingested for {tn_name}
            </div>
            <div style="color:{TEXT_SEC}; max-width:420px; margin:0 auto; line-height:1.6;">
                Upload a consumer CSV and a DT CSV for the same day in the sidebar to get started.
                Evidence accumulates automatically with each new day you add.
            </div>
        </div>""",
        unsafe_allow_html=True,
    )
    st.stop()

matches_df, changes_df = engine.compute_matches(state)
days = sorted(state["days_ingested"])
total = len(matches_df)
high   = int((matches_df["confidence"] == "HIGH").sum())
medium = int((matches_df["confidence"] == "MEDIUM").sum())
low    = int((matches_df["confidence"] == "LOW").sum())
frozen = len(state["frozen_consumers"])

# ---------------------------------------------------------------------------
# Change-detection banner
# ---------------------------------------------------------------------------
if len(changes_df):
    st.markdown(
        f"<div style='font-weight:700; color:{ACCENT_LOW}; font-size:1rem; margin-bottom:8px;'>"
        f"⚠ {len(changes_df)} topology change(s) detected</div>",
        unsafe_allow_html=True,
    )
    for _, row in changes_df.iterrows():
        st.markdown(
            f'<div class="change-alert">'
            f'Consumer <b>{row["consumer_id"]}</b> ({row.get("consumer_name","")}) — '
            f'was confidently matched to <b>{row["older_dt"]}</b> '
            f'({row["older_days"]} older days), now confidently matches '
            f'<b>{row["recent_dt"]}</b> ({row["recent_days"]} recent days). '
            f'<span style="opacity:0.7">Worth a field check.</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    st.markdown("")

# ---------------------------------------------------------------------------
# Tabs: Overview / Consumers / Changes / DT Explorer / Debug
# ---------------------------------------------------------------------------
tab_overview, tab_consumers, tab_changes, tab_explorer, tab_debug = st.tabs(
    ["📊 Overview", "👤 Consumers", f"⚠ Changes ({len(changes_df)})", "🔌 DT Explorer", "🔧 Debug"]
)

# ============================================================
# TAB 1: OVERVIEW
# ============================================================
with tab_overview:
    st.markdown(
        f"<div style='color:{TEXT_SEC}; font-size:0.85rem; margin-bottom:1rem;'>"
        f"📅 <b style='color:{TEXT_PRI}'>{len(days)}</b> days ingested &nbsp;·&nbsp; "
        f"{days[0]} → {days[-1]} &nbsp;·&nbsp; "
        f"<b style='color:{TEXT_PRI}'>{total:,}</b> consumers tracked"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Metric cards
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total consumers", f"{total:,}")
    c2.metric("High confidence", f"{high:,}", f"{100*high/max(total,1):.1f}%")
    c3.metric("Medium", f"{medium:,}", f"{100*medium/max(total,1):.1f}%")
    c4.metric("Ambiguous (low)", f"{low:,}", f"{100*low/max(total,1):.1f}%")
    c5.metric("Frozen / locked", f"{frozen:,}", f"{100*frozen/max(high,1):.0f}% of HIGH")

    # -----------------------------------------------------------------
    # Coverage vs. master installation roster
    # -----------------------------------------------------------------
    st.markdown("<div class='gt-section-label'>Coverage vs. master installation roster</div>", unsafe_allow_html=True)
    if os.path.exists(master_dt_path) and os.path.exists(master_cons_path):
        master_dt_codes = engine.load_master_dt_roster(master_dt_path)
        master_cons_nums = engine.load_master_consumer_roster(master_cons_path)
        cov = engine.compute_coverage(state, master_dt_codes, master_cons_nums)

        cc1, cc2 = st.columns(2)
        with cc1:
            st.metric(
                "DTs with any outage evidence",
                f"{cov['dt_covered']:,} / {cov['dt_total']:,}",
                f"{100*cov['dt_covered']/max(cov['dt_total'],1):.1f}% covered",
            )
            if cov["dt_uncovered"] > 0:
                with st.expander(f"⚠ {cov['dt_uncovered']} DT(s) never seen in outage data"):
                    st.caption("On the master roster, but zero outage events so far — "
                               "could be a healthy DT with no faults, or a non-reporting meter.")
                    st.dataframe(pd.DataFrame({"dt_code": cov["dt_uncovered_list"]}), height=200, width='stretch')
        with cc2:
            st.metric(
                "Consumers with any outage evidence",
                f"{cov['consumer_covered']:,} / {cov['consumer_total']:,}",
                f"{100*cov['consumer_covered']/max(cov['consumer_total'],1):.1f}% covered",
            )
            bc = cov["consumer_by_confidence"]
            st.caption(f"Of covered consumers: {bc['HIGH']:,} HIGH · {bc['MEDIUM']:,} MEDIUM · {bc['LOW']:,} LOW")
            if cov["consumer_uncovered"] > 0:
                with st.expander(f"⚠ {cov['consumer_uncovered']} consumer(s) never seen in outage data"):
                    st.caption("On the master roster, but zero outage events so far — "
                               "worth checking if these meters are reporting at all.")
                    st.dataframe(pd.DataFrame({"consumer_number": cov["consumer_uncovered_list"]}), height=200, width='stretch')
    else:
        st.info("Upload the master DT + consumer installation CSVs in the sidebar to see coverage stats "
                "(what % of the real network your outage data has actually seen).")

    st.markdown("<div class='gt-section-label'>Confidence distribution over time</div>", unsafe_allow_html=True)
    st.caption(
        "Each point is one day's snapshot — percentage of consumers at that confidence level. "
        "Rising HIGH% means the engine is converging on correct topology."
    )

    # Trend chart
    hist_key = "trend_history"
    if hist_key not in state:
        state[hist_key] = []

    today_str = days[-1]
    existing = [h for h in state[hist_key] if h["date"] == today_str]
    point = {
        "date": today_str, "day_number": len(days),
        "high_pct": 100*high/max(total,1),
        "medium_pct": 100*medium/max(total,1),
        "low_pct": 100*low/max(total,1),
    }
    if existing:
        existing[0].update(point)
    else:
        state[hist_key].append(point)
        engine.save_state(state, tn_folder)

    trend_df = pd.DataFrame(sorted(state[hist_key], key=lambda h: h["day_number"]))
    trend_df = trend_df.set_index("day_number")[["high_pct", "medium_pct", "low_pct"]]
    trend_df.columns = ["Confident (HIGH) %", "Medium %", "Ambiguous (LOW) %"]
    st.line_chart(trend_df, color=[ACCENT, ACCENT_MED, ACCENT_LOW], height=280)

    st.markdown("<div class='gt-section-label'>Confidence breakdown — donut summary</div>", unsafe_allow_html=True)

    # Visual breakdown bar
    if total > 0:
        h_pct = high / total * 100
        m_pct = medium / total * 100
        l_pct = low / total * 100

        st.markdown(
            f"""<div style="display:flex; height:28px; border-radius:6px; overflow:hidden;
                border:1px solid {BORDER}; margin-bottom:8px;">
                <div style="width:{h_pct:.1f}%; background:{ACCENT}; display:flex; align-items:center;
                    justify-content:center; font-size:0.75rem; font-weight:700; color:#fff;">
                    {"HIGH" if h_pct > 8 else ""}
                </div>
                <div style="width:{m_pct:.1f}%; background:{ACCENT_MED}; display:flex; align-items:center;
                    justify-content:center; font-size:0.75rem; font-weight:700; color:#fff;">
                    {"MED" if m_pct > 6 else ""}
                </div>
                <div style="width:{l_pct:.1f}%; background:{ACCENT_LOW}; display:flex; align-items:center;
                    justify-content:center; font-size:0.75rem; font-weight:700; color:#fff;">
                    {"LOW" if l_pct > 6 else ""}
                </div>
            </div>
            <div style="display:flex; gap:16px; font-size:0.8rem; color:{TEXT_SEC};">
                <span><span style="color:{ACCENT}; font-weight:700;">■</span> HIGH {h_pct:.1f}%</span>
                <span><span style="color:{ACCENT_MED}; font-weight:700;">■</span> MEDIUM {m_pct:.1f}%</span>
                <span><span style="color:{ACCENT_LOW}; font-weight:700;">■</span> LOW {l_pct:.1f}%</span>
            </div>""",
            unsafe_allow_html=True,
        )

    st.markdown("<div class='gt-section-label'>Days ingested</div>", unsafe_allow_html=True)
    day_cols = st.columns(min(len(days), 10))
    for i, d in enumerate(days[:10]):
        with day_cols[i % 10]:
            st.markdown(
                f"<div style='background:{PANEL}; border:1px solid {BORDER}; border-radius:6px; "
                f"padding:6px 8px; text-align:center; font-size:0.75rem; font-family:monospace; "
                f"color:{TEXT_PRI};'>{d}</div>",
                unsafe_allow_html=True,
            )
    if len(days) > 10:
        st.caption(f"… and {len(days)-10} more days.")

# ============================================================
# TAB 2: CONSUMERS
# ============================================================
with tab_consumers:
    # Filters row
    f1, f2, f3, f4 = st.columns([3, 1.5, 1.5, 1.5])
    with f1:
        search = st.text_input(
            "Search",
            placeholder="Consumer ID, name, or DT code…",
            label_visibility="collapsed",
        )
    with f2:
        conf_filter = st.selectbox("Confidence", ["ALL", "HIGH", "MEDIUM", "LOW"], label_visibility="collapsed")
    with f3:
        sort_order = st.selectbox(
            "Sort",
            ["Lowest confidence first", "Highest confidence first", "Jaccard ↓", "Days with evidence ↓"],
            label_visibility="collapsed",
        )
    with f4:
        show_frozen = st.selectbox("Frozen", ["All", "Frozen only", "Active only"], label_visibility="collapsed")

    # Apply filters
    display_df = matches_df.copy()

    if conf_filter != "ALL":
        display_df = display_df[display_df["confidence"] == conf_filter]

    if show_frozen == "Frozen only":
        display_df = display_df[display_df.get("frozen", False) == True] if "frozen" in display_df.columns else display_df
    elif show_frozen == "Active only":
        if "frozen" in display_df.columns:
            display_df = display_df[display_df["frozen"] != True]

    if search:
        s = search.lower()
        mask = (
            display_df["consumer_id"].astype(str).str.lower().str.contains(s)
            | display_df["consumer_name"].astype(str).str.lower().str.contains(s)
            | display_df["predicted_dt"].astype(str).str.lower().str.contains(s)
        )
        display_df = display_df[mask]

    conf_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    display_df = display_df.assign(_order=display_df["confidence"].map(conf_order))

    if sort_order == "Lowest confidence first":
        display_df = display_df.sort_values("_order", ascending=True)
    elif sort_order == "Highest confidence first":
        display_df = display_df.sort_values("_order", ascending=False)
    elif sort_order == "Jaccard ↓":
        display_df = display_df.sort_values("jaccard_score", ascending=False)
    elif sort_order == "Days with evidence ↓":
        display_df = display_df.sort_values("days_with_evidence", ascending=False)

    display_df = display_df.drop(columns="_order", errors="ignore")

    n_shown = len(display_df)
    st.markdown(
        f"<div style='font-size:0.82rem; color:{TEXT_SEC}; margin-bottom:0.5rem;'>"
        f"Showing <b style='color:{TEXT_PRI}'>{n_shown:,}</b> of {total:,} consumers"
        f"{' (filtered)' if n_shown < total else ''}</div>",
        unsafe_allow_html=True,
    )

    show_cols = ["consumer_id", "consumer_name", "predicted_dt", "confidence",
                 "jaccard_score", "isolation_minutes", "days_with_evidence",
                 "tie_group_size", "claimed_dt_code"]
    show_cols = [c for c in show_cols if c in display_df.columns]

    # merge in installer-recorded status fields, if the master consumer roster
    # has been uploaded -- DATA QUALITY signal (was installation approved? did
    # it reach MDM?), independent of outage-timing confidence
    status_cols_present = False
    if os.path.exists(master_cons_path):
        status_df = engine.load_master_consumer_status(master_cons_path)
        display_df = display_df.merge(status_df, left_on="consumer_id", right_index=True, how="left")
        display_df["l2_approval_status"] = display_df["l2_approval_status"].fillna("No record")
        display_df["mdm_payload_status"] = display_df["mdm_payload_status"].fillna("No record")
        show_cols = show_cols + ["l2_approval_status", "mdm_payload_status"]
        status_cols_present = True

    if status_cols_present:
        def _approval_color(val):
            return "background-color: #2f5f47; color: #cdeedb" if val == "Approved" \
                else "background-color: #5c2e28; color: #f5c6c0"

        def _mdm_color(val):
            return "background-color: #2f5f47; color: #cdeedb" if val == "Success" \
                else "background-color: #5c2e28; color: #f5c6c0"

        styled = (
            display_df[show_cols].style
            .map(_approval_color, subset=["l2_approval_status"])
            .map(_mdm_color, subset=["mdm_payload_status"])
            .format({
                "jaccard_score": lambda v: f"{v:.4f}" if pd.notna(v) else "—",
                "isolation_minutes": lambda v: f"{v:.1f}" if pd.notna(v) else "—",
            })
        )
        st.dataframe(styled, width='stretch', height=460, hide_index=True)
    else:
        st.caption("Upload the master consumer installation CSV in the sidebar to see "
                   "L2 Approval Status / MDM Payload Status columns here.")
        st.dataframe(
            display_df[show_cols],
            width='stretch',
            height=460,
            column_config={
                "consumer_id": st.column_config.TextColumn("Consumer ID", width="small"),
                "consumer_name": st.column_config.TextColumn("Name", width="medium"),
                "predicted_dt": st.column_config.TextColumn("Predicted DT", width="small"),
                "confidence": st.column_config.TextColumn("Confidence", width="small"),
                "jaccard_score": st.column_config.NumberColumn("Jaccard Score", format="%.4f", width="small"),
                "isolation_minutes": st.column_config.NumberColumn("Isolation min.", format="%.1f", width="small"),
                "days_with_evidence": st.column_config.NumberColumn("Days evidence", format="%d", width="small"),
                "tie_group_size": st.column_config.NumberColumn("Tie group", format="%d", width="small"),
                "claimed_dt_code": st.column_config.TextColumn("Claimed DT", width="small"),
            },
            hide_index=True,
        )

    dl_col, _, stat_col = st.columns([2, 2, 3])
    with dl_col:
        st.download_button(
            "⬇ Export filtered view as CSV",
            display_df[show_cols].to_csv(index=False).encode("utf-8"),
            file_name=f"{tn_name}_matches_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            width='stretch',
        )
    with stat_col:
        st.markdown(
            f"<div style='font-size:0.78rem; color:{TEXT_MUT}; padding-top:10px; text-align:right;'>"
            f"State: {state_file_size(tn_folder):.1f} MB &nbsp;·&nbsp; "
            f"Active pairs: {len(state['pair_evidence']):,}"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Confidence distribution bar chart (per-DT breakdown)
    st.markdown("<div class='gt-section-label'>Distribution by predicted DT</div>", unsafe_allow_html=True)
    if len(matches_df) > 0 and "predicted_dt" in matches_df.columns:
        dt_counts = matches_df.groupby(["predicted_dt", "confidence"]).size().unstack(fill_value=0)
        # ensure all columns exist
        for col in ["HIGH", "MEDIUM", "LOW"]:
            if col not in dt_counts.columns:
                dt_counts[col] = 0
        dt_counts = dt_counts[["HIGH", "MEDIUM", "LOW"]].rename(columns={
            "HIGH": "HIGH confidence", "MEDIUM": "MEDIUM confidence", "LOW": "LOW (ambiguous)"
        })
        st.bar_chart(dt_counts, color=[ACCENT, ACCENT_MED, ACCENT_LOW], height=240)

# ============================================================
# TAB 3: CHANGES
# ============================================================
with tab_changes:
    if len(changes_df) == 0:
        days_needed = engine.RECENT_WINDOW_DAYS + engine.MIN_OLDER_DAYS
        have_days = len(days)
        st.markdown(
            f"""<div style="background:{PANEL}; border:1px solid {BORDER}; border-radius:10px;
                padding:32px 24px; text-align:center;">
                <div style="font-size:1.8rem; margin-bottom:10px;">✅</div>
                <div style="font-weight:600; color:{TEXT_PRI}; margin-bottom:6px;">
                    No topology changes detected
                </div>
                <div style="color:{TEXT_SEC}; font-size:0.875rem; max-width:420px; margin:0 auto;">
                    {"Change detection is active and found nothing to flag." if have_days >= days_needed
                     else f"Change detection needs {days_needed} days of history — you have {have_days}. "
                          f"Add {max(0, days_needed - have_days)} more day(s) to enable it."}
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<div style='font-size:0.875rem; color:{TEXT_SEC}; margin-bottom:1rem;'>"
            f"The engine compares the most recent <b style='color:{TEXT_PRI}'>{engine.RECENT_WINDOW_DAYS} days</b> "
            f"against the older window. A flag is only raised when BOTH windows are independently "
            f"confident and they disagree — not from single-day noise.</div>",
            unsafe_allow_html=True,
        )

        for _, row in changes_df.iterrows():
            with st.container():
                st.markdown(
                    f'<div class="change-alert">'
                    f'<b>Consumer {row["consumer_id"]}</b> ({row.get("consumer_name", "—")})<br>'
                    f'<span style="color:{TEXT_SEC}">Previously matched to</span> '
                    f'<b>{row["older_dt"]}</b> '
                    f'<span style="opacity:0.6">({row["older_days"]} older days)</span>'
                    f'<span style="color:{TEXT_SEC}"> · Now matches </span>'
                    f'<b>{row["recent_dt"]}</b> '
                    f'<span style="opacity:0.6">({row["recent_days"]} recent days)</span><br>'
                    f'<span style="font-size:0.8rem; color:{TEXT_MUT}; margin-top:4px; display:block;">'
                    f'Both windows are independently confident and disagree. Worth a field check.'
                    f'</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        st.markdown("<div class='gt-section-label'>All flagged changes</div>", unsafe_allow_html=True)
        st.dataframe(
            changes_df,
            width='stretch',
            hide_index=True,
            column_config={
                "consumer_id": st.column_config.TextColumn("Consumer ID"),
                "consumer_name": st.column_config.TextColumn("Name"),
                "older_dt": st.column_config.TextColumn("Old DT"),
                "older_days": st.column_config.NumberColumn("Old window days", format="%d"),
                "recent_dt": st.column_config.TextColumn("New DT"),
                "recent_days": st.column_config.NumberColumn("Recent window days", format="%d"),
                "note": st.column_config.TextColumn("Note", width="large"),
            },
        )

        st.download_button(
            "⬇ Export change log as CSV",
            changes_df.to_csv(index=False).encode("utf-8"),
            file_name=f"{tn_name}_changes_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
        )

# ============================================================
# TAB 4: DT EXPLORER
# ============================================================
with tab_explorer:
    explorer_summary, explorer_detail = build_dt_summary_from_state(state, matches_df)

    if explorer_summary is None or explorer_summary.empty:
        st.markdown(
            f"""<div style="background:{PANEL}; border:1px solid {BORDER}; border-radius:12px;
                padding:40px 32px; text-align:center;">
                <div style="font-size:2.5rem; margin-bottom:12px;">🔌</div>
                <div style="font-weight:600; color:{TEXT_PRI}; font-size:1.05rem; margin-bottom:8px;">
                    No match data yet
                </div>
                <div style="color:{TEXT_SEC}; max-width:460px; margin:0 auto; line-height:1.6;">
                    Ingest at least one day of data using the sidebar uploader. Once consumers
                    have been matched to DTs, the explorer will populate automatically.
                </div>
            </div>""",
            unsafe_allow_html=True,
        )
    else:
        total_dts  = len(explorer_summary)
        total_cons = int(explorer_summary["consumer_count"].sum())

        # ---- Summary metrics ----
        em1, em2, em3, em4 = st.columns(4)
        em1.metric("Unique DTs", f"{total_dts:,}")
        em2.metric("Total consumers", f"{total_cons:,}")
        em3.metric(
            "Avg consumers / DT",
            f"{total_cons / max(total_dts, 1):.1f}",
        )
        em4.metric(
            "Unique feeders",
            f"{explorer_summary['feeder_name'].nunique():,}",
        )

        st.markdown("")

        # ---- Filters ----
        ef1, ef2, ef3 = st.columns([3, 2, 2])
        with ef1:
            dt_search = st.text_input(
                "Search DT",
                placeholder="DT code, DT name, feeder, or substation…",
                key="dt_explorer_search",
                label_visibility="collapsed",
            )
        with ef2:
            feeder_opts = ["All feeders"] + sorted(explorer_summary["feeder_name"].dropna().unique().tolist())
            feeder_filter = st.selectbox(
                "Feeder", feeder_opts, key="dt_feeder_filter", label_visibility="collapsed"
            )
        with ef3:
            substation_opts = ["All substations"] + sorted(
                explorer_summary["substation_name"].dropna().unique().tolist()
            )
            sub_filter = st.selectbox(
                "Substation", substation_opts, key="dt_sub_filter", label_visibility="collapsed"
            )

        # Apply filters to summary
        filtered_summary = explorer_summary.copy()
        if dt_search:
            s = dt_search.lower()
            filtered_summary = filtered_summary[
                filtered_summary["dt_code"].astype(str).str.lower().str.contains(s)
                | filtered_summary["dt_name"].astype(str).str.lower().str.contains(s)
                | filtered_summary["feeder_name"].astype(str).str.lower().str.contains(s)
                | filtered_summary["substation_name"].astype(str).str.lower().str.contains(s)
            ]
        if feeder_filter != "All feeders":
            filtered_summary = filtered_summary[filtered_summary["feeder_name"] == feeder_filter]
        if sub_filter != "All substations":
            filtered_summary = filtered_summary[filtered_summary["substation_name"] == sub_filter]

        filtered_summary = filtered_summary.reset_index(drop=True)

        st.markdown(
            f"<div style='font-size:0.82rem; color:{TEXT_SEC}; margin-bottom:0.75rem;'>"
            f"Showing <b style='color:{TEXT_PRI}'>{len(filtered_summary):,}</b> of "
            f"{total_dts:,} DTs"
            f"{' (filtered)' if len(filtered_summary) < total_dts else ''}"
            f"</div>",
            unsafe_allow_html=True,
        )

        # ---- Top DT bar chart ----
        st.markdown("<div class='gt-section-label'>Consumer count per DT (top 25)</div>", unsafe_allow_html=True)
        chart_data = (
            filtered_summary.head(25)
            .set_index("dt_code")["consumer_count"]
            .rename("Consumers")
        )
        st.bar_chart(chart_data, color=ACCENT_BLUE, height=220)

        # ---- Per-DT rows with expandable consumer detail ----
        st.markdown(
            "<div class='gt-section-label'>Click a consumer count to drill down</div>",
            unsafe_allow_html=True,
        )

        # Track which DT is expanded
        if "explorer_expanded_dt" not in st.session_state:
            st.session_state["explorer_expanded_dt"] = None

        # Table header
        st.markdown(
            f"""<div style="display:grid; grid-template-columns:2fr 3fr 2fr 1.5fr 1.5fr auto;
                gap:0; padding:6px 12px; background:{PANEL};
                border:1px solid {BORDER}; border-radius:8px 8px 0 0;
                font-size:0.75rem; font-weight:700; letter-spacing:0.06em;
                text-transform:uppercase; color:{TEXT_MUT};">
                <div>DT Code</div>
                <div>DT Name</div>
                <div>Feeder</div>
                <div>Substation</div>
                <div style="text-align:center;">Consumers</div>
                <div></div>
            </div>""",
            unsafe_allow_html=True,
        )

        for idx, row in filtered_summary.iterrows():
            dt_code    = str(row["dt_code"])
            dt_name    = str(row["dt_name"])
            feeder     = str(row.get("feeder_name", "—"))
            substation = str(row.get("substation_name", "—"))
            count      = int(row["consumer_count"])
            is_expanded = st.session_state["explorer_expanded_dt"] == dt_code

            # row background alternation
            row_bg = PANEL if idx % 2 == 0 else PANEL_HOVER

            col_main, col_btn = st.columns([11, 1])
            with col_main:
                st.markdown(
                    f"""<div style="display:grid;
                        grid-template-columns:2fr 3fr 2fr 1.5fr 1.5fr;
                        gap:0; padding:9px 12px; background:{row_bg};
                        border-left:1px solid {BORDER}; border-right:1px solid {BORDER};
                        border-bottom:1px solid {BORDER};
                        font-size:0.85rem; color:{TEXT_PRI}; align-items:center;">
                        <div style="font-family:monospace; color:{ACCENT_BLUE}; font-weight:600;">
                            {dt_code}
                        </div>
                        <div style="color:{TEXT_PRI};">{dt_name}</div>
                        <div style="color:{TEXT_SEC};">{feeder}</div>
                        <div style="color:{TEXT_SEC}; font-size:0.8rem;">{substation}</div>
                        <div style="text-align:center;">
                            <span style="background:{BADGE_HIGH[0] if is_expanded else PANEL};
                                color:{BADGE_HIGH[1] if is_expanded else ACCENT};
                                border:1px solid {BADGE_HIGH[1] if is_expanded else ACCENT};
                                border-radius:20px; padding:2px 12px;
                                font-weight:700; font-family:monospace; font-size:0.85rem;">
                                {count:,}
                            </span>
                        </div>
                    </div>""",
                    unsafe_allow_html=True,
                )

            with col_btn:
                btn_label = "▲" if is_expanded else "▼"
                if st.button(
                    btn_label,
                    key=f"dt_expand_{dt_code}_{idx}",
                    width='stretch',
                    help=f"{'Collapse' if is_expanded else 'Show'} consumers for DT {dt_code}",
                ):
                    st.session_state["explorer_expanded_dt"] = (
                        None if is_expanded else dt_code
                    )
                    st.rerun()

            # ---- Expanded consumer panel ----
            if is_expanded:
                consumers_for_dt = explorer_detail.get(dt_code, pd.DataFrame())
                # derive category breakdown from the consumer detail (already in state)
                category_counts = pd.DataFrame()
                if len(consumers_for_dt) > 0 and "consumer_category" in consumers_for_dt.columns:
                    category_counts = consumers_for_dt["consumer_category"].value_counts()

                # info header for this DT
                st.markdown(
                    f"""<div style="background:{'#0e2030' if is_dark else '#e8f4fd'};
                        border:1px solid {ACCENT_BLUE};
                        border-top:none;
                        padding:14px 20px 10px 20px;
                        display:flex; flex-wrap:wrap; gap:24px; align-items:flex-start;">
                        <div>
                            <div style="font-size:0.7rem; color:{TEXT_MUT}; text-transform:uppercase;
                                letter-spacing:0.08em; margin-bottom:2px;">DT Code</div>
                            <div style="font-family:monospace; font-size:1rem; font-weight:700;
                                color:{ACCENT_BLUE};">{dt_code}</div>
                        </div>
                        <div>
                            <div style="font-size:0.7rem; color:{TEXT_MUT}; text-transform:uppercase;
                                letter-spacing:0.08em; margin-bottom:2px;">DT Name</div>
                            <div style="font-weight:600; color:{TEXT_PRI}; font-size:0.95rem;">
                                {dt_name}</div>
                        </div>
                        <div>
                            <div style="font-size:0.7rem; color:{TEXT_MUT}; text-transform:uppercase;
                                letter-spacing:0.08em; margin-bottom:2px;">Feeder</div>
                            <div style="color:{TEXT_SEC}; font-size:0.9rem;">{feeder}</div>
                        </div>
                        <div>
                            <div style="font-size:0.7rem; color:{TEXT_MUT}; text-transform:uppercase;
                                letter-spacing:0.08em; margin-bottom:2px;">Substation</div>
                            <div style="color:{TEXT_SEC}; font-size:0.9rem;">{substation}</div>
                        </div>
                        <div>
                            <div style="font-size:0.7rem; color:{TEXT_MUT}; text-transform:uppercase;
                                letter-spacing:0.08em; margin-bottom:2px;">Total consumers</div>
                            <div style="font-family:monospace; font-size:1.1rem; font-weight:700;
                                color:{ACCENT};">{count:,}</div>
                        </div>
                        {''.join(
                            f'<div><div style="font-size:0.7rem; color:{TEXT_MUT}; text-transform:uppercase; '
                            f'letter-spacing:0.08em; margin-bottom:2px;">{cat}</div>'
                            f'<div style="font-family:monospace; font-weight:600; color:{TEXT_PRI};">{n:,}</div></div>'
                            for cat, n in category_counts.items()
                        ) if len(category_counts) > 0 else ''}
                    </div>""",
                    unsafe_allow_html=True,
                )

                # Consumer search within this DT
                cons_search_col, cons_cat_col, cons_dl_col = st.columns([3, 2, 2])
                with cons_search_col:
                    cons_search = st.text_input(
                        "Filter consumers",
                        placeholder="Name, number, category…",
                        key=f"cons_search_{dt_code}",
                        label_visibility="collapsed",
                    )
                with cons_cat_col:
                    cat_options = ["All categories"]
                    if len(category_counts) > 0:
                        cat_options += list(category_counts.index)
                    cons_cat = st.selectbox(
                        "Category",
                        cat_options,
                        key=f"cons_cat_{dt_code}",
                        label_visibility="collapsed",
                    )

                display_cons = consumers_for_dt.copy()
                if cons_search:
                    sc = cons_search.lower()
                    mask = pd.Series([False] * len(display_cons), index=display_cons.index)
                    for col in ["consumer_number", "consumer_name", "consumer_category", "meter_id"]:
                        if col in display_cons.columns:
                            mask |= display_cons[col].astype(str).str.lower().str.contains(sc)
                    display_cons = display_cons[mask]
                if cons_cat != "All categories" and "consumer_category" in display_cons.columns:
                    display_cons = display_cons[display_cons["consumer_category"] == cons_cat]

                with cons_dl_col:
                    st.download_button(
                        f"⬇ Export {len(display_cons):,} consumers",
                        display_cons.to_csv(index=False).encode("utf-8"),
                        file_name=f"dt_{dt_code}_consumers.csv",
                        mime="text/csv",
                        key=f"dl_{dt_code}",
                        width='stretch',
                    )

                # Display consumer table
                show_consumer_cols = [
                    c for c in [
                        "consumer_number", "consumer_name",
                        "consumer_category", "meter_id",
                    ]
                    if c in display_cons.columns
                ]

                st.dataframe(
                    display_cons[show_consumer_cols].reset_index(drop=True),
                    width='stretch',
                    height=min(42 * len(display_cons) + 38, 480),
                    column_config={
                        "consumer_number": st.column_config.TextColumn(
                            "Consumer No.", width="medium"
                        ),
                        "consumer_name": st.column_config.TextColumn(
                            "Name", width="medium"
                        ),
                        "consumer_category": st.column_config.TextColumn(
                            "Category", width="small"
                        ),
                        "meter_id": st.column_config.TextColumn(
                            "Meter ID", width="small"
                        ),
                    },
                    hide_index=True,
                )

                st.markdown(
                    f"<div style='border-bottom:1px solid {BORDER}; margin-bottom:0;'></div>",
                    unsafe_allow_html=True,
                )

        # ---- Full DT summary table (collapsible) ----
        with st.expander(f"📋 Full DT summary table ({len(filtered_summary):,} DTs)", expanded=False):
            st.dataframe(
                filtered_summary.rename(columns={
                    "dt_code": "DT Code",
                    "dt_name": "DT Name",
                    "feeder_name": "Feeder",
                    "feeder_code": "Feeder Code",
                    "substation_name": "Substation",
                    "consumer_count": "Consumer Count",
                }),
                width='stretch',
                height=420,
                hide_index=True,
                column_config={
                    "Consumer Count": st.column_config.NumberColumn(format="%d"),
                },
            )
            st.download_button(
                "⬇ Export DT summary as CSV",
                filtered_summary.to_csv(index=False).encode("utf-8"),
                file_name=f"{tn_name}_dt_summary_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )


# ============================================================
# TAB 5: DEBUG
# ============================================================
with tab_debug:
    st.markdown(f"<div class='gt-section-label'>State summary</div>", unsafe_allow_html=True)

    info_items = [
        ("TN folder", tn_folder),
        ("State file size", f"{state_file_size(tn_folder):.2f} MB"),
        ("Days ingested", str(len(days))),
        ("Date range", f"{days[0]} → {days[-1]}" if days else "—"),
        ("Active pair evidence keys", f"{len(state['pair_evidence']):,}"),
        ("Frozen consumers", f"{frozen:,}"),
        ("DTs tracked", f"{len(state.get('dt_feeder', {})):,}"),
        ("Consumers tracked (meta)", f"{len(state.get('consumer_meta', {})):,}"),
        ("MIN_DAYS_FOR_HIGH_CONFIDENCE", str(engine.MIN_DAYS_FOR_HIGH_CONFIDENCE)),
        ("TIE_TOLERANCE", str(engine.TIE_TOLERANCE)),
        ("RECENT_WINDOW_DAYS", str(engine.RECENT_WINDOW_DAYS)),
        ("MIN_OLDER_DAYS", str(engine.MIN_OLDER_DAYS)),
    ]

    st.markdown(
        "<div class='summary-card'>"
        + "".join(
            f"<div class='info-row'>"
            f"<span class='info-label'>{k}</span>"
            f"<span class='info-value'>{v}</span>"
            f"</div>"
            for k, v in info_items
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(f"<div class='gt-section-label'>Jaccard score histogram (all consumers)</div>", unsafe_allow_html=True)
    if len(matches_df) > 0 and "jaccard_score" in matches_df.columns:
        hist_data = matches_df["jaccard_score"].dropna()
        if len(hist_data) > 0:
            bins = pd.cut(hist_data, bins=20)
            hist_counts = bins.value_counts().sort_index()
            hist_df = pd.DataFrame({"Jaccard bin": hist_counts.index.astype(str), "Count": hist_counts.values})
            hist_df = hist_df.set_index("Jaccard bin")
            st.bar_chart(hist_df, color=ACCENT_BLUE, height=200)
            st.caption("Higher Jaccard scores mean more overlap between consumer and DT outage windows.")

    st.markdown(f"<div class='gt-section-label'>Raw engine.py constants</div>", unsafe_allow_html=True)
    st.code(
        f"MIN_DAYS_FOR_HIGH_CONFIDENCE = {engine.MIN_DAYS_FOR_HIGH_CONFIDENCE}\n"
        f"TIE_TOLERANCE                = {engine.TIE_TOLERANCE}\n"
        f"RECENT_WINDOW_DAYS           = {engine.RECENT_WINDOW_DAYS}\n"
        f"MIN_OLDER_DAYS               = {engine.MIN_OLDER_DAYS}\n"
        f"FREEZE_DAYS                  = {engine.FREEZE_DAYS}\n"
        f"TOP_N_CANDIDATES             = {engine.TOP_N_CANDIDATES}\n"
        f"RELATIVE_GAP_THRESHOLD       = {engine.RELATIVE_GAP_THRESHOLD}",
        language="python",
    )

    st.markdown(f"<div class='gt-section-label'>Days ingested</div>", unsafe_allow_html=True)
    st.code("\n".join(days), language="text")