IMPORTANT NOTE
--------------
This is a CLEAN RECOVERY app.py that fixes the Streamlit Cloud Flask-import crash.
It intentionally removes Flask/local-relay code because Flask belongs in html_fetch_server.py, not Streamlit Cloud.

If you want me to preserve your full QA logic, upload the REAL current app.py file, because only the Flask relay file has been uploaded in this thread.


# Replace your deployed app.py with this code:

import hashlib
from io import BytesIO

import pandas as pd
import streamlit as st
from pandas.errors import EmptyDataError

# =========================================
# APP SETUP
# =========================================
st.set_page_config(layout="wide")
st.title("PDP QA Tool ✅")

st.markdown(
    "<style>"
    "div[data-testid='stFileUploader'] > section {"
    "background:#232733;"
    "border:1px solid #2f3442;"
    "border-radius:10px;"
    "padding:10px;"
    "}"
    "div[data-testid='stDownloadButton'] > button {"
    "width:100%;"
    "min-height:56px;"
    "border-radius:10px;"
    "border:1px solid #2f3442;"
    "background:#232733;"
    "color:white;"
    "font-weight:700;"
    "}"
    "div[data-testid='stDownloadButton'] > button:hover {"
    "border-color:#4EA1FF;"
    "color:white;"
    "}"
    "</style>",
    unsafe_allow_html=True,
)

# =========================================
# HELPERS
# =========================================
def read_uploaded_file_from_bytes(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    if not file_bytes:
        raise EmptyDataError("Uploaded file is empty.")

    file_name = str(file_name or "").lower().strip()

    if file_name.endswith(".xlsx"):
        xls = pd.ExcelFile(BytesIO(file_bytes), engine="openpyxl")
        frames = []
        for sheet_name in xls.sheet_names:
            sheet_df = pd.read_excel(
                BytesIO(file_bytes),
                sheet_name=sheet_name,
                engine="openpyxl",
            )
            if sheet_df is not None and not sheet_df.empty:
                sheet_df["__sheet_name__"] = sheet_name
                frames.append(sheet_df)
        if not frames:
            raise EmptyDataError("Excel file contains no readable rows.")
        return pd.concat(frames, ignore_index=True)

    if file_name.endswith(".csv"):
        encodings = ["utf-8", "utf-8-sig", "cp1252", "latin1"]
        for enc in encodings:
            try:
                return pd.read_csv(BytesIO(file_bytes), encoding=enc)
            except Exception:
                pass
        raise EmptyDataError("CSV could not be read with supported encodings.")

    raise ValueError("Unsupported file type. Please upload .xlsx or .csv.")


def infer_retailer(url: str) -> str:
    u = str(url or "").lower()
    if "cvs.com" in u:
        return "CVS"
    if "walgreens.com" in u:
        return "Walgreens"
    if "kroger.com" in u:
        return "Kroger"
    if "samsclub.com" in u or "samsclub" in u:
        return "Sam's Club"
    if "salsify.com" in u or "app.salsify.com" in u or "shop.salsify.com" in u:
        return "Salsify"
    return ""


def prepare_input_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    rename_map = {}
    for col in df.columns:
        c = col.strip().lower()
        if c in ["sku", "item number", "item_number"]:
            rename_map[col] = "sku"
        elif c in ["brand"]:
            rename_map[col] = "brand"
        elif c in ["retail_url", "retailer url", "retailer_url", "pdp url", "url"]:
            rename_map[col] = "retail_url"
        elif c in ["salsify_url", "salsify url"]:
            rename_map[col] = "salsify_url"
        elif c in ["retailer"]:
            rename_map[col] = "retailer"

    if rename_map:
        df = df.rename(columns=rename_map)

    for required in ["sku", "brand", "retail_url", "salsify_url", "retailer"]:
        if required not in df.columns:
            df[required] = ""

    if "retailer" in df.columns:
        empty_mask = df["retailer"].astype(str).str.strip().eq("")
        df.loc[empty_mask, "retailer"] = df.loc[empty_mask, "retail_url"].apply(infer_retailer)

    return df


# =========================================
# SESSION STATE
# =========================================
if "last_file_hash" not in st.session_state:
    st.session_state.last_file_hash = None
if "selected_retailer" not in st.session_state:
    st.session_state.selected_retailer = "-- Select Retailer --"

# =========================================
# UI
# =========================================
st.markdown("### Upload Master File")
uploaded_file = st.file_uploader(
    "Upload .xlsx or .csv",
    type=["xlsx", "csv"],
    label_visibility="collapsed",
)

master_df = None
retailer_df = None
all_retailers = []
file_ready_for_batch = False
selected_retailer = ""

if uploaded_file:
    try:
        file_bytes = uploaded_file.getvalue()
        file_hash = hashlib.md5(file_bytes).hexdigest()

        if st.session_state.last_file_hash != file_hash:
            st.session_state.last_file_hash = file_hash
            st.session_state.selected_retailer = "-- Select Retailer --"

        master_df = read_uploaded_file_from_bytes(file_bytes, uploaded_file.name)
        master_df = prepare_input_df(master_df)

        if "retailer" in master_df.columns:
            all_retailers = sorted(
                [r for r in master_df["retailer"].dropna().astype(str).unique().tolist() if str(r).strip()]
            )

        if not all_retailers:
            all_retailers = ["CVS"]

        st.caption("Detected retailers in upload: " + ", ".join(all_retailers))

        retailer_options = ["-- Select Retailer --"] + all_retailers
        if st.session_state.selected_retailer not in retailer_options:
            st.session_state.selected_retailer = "-- Select Retailer --"

        selected_retailer = st.selectbox(
            "🏪 Select Retailer",
            retailer_options,
            key="selected_retailer",
            help="Select retailer to run batch.",
        )

        if selected_retailer == "-- Select Retailer --":
            st.info("Select retailer to continue.")
        else:
            file_ready_for_batch = True
            retailer_df = master_df[master_df["retailer"].astype(str) == selected_retailer].copy()

    except EmptyDataError:
        st.error("🔥 CRITICAL APP ERROR")
        st.text("The uploaded file is empty or could not be read.")
    except ValueError as e:
        st.error("❌ INPUT FILE ERROR")
        st.text(str(e))
    except Exception as e:
        st.error("🔥 CRITICAL APP ERROR")
        st.text(str(e))

# =========================================
# VIEW
# =========================================
st.markdown("## 🔎 QA Viewer Controls")
show_only_issues = st.checkbox("❌ Show ONLY Issues")
hide_good = st.checkbox("✅ Hide Strong Matches (80%+)")
show_below_90_only = st.checkbox("🔎 Show Only Scores Below 90%")

st.markdown("### 🧪 Debug Controls")
show_html_debugger = st.checkbox("Debug HTML")
standalone_debug_url = st.text_input("URL to pull raw HTML").strip()

if file_ready_for_batch and retailer_df is not None:
    if retailer_df.empty:
        st.warning("No rows found for the selected retailer.")
    else:
        st.success(f"Ready to process {len(retailer_df)} rows for {selected_retailer}.")
        preview_cols = [c for c in ["sku", "brand", "retail_url", "salsify_url"] if c in retailer_df.columns]
        if preview_cols:
            st.dataframe(retailer_df[preview_cols].head(50), use_container_width=True)
        else:
            st.dataframe(retailer_df.head(50), use_container_width=True)

if show_html_debugger and standalone_debug_url:
    st.info("Debugger placeholder active. Add your HTML fetch/debug logic here.")

st.markdown("---")
st.caption(
    "Important: this hosted app must remain a Streamlit app. "
    "Do not paste Flask/local relay code into app.py."
)
