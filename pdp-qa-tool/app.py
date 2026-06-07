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
SECTION_WINDOW = 3000

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
    "wellness zone",
    "help center",
    "contact us",
    "return policy",
    "store locator",
    "careers",
    "social responsibility",
    "news room",
    "real estate",
    "supplier program",
    "additional resources",
    "patient authorization forms",
    "notice of privacy practices",
    "drug information center",
    "order cancellation policy",
    "language assistance",
    "non-discrimination policy",
    "refill prescriptions",
    "transfer prescriptions",
]

FEATURE_HINTS = [
    "3x",
    "strong",
    "stronger",
    "soft",
    "softest",
    "comfort",
    "absorbent",
    "absorbency",
    "cleaningripples",
    "septic",
    "flushable",
    "hypoallergenic",
    "dermatologist",
    "tested",
    "no perfumes",
    "no dyes",
    "95% water",
    "95 water",
    "aloe",
    "vitamin e",
    "chamomile",
    "odorblock",
    "dryshield",
    "moisturewick",
    "hsa",
    "fsa",
    "alcohol free",
    "free of added perfumes",
    "free of added dyes",
    "moisture-wicking",
    "maximum absorbency",
    "light absorbency",
    "in stock",
]

DESCRIPTION_HINTS = [
    "buy ",
    "free shipping",
    "coupons",
    "best deals",
    "shop cvs now",
    "mega rolls",
    "count",
    "wipes",
    "underwear",
    "guards",
    "shields",
    "adult wet wipes",
    "flushable",
    "incontinence",
]

# These are heading-like anchors that often sit near useful product copy.
DESCRIPTION_SECTION_PATTERNS = [
    r'"longDescription"\s*:\s*"[^"]+"',
    r'"shortDescription"\s*:\s*"[^"]+"',
    r'"description"\s*:\s*"[^"]+"',
    r'"productDescription"\s*:\s*"[^"]+"',
    r'<meta[^>]+name="description"[^>]+content="[^"]+"',
    r'<meta[^>]+property="og:description"[^>]+content="[^"]+"',
    r'Details',
    r'Specifications',
]

FEATURE_SECTION_PATTERNS = [
    r'"features"\s*:\s*\[[^\]]+\]',
    r'"feature"\s*:\s*\[[^\]]+\]',
    r'"benefits"\s*:\s*\[[^\]]+\]',
    r'"bullets"\s*:\s*\[[^\]]+\]',
    r'"bulletText"\s*:\s*\[[^\]]+\]',
    r'"keyFeatures"\s*:\s*\[[^\]]+\]',
    r'Details',
    r'Specifications',
    r'<li[^>]*>.*?</li>',
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
    response = session.get(
        url,
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )
    return response.status_code, response.url, response.text


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


def contains_noise(text):
    lowered = normalize_space(text).lower()
    if not lowered:
        return True
    for phrase in NOISE_PHRASES:
        if phrase in lowered:
            return True
    return False


def simple_sentence_split(text):
    text = normalize_space(text)
    if not text:
        return []
    splitters = re.split(r"(?<=[\.\!\?])\s+|\s+\|\s+|;\s+", text)
    cleaned = []
    for part in splitters:
        part = normalize_space(part)
        if part:
            cleaned.append(part)
    return cleaned


def dedupe_preserve_order(items):
    output = []
    seen = set()
    for item in items:
        item = normalize_space(item)
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


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
            parsed = json.loads(raw)
            blocks.append(parsed)
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
                        value = node[key]
                        if isinstance(value, list):
                            for item in value:
                                item = normalize_space(item)
                                if item and not contains_noise(item):
                                    features.append(item)
                        else:
                            value = normalize_space(value)
                            if value and not contains_noise(value):
                                features.append(value)

    features = dedupe_preserve_order(features)
    return {
        "title": title,
        "vendor": vendor,
        "description": description,
        "features": " | ".join(features[:25]),
    }


# -----------------------------
# Raw section pullers
# -----------------------------

def pull_big_section(source, patterns, window=SECTION_WINDOW):
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


def pull_title_section(source):
    return pull_big_section(
        source,
        [
            r"<h1[^>]*>.*?</h1>",
            r"<title[^>]*>.*?</title>",
            r'"productName"\s*:\s*"[^"]+"',
            r'"name"\s*:\s*"[^"]+"',
            r'"title"\s*:\s*"[^"]+"',
            r'<meta[^>]+property="og:title"[^>]+content="[^"]+"',
        ],
    )


def pull_vendor_section(source):
    return pull_big_section(
        source,
        [
            r'>\s*From\s+[^<]+<',
            r'See all\s+[A-Za-z0-9 &._-]+\s+products',
            r'"brand"\s*:\s*"[^"]+"',
            r'"vendor"\s*:\s*"[^"]+"',
            r'"manufacturer"\s*:\s*"[^"]+"',
        ],
    )


def pull_description_section(source):
    return pull_big_section(source, DESCRIPTION_SECTION_PATTERNS)


def pull_features_section(source):
    return pull_big_section(source, FEATURE_SECTION_PATTERNS)


# -----------------------------
# Cleaning logic for sections
# -----------------------------

def extract_quoted_payload(anchor):
    patterns = [
        r'"longDescription"\s*:\s*"([^"]+)"',
        r'"shortDescription"\s*:\s*"([^"]+)"',
        r'"description"\s*:\s*"([^"]+)"',
        r'"productDescription"\s*:\s*"([^"]+)"',
        r'"productName"\s*:\s*"([^"]+)"',
        r'"name"\s*:\s*"([^"]+)"',
        r'"title"\s*:\s*"([^"]+)"',
        r'"brand"\s*:\s*"([^"]+)"',
        r'"vendor"\s*:\s*"([^"]+)"',
        r'"manufacturer"\s*:\s*"([^"]+)"',
        r'content="([^"]+)"',
    ]
    for pattern in patterns:
        m = re.search(pattern, anchor, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return normalize_space(m.group(1))
    return ""


def clean_section_lines(section_text):
    text = normalize_space(section_text)
    if not text:
        return []

    # Strip visible HTML tags if still present.
    text = re.sub(r"<[^>]+>", " ", text)
    text = normalize_space(text)

    lines = simple_sentence_split(text)
    output = []

    for line in lines:
        lowered = line.lower()

        if len(line) < 8:
            continue
        if contains_noise(line):
            continue
        if re.match(r"^(home|shop|menu|cart|sign in)$", lowered):
            continue
        if "cvs pharmacy" == lowered:
            continue

        output.append(line)

    return dedupe_preserve_order(output)


def clean_description_from_section(anchor, section_text):
    anchor_payload = extract_quoted_payload(anchor)
    if anchor_payload and not contains_noise(anchor_payload):
        return anchor_payload, "anchor_payload"

    lines = clean_section_lines(section_text)

    keep = []
    for line in lines:
        lowered = line.lower()

        # Prefer product-commercial lines, but avoid obvious footer noise.
        if any(hint in lowered for hint in DESCRIPTION_HINTS):
            keep.append(line)

    if keep:
        return " ".join(keep[:6]), "description_section_lines"

    if lines:
        return " ".join(lines[:6]), "description_section_fallback"

    return "", "description_empty"


def clean_features_from_section(anchor, section_text):
    # 1. Try to parse structured arrays directly from anchor/context.
    array_patterns = [
        r'"features"\s*:\s*\[([^\]]+)\]',
        r'"feature"\s*:\s*\[([^\]]+)\]',
        r'"benefits"\s*:\s*\[([^\]]+)\]',
        r'"bullets"\s*:\s*\[([^\]]+)\]',
        r'"bulletText"\s*:\s*\[([^\]]+)\]',
        r'"keyFeatures"\s*:\s*\[([^\]]+)\]',
    ]

    for pattern in array_patterns:
        match = re.search(pattern, anchor + " " + section_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            payload = normalize_space(match.group(1))
            if payload and not contains_noise(payload):
                return payload, "features_json_array"

    # 2. Try bullet-like HTML from the section.
    bullets = []
    seen = set()
    section_soup = build_soup(section_text)
    for li in section_soup.find_all("li"):
        text = normalize_space(li.get_text(" ", strip=True))
        lowered = text.lower()
        if not text:
            continue
        if len(text) < 20:
            continue
        if contains_noise(text):
            continue
        if not any(hint in lowered for hint in FEATURE_HINTS):
            continue
        if text not in seen:
            seen.add(text)
            bullets.append(text)

    if bullets:
        return " | ".join(bullets[:20]), "features_html_bullets"

    # 3. Try cleaned lines with feature hints.
    lines = clean_section_lines(section_text)
    feature_lines = []
    for line in lines:
        lowered = line.lower()
        if any(hint in lowered for hint in FEATURE_HINTS):
            feature_lines.append(line)

    feature_lines = dedupe_preserve_order(feature_lines)

    if feature_lines:
        return " | ".join(feature_lines[:20]), "features_section_lines"

    # 4. Last resort: just extract hint phrases from the section.
    lowered = clean_section_lines(section_text)
    joined = " ".join(lowered).lower()
    hits = []
    for hint in FEATURE_HINTS:
        if hint in joined:
            hits.append(hint)

    hits = dedupe_preserve_order(hits)
    if hits:
        return " | ".join(hits[:20]), "features_hint_hits"

    return "", "features_empty"


# -----------------------------
# Field extraction
# -----------------------------

def extract_title(soup, html_source, title_anchor, title_section):
    h1 = soup.find("h1")
    if h1:
        text = normalize_space(h1.get_text(" ", strip=True))
        if text:
            return text, "h1"

    jsonld = extract_from_jsonld(soup)
    if jsonld["title"]:
        return jsonld["title"], "jsonld_title"

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        return normalize_space(og_title.get("content")), "og_title"

    title_tag = soup.find("title")
    if title_tag:
        text = normalize_space(title_tag.get_text(" ", strip=True))
        if text:
            return text, "html_title"

    payload = extract_quoted_payload(title_anchor)
    if payload:
        return payload, "title_anchor_payload"

    lines = clean_section_lines(title_section)
    if lines:
        return lines[0], "title_section_line"

    return "", "title_empty"


def extract_vendor(soup, html_source, vendor_anchor, vendor_section):
    jsonld = extract_from_jsonld(soup)
    if jsonld["vendor"]:
        return jsonld["vendor"], "jsonld_vendor"

    for a in soup.find_all("a", href=True):
        text = normalize_space(a.get_text(" ", strip=True))
        href = a.get("href", "")
        if text and ("brand-shop" in href or "See all" in text):
            text = re.sub(r"^See all\s+", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s+products$", "", text, flags=re.IGNORECASE)
            return normalize_space(text), "brand_shop_link"

    payload = extract_quoted_payload(vendor_anchor)
    if payload:
        return payload, "vendor_anchor_payload"

    text = vendor_anchor
    text = re.sub(r"^See all\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+products$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^From\s+", "", text, flags=re.IGNORECASE)
    text = normalize_space(text)

    if text:
        return text, "vendor_anchor_text"

    lines = clean_section_lines(vendor_section)
    if lines:
        return lines[0], "vendor_section_line"

    return "", "vendor_empty"


def extract_description(soup, html_source, description_anchor, description_section):
    jsonld = extract_from_jsonld(soup)
    if jsonld["description"]:
        return jsonld["description"], "jsonld_description"

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        text = normalize_space(meta_desc.get("content"))
        if text:
            return text, "meta_description"

    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        text = normalize_space(og_desc.get("content"))
        if text:
            return text, "og_description"

    cleaned, path = clean_description_from_section(description_anchor, description_section)
    if cleaned:
        return cleaned, path

    fallback = clean_page_text(soup)[:2500]
    return fallback, "visible_text_fallback"


def extract_features(soup, html_source, features_anchor, features_section):
    jsonld = extract_from_jsonld(soup)
    if jsonld["features"]:
        return jsonld["features"], "jsonld_features"

    cleaned, path = clean_features_from_section(features_anchor, features_section)
    if cleaned:
        return cleaned, path

    return "", "features_empty"


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

        raw_title_section = ""
        raw_vendor_section = ""
        raw_description_section = ""
        raw_features_section = ""

        title_anchor = ""
        vendor_anchor = ""
        description_anchor = ""
        features_anchor = ""

        title_extracted = ""
        vendor_extracted = ""
        description_extracted = ""
        features_extracted = ""

        title_extraction_path = ""
        vendor_extraction_path = ""
        description_extraction_path = ""
        features_extraction_path = ""

        cleaning_flags = []

        try:
            status_code, final_url, html_source = fetch_page(session, retail_url)

            if status_code == 200:
                source_capture_status = "success"
                soup = build_soup(html_source)

                title_anchor, raw_title_section = pull_title_section(html_source)
                vendor_anchor, raw_vendor_section = pull_vendor_section(html_source)
                description_anchor, raw_description_section = pull_description_section(html_source)
                features_anchor, raw_features_section = pull_features_section(html_source)

                title_extracted, title_extraction_path = extract_title(
                    soup, html_source, title_anchor, raw_title_section
                )
                vendor_extracted, vendor_extraction_path = extract_vendor(
                    soup, html_source, vendor_anchor, raw_vendor_section
                )
                description_extracted, description_extraction_path = extract_description(
                    soup, html_source, description_anchor, raw_description_section
                )
                features_extracted, features_extraction_path = extract_features(
                    soup, html_source, features_anchor, raw_features_section
                )

                if not title_extracted:
                    cleaning_flags.append("missing_title")
                if not vendor_extracted:
                    cleaning_flags.append("missing_vendor")
                if not description_extracted:
                    cleaning_flags.append("missing_description")
                if not features_extracted:
                    cleaning_flags.append("missing_features")

                if description_extraction_path in ("meta_description", "og_description"):
                    cleaning_flags.append("description_meta_only")
                if features_extraction_path in ("features_empty", "features_hint_hits"):
                    cleaning_flags.append("weak_features")

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
            "source_bytes": len(html_source.encode("utf-8", errors="ignore")) if html_source else 0,
            "source_length": len(html_source) if html_source else 0,

            "title_extracted": title_extracted,
            "vendor_extracted": vendor_extracted,
            "description_extracted": description_extracted,
            "features_extracted": features_extracted,

            "title_extraction_path": title_extraction_path,
            "vendor_extraction_path": vendor_extraction_path,
            "description_extraction_path": description_extraction_path,
            "features_extraction_path": features_extraction_path,

            "cleaning_flags": " | ".join(cleaning_flags),

            "title_anchor": title_anchor,
            "vendor_anchor": vendor_anchor,
            "description_anchor": description_anchor,
            "features_anchor": features_anchor,

            "raw_title_section": raw_title_section,
            "raw_vendor_section": raw_vendor_section,
            "raw_description_section": raw_description_section,
            "raw_features_section": raw_features_section,
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
        min_value=1,
        max_value=len(df),
        value=min(100, len(df)),
        step=1,
    )

    if st.button("Pull CVS Copy From Source"):
        with st.spinner("Fetching CVS pages, pulling big copy sections, and cleaning fields..."):
            try:
                results_df = process_items(df, int(max_rows))
            except Exception as exc:
                st.error(f"Run failed: {exc}")
                st.stop()

        st.success("Extraction complete.")
        st.dataframe(results_df.head(50), width="stretch")

        excel_bytes = make_excel_bytes(results_df)
        st.download_button(
            label="Download CVS Copy Debugger Excel",
            data=excel_bytes,
            file_name="cvs_copy_debugger.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
