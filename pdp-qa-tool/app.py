import io
import json
import re
from html import unescape
from typing import Dict, List, Tuple

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

REQUEST_TIMEOUT = 25
EXCEL_CELL_LIMIT = 30000
CONTEXT_WINDOW = 1200


# ---------- Generic helpers ----------

def normalize_space(text: str) -> str:
    text = str(text or "")
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = EXCEL_CELL_LIMIT) -> List[str]:
    text = text if isinstance(text, str) else str(text or "")
    if not text:
        return [""]
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def safe_join(items: List[str], sep: str = " | ") -> str:
    cleaned = []
    seen = set()
    for item in items:
        item = normalize_space(item)
        if item and item not in seen:
            seen.add(item)
            cleaned.append(item)
    return sep.join(cleaned)


def fetch_html(url: str) -> Tuple[int, str, str]:
    if not url or not str(url).strip():
        return 0, "", ""
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    return resp.status_code, resp.url, resp.text


def build_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def get_clean_page_text(soup: BeautifulSoup) -> str:
    soup_copy = BeautifulSoup(str(soup), "html.parser")
    for tag in soup_copy(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_space(soup_copy.get_text(separator=" ", strip=True))


def find_first_context(source: str, patterns: List[str], window: int = CONTEXT_WINDOW) -> Tuple[str, str]:
    source = str(source or "")
    if not source:
        return "", ""

    for pattern in patterns:
        match = re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
        if match:
            start = max(0, match.start() - window)
            end = min(len(source), match.end() + window)
            anchor = normalize_space(match.group(0))
            context = normalize_space(source[start:end])
            return anchor, context

    return "", ""


# ---------- JSON-LD / structured extraction ----------

def flatten_jsonld(obj):
    """Yield every dict node inside mixed JSON-LD structures."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from flatten_jsonld(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from flatten_jsonld(item)


def parse_jsonld_blocks(soup: BeautifulSoup) -> List[dict]:
    blocks = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            blocks.append(parsed)
        except Exception:
            # ignore malformed JSON-LD
            continue
    return blocks


def extract_from_jsonld(blocks: List[dict]) -> Dict[str, str]:
    title = ""
    vendor = ""
    description = ""
    features = []

    for block in blocks:
        for node in flatten_jsonld(block):
            node_type = str(node.get("@type", "")).lower()

            # Product-ish nodes are the most useful.
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
                            features.extend([normalize_space(x) for x in val if normalize_space(x)])
                        else:
                            v = normalize_space(val)
                            if v:
                                features.append(v)

    return {
        "title": normalize_space(title),
        "vendor": normalize_space(vendor),
        "description": normalize_space(description),
        "features": safe_join(features),
    }


# ---------- HTML/meta extraction ----------

def extract_title_candidates(soup: BeautifulSoup) -> List[str]:
    out = []

    h1 = soup.find("h1")
    if h1:
        out.append(h1.get_text(" ", strip=True))

    title_tag = soup.find("title")
    if title_tag:
        out.append(title_tag.get_text(" ", strip=True))

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        out.append(og_title.get("content"))

    return [normalize_space(x) for x in out if normalize_space(x)]


def extract_description_candidates(soup: BeautifulSoup) -> List[str]:
    out = []

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        out.append(meta_desc.get("content"))

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        out.append(og_desc.get("content"))

    return [normalize_space(x) for x in out if normalize_space(x)]


def extract_vendor_candidates(source: str, soup: BeautifulSoup) -> List[str]:
    out = []

    # Visible text patterns from the sample source like "From Cottonelle" / "See all Cottonelle products".
    vendor_patterns = [
        r'>\s*From\s+([^<]+)<',
        r'See all\s+([A-Za-z0-9 &._-]+)\s+products',
        r'"brand"\s*:\s*"([^"]+)"',
        r'"manufacturer"\s*:\s*"([^"]+)"',
        r'"vendor"\s*:\s*"([^"]+)"',
    ]

    for pattern in vendor_patterns:
        for m in re.finditer(pattern, source, flags=re.IGNORECASE | re.DOTALL):
            if m.groups():
                out.append(m.group(1))
            else:
                out.append(m.group(0))

    # Sometimes brand page links include the brand name.
    for a in soup.find_all("a", href=True):
        txt = normalize_space(a.get_text(" ", strip=True))
        href = a.get("href", "")
        if txt and ("brand-shop" in href or "See all" in txt):
            out.append(txt)

    return [normalize_space(x) for x in out if normalize_space(x)]


def extract_feature_candidates(source: str, soup: BeautifulSoup) -> List[str]:
    out = []

    # JSON-ish keys in raw source.
    feature_patterns = [
        r'"features"\s*:\s*(\[[^\]]+\])',
        r'"feature"\s*:\s*(\[[^\]]+\])',
        r'"benefits"\s*:\s*(\[[^\]]+\])',
        r'"bullets"\s*:\s*(\[[^\]]+\])',
        r'"bulletText"\s*:\s*(\[[^\]]+\])',
        r'"keyFeatures"\s*:\s*(\[[^\]]+\])',
    ]

    for pattern in feature_patterns:
        for m in re.finditer(pattern, source, flags=re.IGNORECASE | re.DOTALL):
            out.append(m.group(0))

    # Bullet-like HTML.
    for li in soup.find_all("li"):
        txt = normalize_space(li.get_text(" ", strip=True))
        if len(txt) >= 20:
            out.append(txt)

    return [normalize_space(x) for x in out if normalize_space(x)]


# ---------- Main extraction ----------

TITLE_CONTEXT_PATTERNS = [
    r"<title[^>]*>.*?</title>",
    r"<h1[^>]*>.*?</h1>",
    r'"title"\s*:\s*"[^"]+"',
    r'"name"\s*:\s*"[^"]+"',
    r'"productName"\s*:\s*"[^"]+"',
]

VENDOR_CONTEXT_PATTERNS = [
    r'>\s*From\s+[^<]+<',
    r'See all\s+[A-Za-z0-9 &._-]+\s+products',
    r'"brand"\s*:\s*"[^"]+"',
    r'"vendor"\s*:\s*"[^"]+"',
    r'"manufacturer"\s*:\s*"[^"]+"',
]

DESCRIPTION_CONTEXT_PATTERNS = [
    r'"description"\s*:\s*"[^"]+"',
    r'"longDescription"\s*:\s*"[^"]+"',
    r'"shortDescription"\s*:\s*"[^"]+"',
    r'"productDescription"\s*:\s*"[^"]+"',
    r'<meta[^>]+name="description"[^>]+content="[^"]+"',
    r'<meta[^>]+property="og:description"[^>]+content="[^"]+"',
]

FEATURES_CONTEXT_PATTERNS = [
    r'"features"\s*:\s*\[[^\]]+\]',
    r'"feature"\s*:\s*\[[^\]]+\]',
    r'"benefits"\s*:\s*\[[^\]]+\]',
    r'"bullets"\s*:\s*\[[^\]]+\]',
    r'"bulletText"\s*:\s*\[[^\]]+\]',
    r'"keyFeatures"\s*:\s*\[[^\]]+\]',
    r'<li[^>]*>.*?</li>',
]


def extract_fields_and_contexts(html_source: str) -> Dict[str, str]:
    soup = build_soup(html_source)
    jsonld_blocks = parse_jsonld_blocks(soup)
    jsonld_data = extract_from_jsonld(jsonld_blocks)

    title_candidates = []
    title_candidates.extend(extract_title_candidates(soup))
    if jsonld_data["title"]:
        title_candidates.insert(0, jsonld_data["title"])

    vendor_candidates = []
    vendor_candidates.extend(extract_vendor_candidates(html_source, soup))
    if jsonld_data["vendor"]:
        vendor_candidates.insert(0, jsonld_data["vendor"])

    desc_candidates = []
    desc_candidates.extend(extract_description_candidates(soup))
    if jsonld_data["description"]:
        desc_candidates.insert(0, jsonld_data["description"])

    feature_candidates = []
    if jsonld_data["features"]:
        feature_candidates.append(jsonld_data["features"])
    feature_candidates.extend(extract_feature_candidates(html_source, soup))

    title_extracted = next((x for x in title_candidates if x), "")
    vendor_extracted = next((x for x in vendor_candidates if x), "")
    description_extracted = next((x for x in desc_candidates if x), "")
    features_extracted = safe_join(feature_candidates)

    title_anchor, title_source_context = find_first_context(html_source, TITLE_CONTEXT_PATTERNS)
    vendor_anchor, vendor_source_context = find_first_context(html_source, VENDOR_CONTEXT_PATTERNS)
    description_anchor, description_source_context = find_first_context(html_source, DESCRIPTION_CONTEXT_PATTERNS)
    features_anchor, features_source_context = find_first_context(html_source, FEATURES_CONTEXT_PATTERNS)

    return {
        "title_extracted": title_extracted,
        "vendor_extracted": vendor_extracted,
        "description_extracted": description_extracted,
        "features_extracted": features_extracted,
        "title_anchor": title_anchor,
        "title_source_context": title_source_context,
        "vendor_anchor": vendor_anchor,
        "vendor_source_context": vendor_source_context,
        "description_anchor": description_anchor,
        "description_source_context": description_source_context,
        "features_anchor": features_anchor,
        "features_source_context": features_source_context,
    }


def validate_columns(df: pd.DataFrame):
    cols = {c.strip().lower(): c for c in df.columns}
    required = ["retail_url"]
    missing = [col for col in required if col not in cols]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    return cols


def make_excel_bytes(results_df: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        results_df.to_excel(writer, index=False, sheet_name="debugger")
    output.seek(0)
    return output.getvalue()


def process_items(df: pd.DataFrame) -> pd.DataFrame:
    cols = validate_columns(df)

    retail_col = cols["retail_url"]
    sku_col = cols.get("sku")
    rpc_col = cols.get("cvs rpc")
    brand_col = cols.get("brand")

    results = []

    progress = st.progress(0)
    total = len(df)

    for i, (_, row) in enumerate(df.iterrows(), start=1):
        retail_url = str(row[retail_col]).strip() if pd.notna(row[retail_col]) else ""
        sku = str(row[sku_col]).strip() if sku_col and pd.notna(row[sku_col]) else ""
        cvs_rpc = str(row[rpc_col]).strip() if rpc_col and pd.notna(row[rpc_col]) else ""
        brand = str(row[brand_col]).strip() if brand_col and pd.notna(row[brand_col]) else ""

        status_code = 0
        final_url = ""
        source_capture_status = "failed"
        source_capture_error = ""
        html_source = ""

        extracted = {
            "title_extracted": "",
            "vendor_extracted": "",
            "description_extracted": "",
            "features_extracted": "",
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
            status_code, final_url, html_source = fetch_html(retail_url)
            if status_code == 200:
                source_capture_status = "success"
                extracted = extract_fields_and_contexts(html_source)
            else:
                source_capture_error = f"http_{status_code}"

        except Exception as exc:
            source_capture_error = f"{type(exc).__name__}: {exc}"

        row_dict = {
            "sku": sku,
            "cvs_rpc": cvs_rpc,
            "brand": brand,
            "retail_url": retail_url,
            "final_url": final_url,
            "status_code": status_code,
            "source_capture_status": source_capture_status,
            "source_capture_error": source_capture_error,
            "source_bytes": len(html_source.encode("utf-8", errors="ignore")) if html_source else 0,
            "source_length": len(html_source) if html_source else 0,
            **extracted,
        }

        source_chunks = chunk_text(html_source)
        for idx_chunk, chunk in enumerate(source_chunks, start=1):
            row_dict[f"raw_source_{idx_chunk}"] = chunk

        results.append(row_dict)
        progress.progress(i / total)

    progress.empty()
    return pd.DataFrame(results)


def main():
    st.set_page_config(page_title="PDP QA Tool", layout="wide")
    st.title("PDP QA Tool")

    uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

    if uploaded_file is None:
        st.info("Upload a CVS CSV file to begin.")
        st.stop()

    try:
        df = pd.read_csv(uploaded_file)
    except Exception as exc:
        st.error(f"Could not read uploaded CSV: {exc}")
        st.stop()

    st.write("Preview of uploaded data:")
    st.dataframe(df.head(), use_container_width=True)

    if st.button("Run Extraction"):
        with st.spinner("Fetching CVS pages and extracting title/vendor/description/features..."):
            try:
                results_df = process_items(df)
            except Exception as exc:
                st.error(f"Extraction run failed: {exc}")
                st.stop()

        st.success("Extraction run complete.")
        st.dataframe(results_df.head(50), use_container_width=True)

        excel_bytes = make_excel_bytes(results_df)
        st.download_button(
            label="Download Debugger Excel",
            data=excel_bytes,
            file_name="cvs_debugger_with_source.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
