import io
import json
import re
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

NOISE_PHRASES = [
    "manage prescriptions",
    "schedule a vaccine",
    "weekly ad",
    "coupons rewards",
    "minuteclinic",
    "oak street health",
    "accessibility statement",
    "privacy policy",
    "terms of use",
    "shipping restrictions",
    "same-day delivery policies",
    "back to top",
    "sign in",
    "cart",
    "menu",
    "home",
    "shop",
    "wellness zone",
    "help center",
    "contact us",
    "return policy",
    "store locator",
]

FEATURE_KEYS = [
    "features",
    "feature",
    "benefits",
    "bullets",
    "bulletText",
    "keyFeatures",
]


# -----------------------------
# Generic helpers
# -----------------------------

def normalize_space(text):
    text = str(text or "")
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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


def make_excel_bytes(results_df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        results_df.to_excel(writer, index=False, sheet_name="cvs_copy_debugger")
    output.seek(0)
    return output.getvalue()


def is_noise_text(text):
    t = normalize_space(text).lower()
    if not t:
        return True
    if len(t) < 8:
        return True
    for phrase in NOISE_PHRASES:
        if phrase in t:
            return True
    return False


def safe_first(values):
    for value in values:
        value = normalize_space(value)
        if value:
            return value
    return ""


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

    for block in parse_jsonld_blocks(soup):
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

                for key in FEATURE_KEYS:
                    if key in node:
                        val = node[key]
                        if isinstance(val, list):
                            for item in val:
                                item = normalize_space(item)
                                if item and not is_noise_text(item):
                                    features.append(item)
                        else:
                            val = normalize_space(val)
                            if val and not is_noise_text(val):
                                features.append(val)

    # dedupe
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
# Extraction functions
# -----------------------------

def extract_title(soup, html_source):
    h1 = soup.find("h1")
    if h1:
        txt = normalize_space(h1.get_text(" ", strip=True))
        if txt:
            return txt

    jsonld = extract_from_jsonld(soup)
    if jsonld["title"]:
        return jsonld["title"]

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        return normalize_space(og_title.get("content"))

    title_tag = soup.find("title")
    if title_tag:
        txt = normalize_space(title_tag.get_text(" ", strip=True))
        if txt:
            return txt

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
        txt = normalize_space(meta_desc.get("content"))
        if txt:
            return txt

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        txt = normalize_space(og_desc.get("content"))
        if txt:
            return txt

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

    # last resort: clean visible text, trimmed
    page_text = clean_page_text(soup)
    return page_text[:2500]


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
            text = normalize_space(m.group(1))
            if text and not is_noise_text(text):
                return text

    # collect likely product bullets only
    bullets = []
    seen = set()
    for li in soup.find_all("li"):
        txt = normalize_space(li.get_text(" ", strip=True))
        if txt and not is_noise_text(txt) and len(txt) >= 20 and txt not in seen:
            seen.add(txt)
            bullets.append(txt)

    if bullets:
        return " | ".join(bullets[:20])

    # fallback: look for feature-ish claims in visible text
    page_text = clean_page_text(soup)
    claims = []
    claim_patterns = [
        r"3x [a-z ]+",
        r"septic[- ]safe",
        r"no perfumes",
        r"no dyes",
        r"hypoallergenic",
        r"dermatologist[- ]tested",
        r"95% water",
        r"flushable",
        r"odorblock",
        r"dryshield",
        r"moisturewick",
    ]
    for pattern in claim_patterns:
        for m in re.finditer(pattern, page_text, flags=re.IGNORECASE):
            claims.append(normalize_space(m.group(0)))

    claims = [c for c in dict.fromkeys(claims)]
    return " | ".join(claims[:20])


# -----------------------------
# Source context extraction
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
# App logic
# -----------------------------

def validate_columns(df):
    cols = {c.strip().lower(): c for c in df.columns}
    required = ["retail_url"]
    missing = [col for col in required if col not in cols]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    return cols


def process_items(df, max_rows):
    cols = validate_columns(df)

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
        retail_url = str(row[retail_col]).strip() if pd.notna(row[retail_col]) else ""
        sku = str(row[sku_col]).strip() if sku_col and pd.notna(row[sku_col]) else ""
        cvs_rpc = str(row[rpc_col]).strip() if rpc_col and pd.notna(row[rpc_col]) else ""
        brand = str(row[brand_col]).strip() if brand_col and pd.notna(row[brand_col]) else ""

        status_box.write(f"Processing row {i} of {total}: CVS RPC = {cvs_rpc or '(blank)'}")

        status_code = 0
        final_url = ""
        html_source = ""
        source_capture_status = "failed"
        source_capture_error = ""

        title_extracted = ""
        vendor_extracted = ""
        description_extracted = ""
        features_extracted = ""

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
            status_code, final_url, html_source = fetch_page(session, retail_url)

            if status_code == 200:
                source_capture_status = "success"
                soup = build_soup(html_source)

                title_extracted = extract_title(soup, html_source)
                vendor_extracted = extract_vendor(soup, html_source)
                description_extracted = extract_description(soup, html_source)
                features_extracted = extract_features(soup, html_source)
                contexts = extract_contexts(html_source)
            else:
                source_capture_error = f"http_{status_code}"

        except Exception as exc:
            source_capture_error = f"{type(exc).__name__}: {exc}"

        results.append({
            "sku": sku,
            "cvs_rpc": cvs_rpc,
            "brand": brand,
            "retail_url": retail_url,
            "final_url": final_url,
            "status_code": status_code,
            "source_capture_status": source_capture_status,
            "source_capture_error": source_capture_error,
            "source_bytes": len(html_source.encode('utf-8', errors='ignore')) if html_source else 0,
            "source_length": len(html_source) if html_source else 0,
            "title_extracted": title_extracted,
            "vendor_extracted": vendor_extracted,
            "description_extracted": description_extracted,
            "features_extracted": features_extracted,
            "title_anchor": contexts["title_anchor"],
            "title_source_context": contexts["title_source_context"],
            "vendor_anchor": contexts["vendor_anchor"],
            "vendor_source_context": contexts["vendor_source_context"],
            "description_anchor": contexts["description_anchor"],
            "description_source_context": contexts["description_source_context"],
            "features_anchor": contexts["features_anchor"],
            "features_source_context": contexts["features_source_context"],
        })

        progress.progress(i / total)

    progress.empty()
    status_box.empty()
    return pd.DataFrame(results)


def main():
    st.set_page_config(page_title="CVS Copy Extractor", layout="wide")
    st.title("CVS Copy Extractor")

    uploaded_file = st.file_uploader("Upload CVS CSV", type=["csv"])

    if uploaded_file is None:
        st.info("Upload a CSV with at least a retail_url column.")
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
