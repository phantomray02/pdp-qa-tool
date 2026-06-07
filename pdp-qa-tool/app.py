import io
import json
import re
from difflib import SequenceMatcher
from html import unescape

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

REQUEST_TIMEOUT = 15
CONTEXT_WINDOW = 1000

GENERIC_BRAND_WORDS = {
    "brand", "co", "company", "corp", "corporation", "inc", "llc", "ltd", "the"
}

TITLE_SYNONYMS = {
    "ultra comfort": "ultra soft",
    "toilet tissue": "toilet paper",
    "flushable wet wipes": "flushable wipes",
    "adult wet wipes": "flushable wipes",
    "fresh protection": "fit flex",
}

STOPWORDS = {
    "buy", "shop", "now", "with", "and", "the", "a", "an", "for", "of", "most", "orders"
}

FEATURE_KEYWORDS = [
    "cleaningripples",
    "softest",
    "strong",
    "strength",
    "absorbent",
    "absorbency",
    "3x",
    "thicker",
    "stronger",
    "septic",
    "safe",
    "no perfumes",
    "no dyes",
    "hypoallergenic",
    "dermatologist tested",
    "95 water",
    "flushable",
    "odorblock",
    "dryshield",
    "moisturewick",
    "hsa",
    "fsa",
    "aloe",
    "vitamin e",
    "chamomile",
    "alcohol free",
]


# -----------------------------
# Generic helpers
# -----------------------------

def normalize_space(text):
    text = str(text or "")
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_basic(text):
    text = normalize_space(text).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def apply_title_synonyms(text):
    text = text.lower()
    for src, dst in TITLE_SYNONYMS.items():
        text = text.replace(src, dst)
    return text


def normalize_vendor_name(text):
    text = clean_basic(text)
    parts = [p for p in text.split() if p not in GENERIC_BRAND_WORDS]
    return " ".join(parts).strip()


def normalize_title_text(text):
    text = apply_title_synonyms(normalize_space(text))
    text = clean_basic(text)
    parts = [p for p in text.split() if p not in STOPWORDS]
    return " ".join(parts).strip()


def sequence_score(a, b):
    a = normalize_space(a)
    b = normalize_space(b)
    if not a or not b:
        return 0
    return int(round(SequenceMatcher(None, a, b).ratio() * 100))


def vendor_score(a, b, fallback_brand=""):
    a_norm = normalize_vendor_name(a or fallback_brand)
    b_norm = normalize_vendor_name(b or fallback_brand)

    if not a_norm or not b_norm:
        return 0
    if a_norm == b_norm:
        return 100
    return int(round(SequenceMatcher(None, a_norm, b_norm).ratio() * 100))


def title_score(a, b):
    a_norm = normalize_title_text(a)
    b_norm = normalize_title_text(b)

    if not a_norm or not b_norm:
        return 0
    if a_norm == b_norm:
        return 100
    return int(round(SequenceMatcher(None, a_norm, b_norm).ratio() * 100))


def tokenize_feature_text(text):
    text = normalize_space(text).lower()

    # simple phrase preservation for useful phrases
    text = text.replace("no perfumes", "no_perfumes")
    text = text.replace("no dyes", "no_dyes")
    text = text.replace("dermatologist tested", "dermatologist_tested")
    text = text.replace("alcohol free", "alcohol_free")
    text = text.replace("vitamin e", "vitamin_e")
    text = text.replace("95% water", "95_water")
    text = text.replace("95 water", "95_water")
    text = text.replace("hsa/fsa", "hsa_fsa")
    text = text.replace("cleaning ripples", "cleaningripples")
    text = text.replace("septic safe", "septic_safe")

    text = re.sub(r"[^a-z0-9_\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    tokens = set()
    for token in text.split():
        if len(token) >= 3:
            tokens.add(token)

    for keyword in FEATURE_KEYWORDS:
        if keyword in text:
            tokens.add(keyword.replace(" ", "_"))

    return tokens


def keyword_overlap_score(a, b):
    a_tokens = tokenize_feature_text(a)
    b_tokens = tokenize_feature_text(b)

    if not a_tokens or not b_tokens:
        return 0

    overlap = len(a_tokens & b_tokens)
    union = len(a_tokens | b_tokens)

    return int(round((overlap / union) * 100)) if union else 0


def fetch_page(session, url):
    if not url or not str(url).strip():
        return 0, "", ""
    resp = session.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )
    return resp.status_code, resp.url, resp.text


def build_soup(html_source):
    return BeautifulSoup(html_source or "", "html.parser")


def clean_page_text(soup):
    soup_copy = BeautifulSoup(str(soup), "html.parser")
    for tag in soup_copy(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_space(soup_copy.get_text(separator=" ", strip=True))


# -----------------------------
# JSON-LD helpers
# -----------------------------

def parse_jsonld_blocks(soup):
    blocks = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw))
        except Exception:
            continue
    return blocks


def iter_json_nodes(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_json_nodes(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_json_nodes(item)


def extract_from_jsonld(soup):
    title = ""
    vendor = ""
    description = ""
    features = []

    blocks = parse_jsonld_blocks(soup)

    for block in blocks:
        for node in iter_json_nodes(block):
            node_type = str(node.get("@type", "")).lower()

            if "product" in node_type or node.get("name") or node.get("description"):
                if not title and node.get("name"):
                    title = normalize_space(node.get("name"))

                if not description and node.get("description"):
                    description = normalize_space(node.get("description"))

                brand = node.get("brand")
                if not vendor and brand:
                    if isinstance(brand, dict):
                        vendor = normalize_space(brand.get("name", ""))
                    else:
                        vendor = normalize_space(str(brand))

                for key in ["features", "feature", "benefits", "bullets", "bulletText", "keyFeatures"]:
                    if key in node:
                        val = node[key]
                        if isinstance(val, list):
                            for item in val:
                                item = normalize_space(item)
                                if item:
                                    features.append(item)
                        else:
                            val = normalize_space(val)
                            if val:
                                features.append(val)

    deduped = []
    seen = set()
    for item in features:
        if item not in seen:
            seen.add(item)
            deduped.append(item)

    return {
        "title": title,
        "vendor": vendor,
        "description": description,
        "features": " | ".join(deduped[:25]),
    }


# -----------------------------
# Field extraction
# -----------------------------

def extract_title(soup, html_source):
    h1 = soup.find("h1")
    if h1:
        text = normalize_space(h1.get_text(" ", strip=True))
        if text:
            return text

    jsonld = extract_from_jsonld(soup)
    if jsonld["title"]:
        return jsonld["title"]

    title_tag = soup.find("title")
    if title_tag:
        text = normalize_space(title_tag.get_text(" ", strip=True))
        if text:
            return text

    patterns = [
        r'"productName"\s*:\s*"([^"]+)"',
        r'"name"\s*:\s*"([^"]+)"',
        r'"title"\s*:\s*"([^"]+)"',
    ]

    for pattern in patterns:
        m = re.search(pattern, html_source, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return normalize_space(m.group(1))

    return ""


def extract_vendor(soup, html_source):
    jsonld = extract_from_jsonld(soup)
    if jsonld["vendor"]:
        return jsonld["vendor"]

    patterns = [
        r'>\s*From\s+([^<]+)<',
        r'See all\s+([A-Za-z0-9 &._-]+)\s+products',
        r'"brand"\s*:\s*"([^"]+)"',
        r'"vendor"\s*:\s*"([^"]+)"',
        r'"manufacturer"\s*:\s*"([^"]+)"',
    ]

    for pattern in patterns:
        m = re.search(pattern, html_source, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return normalize_space(m.group(1))

    for a in soup.find_all("a", href=True):
        txt = normalize_space(a.get_text(" ", strip=True))
        href = a.get("href", "")
        if txt and ("brand-shop" in href or "See all" in txt):
            return txt

    return ""


def extract_description(soup, html_source):
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        text = normalize_space(meta_desc.get("content"))
        if text:
            return text

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        text = normalize_space(og_desc.get("content"))
        if text:
            return text

    jsonld = extract_from_jsonld(soup)
    if jsonld["description"]:
        return jsonld["description"]

    patterns = [
        r'"longDescription"\s*:\s*"([^"]+)"',
        r'"shortDescription"\s*:\s*"([^"]+)"',
        r'"description"\s*:\s*"([^"]+)"',
        r'"productDescription"\s*:\s*"([^"]+)"',
    ]

    for pattern in patterns:
        m = re.search(pattern, html_source, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return normalize_space(m.group(1))

    return clean_page_text(soup)[:2500]


def extract_features(soup, html_source):
    jsonld = extract_from_jsonld(soup)
    if jsonld["features"]:
        return jsonld["features"]

    patterns = [
        r'"features"\s*:\s*\[([^\]]+)\]',
        r'"feature"\s*:\s*\[([^\]]+)\]',
        r'"benefits"\s*:\s*\[([^\]]+)\]',
        r'"bullets"\s*:\s*\[([^\]]+)\]',
        r'"bulletText"\s*:\s*\[([^\]]+)\]',
        r'"keyFeatures"\s*:\s*\[([^\]]+)\]',
    ]

    for pattern in patterns:
        m = re.search(pattern, html_source, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return normalize_space(m.group(1))

    bullets = []
    seen = set()

    for li in soup.find_all("li"):
        txt = normalize_space(li.get_text(" ", strip=True))
        if len(txt) >= 20 and txt not in seen:
            seen.add(txt)
            bullets.append(txt)

    if bullets:
        return " | ".join(bullets[:20])

    return ""


# -----------------------------
# Context extraction
# -----------------------------

def find_context(source, patterns, window=CONTEXT_WINDOW):
    source = str(source or "")
    if not source:
        return "", ""

    for pattern in patterns:
        m = re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
        if m:
            start = max(0, m.start() - window)
            end = min(len(source), m.end() + window)
            anchor = normalize_space(m.group(0))
            context = normalize_space(source[start:end])
            return anchor, context

    return "", ""


def extract_contexts(html_source):
    title_anchor, title_source_context = find_context(
        html_source,
        [
            r"<title[^>]*>.*?</title>",
            r"<h1[^>]*>.*?</h1>",
            r'"productName"\s*:\s*"[^"]+"',
            r'"name"\s*:\s*"[^"]+"',
            r'"title"\s*:\s*"[^"]+"',
        ],
    )

    vendor_anchor, vendor_source_context = find_context(
        html_source,
        [
            r'>\s*From\s+[^<]+<',
            r'See all\s+[A-Za-z0-9 &._-]+\s+products',
            r'"brand"\s*:\s*"[^"]+"',
            r'"vendor"\s*:\s*"[^"]+"',
            r'"manufacturer"\s*:\s*"[^"]+"',
        ],
    )

    description_anchor, description_source_context = find_context(
        html_source,
        [
            r'"longDescription"\s*:\s*"[^"]+"',
            r'"shortDescription"\s*:\s*"[^"]+"',
            r'"description"\s*:\s*"[^"]+"',
            r'"productDescription"\s*:\s*"[^"]+"',
            r'<meta[^>]+name="description"[^>]+content="[^"]+"',
            r'<meta[^>]+property="og:description"[^>]+content="[^"]+"',
        ],
    )

    features_anchor, features_source_context = find_context(
        html_source,
        [
            r'"features"\s*:\s*\[[^\]]+\]',
            r'"feature"\s*:\s*\[[^\]]+\]',
            r'"benefits"\s*:\s*\[[^\]]+\]',
            r'"bullets"\s*:\s*\[[^\]]+\]',
            r'"bulletText"\s*:\s*\[[^\]]+\]',
            r'"keyFeatures"\s*:\s*\[[^\]]+\]',
            r"<li[^>]*>.*?</li>",
        ],
    )

    return {
        "title_anchor": title_anchor,
        "title_source_context": title_source_context,
        "vendor_anchor": vendor_anchor,
        "vendor_source_context": vendor_source_context,
        "description_anchor": description_anchor,
        "description_source_context": description_source_context,
        "features_anchor": features_anchor,
        "features_source_context": features_source_context,
    }


# -----------------------------
# Diagnostics / status helpers
# -----------------------------

def build_mapping_warning(sku, cvs_rpc, s_title, r_title, s_vendor, r_vendor):
    warnings = []

    if not sku:
        warnings.append("missing_sku")
    if not cvs_rpc:
        warnings.append("missing_cvs_rpc")

    if s_vendor and r_vendor and vendor_score(s_vendor, r_vendor) == 0:
        warnings.append("vendor_zero_match")

    if s_title and r_title and title_score(s_title, r_title) < 40:
        warnings.append("title_low_match")

    return " | ".join(warnings)


def compare_status_for_row(error_text, s_status, r_status, t_score, v_score, d_score, f_score):
    if error_text:
        return "ERROR"
    if r_status != 200:
        return "RETAIL_FAIL"
    if s_status != 200:
        return "SALSIFY_FAIL"

    # Weighted logic: vendor + title matter most, features/description support.
    if v_score >= 70 and t_score >= 70 and (d_score >= 20 or f_score >= 15):
        return "PASS"

    if v_score == 100 and t_score >= 55 and (d_score >= 15 or f_score >= 10):
        return "PASS"

    return "FAIL"


# -----------------------------
# Main comparison / debugger logic
# -----------------------------

def validate_columns(df):
    cols = {c.strip().lower(): c for c in df.columns}
    required = ["salsify_url", "retail_url"]
    missing = [col for col in required if col not in cols]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    return cols


def make_excel_bytes(results_df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        results_df.to_excel(writer, index=False, sheet_name="debugger")
    output.seek(0)
    return output.getvalue()


def process_items(df, max_rows):
    cols = validate_columns(df)

    salsify_col = cols["salsify_url"]
    retail_col = cols["retail_url"]
    sku_col = cols.get("sku")
    rpc_col = cols.get("cvs rpc")
    brand_col = cols.get("brand")

    work_df = df.head(max_rows).copy()
    results = []

    session = requests.Session()
    session.headers.update(HEADERS)

    progress = st.progress(0.0)
    status_box = st.empty()
    total = len(work_df)

    for i, (_, row) in enumerate(work_df.iterrows(), start=1):
        salsify_url = str(row[salsify_col]).strip() if pd.notna(row[salsify_col]) else ""
        retail_url = str(row[retail_col]).strip() if pd.notna(row[retail_col]) else ""
        sku = str(row[sku_col]).strip() if sku_col and pd.notna(row[sku_col]) else ""
        cvs_rpc = str(row[rpc_col]).strip() if rpc_col and pd.notna(row[rpc_col]) else ""
        brand = str(row[brand_col]).strip() if brand_col and pd.notna(row[brand_col]) else ""

        status_box.write(f"Processing row {i} of {total}: CVS RPC = {cvs_rpc or '(blank)'}")

        s_status = 0
        r_status = 0
        s_final_url = ""
        r_final_url = ""
        s_html = ""
        r_html = ""
        error_text = ""

        s_title = ""
        s_vendor = ""
        s_description = ""
        s_features = ""

        r_title = ""
        r_vendor = ""
        r_description = ""
        r_features = ""

        contexts = {
            "title_anchor": "",
            "title_source_context": "",
            "vendor_anchor": "",
            "vendor_source_context": "",
            "description_anchor": "",
            "description_source_context": "",
            "features_anchor": "",
            "features_source_context": "",
        }

        try:
            s_status, s_final_url, s_html = fetch_page(session, salsify_url)
            r_status, r_final_url, r_html = fetch_page(session, retail_url)

            if s_status == 200:
                s_soup = build_soup(s_html)
                s_title = extract_title(s_soup, s_html)
                s_vendor = extract_vendor(s_soup, s_html)
                s_description = extract_description(s_soup, s_html)
                s_features = extract_features(s_soup, s_html)

            if r_status == 200:
                r_soup = build_soup(r_html)
                r_title = extract_title(r_soup, r_html)
                r_vendor = extract_vendor(r_soup, r_html)
                r_description = extract_description(r_soup, r_html)
                r_features = extract_features(r_soup, r_html)
                contexts = extract_contexts(r_html)

        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"

        # If the explicit brand column exists, use that as the fallback/anchor.
        v_score = vendor_score(s_vendor or brand, r_vendor or brand, fallback_brand=brand)
        t_score = title_score(s_title, r_title)
        d_score = keyword_overlap_score(s_description, r_description)
        f_score = keyword_overlap_score(s_features, r_features)

        mapping_warning = build_mapping_warning(
            sku,
            cvs_rpc,
            s_title,
            r_title,
            s_vendor or brand,
            r_vendor or brand,
        )

        compare_status = compare_status_for_row(
            error_text,
            s_status,
            r_status,
            t_score,
            v_score,
            d_score,
            f_score,
        )

        row_dict = {
            "sku": sku,
            "cvs_rpc": cvs_rpc,
            "brand": brand,
            "salsify_url": salsify_url,
            "retail_url": retail_url,
            "salsify_final_url": s_final_url,
            "retail_final_url": r_final_url,
            "salsify_status_code": s_status,
            "retail_status_code": r_status,
            "compare_status": compare_status,
            "mapping_warning": mapping_warning,
            "error": error_text,

            "salsify_title": s_title,
            "salsify_vendor": s_vendor,
            "salsify_description": s_description,
            "salsify_features": s_features,

            "cvs_title": r_title,
            "cvs_vendor": r_vendor,
            "cvs_description": r_description,
            "cvs_features": r_features,

            "title_score": t_score,
            "vendor_score": v_score,
            "description_score": d_score,
            "features_score": f_score,

            "source_bytes": len(r_html.encode("utf-8", errors="ignore")) if r_html else 0,
            "source_length": len(r_html) if r_html else 0,

            "title_anchor": contexts["title_anchor"],
            "title_source_context": contexts["title_source_context"],
            "vendor_anchor": contexts["vendor_anchor"],
            "vendor_source_context": contexts["vendor_source_context"],
            "description_anchor": contexts["description_anchor"],
            "description_source_context": contexts["description_source_context"],
            "features_anchor": contexts["features_anchor"],
            "features_source_context": contexts["features_source_context"],
        }

        results.append(row_dict)
        progress.progress(i / total)

    progress.empty()
    status_box.empty()
    return pd.DataFrame(results)


# -----------------------------
# Streamlit app
# -----------------------------

def main():
    st.set_page_config(page_title="PDP QA Tool", layout="wide")
    st.title("PDP QA Tool")

    uploaded_file = st.file_uploader("Upload comparison CSV", type=["csv"])

    if uploaded_file is None:
        st.info("Upload a CSV with at least salsify_url and retail_url columns.")
        st.stop()

    try:
        df = pd.read_csv(uploaded_file)
    except Exception as exc:
        st.error(f"Could not read uploaded CSV: {exc}")
        st.stop()

    st.write("Preview of uploaded data:")
    st.dataframe(df.head(), width="stretch")

    max_rows = st.number_input(
        "Rows to process",
        min_value=1,
        max_value=len(df),
        value=min(25, len(df)),
        step=1,
    )

    if st.button("Run Comparison + Debugger Extraction"):
        with st.spinner("Fetching pages, extracting fields, and comparing..."):
            try:
                results_df = process_items(df, int(max_rows))
            except Exception as exc:
                st.error(f"Run failed: {exc}")
                st.stop()

        st.success("Run complete.")
        st.dataframe(results_df.head(50), width="stretch")

        excel_bytes = make_excel_bytes(results_df)
        st.download_button(
            label="Download Comparison Debugger Excel",
            data=excel_bytes,
            file_name="pdp_qa_comparison_debugger.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
