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
SECTION_WINDOW = 3500

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
    "patient forms",
    "ethics",
    "human rights",
    "media exchange",
    "supplier",
    "privacy practices",
]

PROMO_PHRASES = [
    "buy ",
    "free shipping",
    "coupons",
    "best deals",
    "shop cvs now",
    "enjoy free shipping",
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
    "moisture-wicking",
    "hsa",
    "fsa",
    "alcohol free",
    "free of added perfumes",
    "free of added dyes",
    "maximum absorbency",
    "light absorbency",
    "trusted care",
    "soothing lotion",
    "cooling aloe",
]

TITLE_PATTERNS = [
    r"<h1[^>]*>.*?</h1>",
    r"<title[^>]*>.*?</title>",
    r'"productName"\s*:\s*"[^"]+"',
    r'"name"\s*:\s*"[^"]+"',
    r'"title"\s*:\s*"[^"]+"',
    r'<meta[^>]+property="og:title"[^>]+content="[^"]+"',
]

DESCRIPTION_PATTERNS = [
    r'"longDescription"\s*:\s*"[^"]+"',
    r'"shortDescription"\s*:\s*"[^"]+"',
    r'"description"\s*:\s*"([^"]+)"',
    r'"productDescription"\s*:\s*"([^"]+)"',
    r'<meta[^>]+name="description"[^>]+content="[^"]+"',
    r'<meta[^>]+property="og:description"[^>]+content="[^"]+"',
]

DETAILS_PATTERNS = [
    r'Details',
    r'Specifications',
    r'Directions',
    r'Warnings',
    r'Ingredients',
]

FEATURE_ARRAY_PATTERNS = [
    r'"features"\s*:\s*\[([^\]]+)\]',
    r'"feature"\s*:\s*\[([^\]]+)\]',
    r'"benefits"\s*:\s*\[([^\]]+)\]',
    r'"bullets"\s*:\s*\[([^\]]+)\]',
    r'"bulletText"\s*:\s*\[([^\]]+)\]',
    r'"keyFeatures"\s*:\s*\[([^\]]+)\]',
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
        results_df.to_excel(writer, index=False, sheet_name="cvs_copy")
    output.seek(0)
    return output.getvalue()


def dedupe_preserve_order(items):
    output = []
    seen = set()
    for item in items:
        item = normalize_space(item)
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def contains_noise(text):
    lowered = normalize_space(text).lower()
    if not lowered:
        return True
    for phrase in NOISE_PHRASES:
        if phrase in lowered:
            return True
    return False


def looks_promotional(text):
    lowered = normalize_space(text).lower()
    if not lowered:
        return False
    return any(phrase in lowered for phrase in PROMO_PHRASES)


def simple_sentence_split(text):
    text = normalize_space(text)
    if not text:
        return []
    parts = re.split(r"(?<=[\.\!\?])\s+|\s+\|\s+|;\s+|\s{2,}", text)
    return [normalize_space(p) for p in parts if normalize_space(p)]


def clean_section_lines(section_text):
    text = normalize_space(section_text)
    if not text:
        return []

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
        if lowered in {"details", "specifications", "directions", "warnings", "ingredients"}:
            continue

        output.append(line)

    return dedupe_preserve_order(output)


def extract_quoted_payload(text):
    patterns = [
        r'"longDescription"\s*:\s*"([^"]+)"',
        r'"shortDescription"\s*:\s*"([^"]+)"',
        r'"description"\s*:\s*"([^"]+)"',
        r'"productDescription"\s*:\s*"([^"]+)"',
        r'"productName"\s*:\s*"([^"]+)"',
        r'"name"\s*:\s*"([^"]+)"',
        r'"title"\s*:\s*"([^"]+)"',
        r'"brand"\s*:\s*"([^"]+)"',
        r'content="([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return normalize_space(match.group(1))
    return ""


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
    description = ""
    features = []
    image = ""

    for block in parse_jsonld_blocks(soup):
        for node in iter_json_nodes(block):
            node_type = str(node.get("@type", "")).lower()

            if "product" in node_type or node.get("name") or node.get("description"):
                if not title and node.get("name"):
                    title = normalize_space(node.get("name"))

                if not description and node.get("description"):
                    description = normalize_space(node.get("description"))

                if not image and node.get("image"):
                    img = node.get("image")
                    if isinstance(img, list) and img:
                        image = normalize_space(img[0])
                    else:
                        image = normalize_space(img)

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

    return {
        "title": title,
        "description": description,
        "features": " | ".join(dedupe_preserve_order(features)[:25]),
        "image": image,
    }


# -----------------------------
# Pull raw sections
# -----------------------------

def pull_title_section(source):
    return pull_big_section(source, TITLE_PATTERNS)


def pull_short_description_section(source):
    return pull_big_section(source, DESCRIPTION_PATTERNS)


def pull_details_section(source):
    return pull_big_section(source, DETAILS_PATTERNS)


def pull_features_section(source):
    patterns = FEATURE_ARRAY_PATTERNS + DETAILS_PATTERNS + [r"<li[^>]*>.*?</li>"]
    return pull_big_section(source, patterns)


# -----------------------------
# Extract fields
# -----------------------------

def extract_title(soup, title_anchor, raw_title_section):
    h1 = soup.find("h1")
    if h1:
        text = normalize_space(h1.get_text(" ", strip=True))
        if text:
            return text, "h1"

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        return normalize_space(og_title.get("content")), "og_title"

    jsonld = extract_from_jsonld(soup)
    if jsonld["title"]:
        return jsonld["title"], "jsonld_title"

    title_tag = soup.find("title")
    if title_tag:
        text = normalize_space(title_tag.get_text(" ", strip=True))
        if text:
            return text, "html_title"

    payload = extract_quoted_payload(title_anchor)
    if payload:
        return payload, "title_anchor_payload"

    lines = clean_section_lines(raw_title_section)
    if lines:
        return lines[0], "title_section_line"

    return "", "title_empty"


def extract_description(soup, short_description_anchor, raw_short_description_section, raw_details_section):
    # 1. Try deeper details section first if it has useful non-noisy lines.
    details_lines = clean_section_lines(raw_details_section)
    detail_keep = []
    for line in details_lines:
        lowered = line.lower()
        if any(hint in lowered for hint in FEATURE_HINTS):
            detail_keep.append(line)
        elif not contains_noise(line) and len(line) >= 25:
            detail_keep.append(line)

    detail_keep = dedupe_preserve_order(detail_keep)
    if detail_keep and not looks_promotional(" ".join(detail_keep[:4])):
        return " ".join(detail_keep[:8]), "details_section_lines"

    # 2. JSON-LD
    jsonld = extract_from_jsonld(soup)
    if jsonld["description"] and not looks_promotional(jsonld["description"]):
        return jsonld["description"], "jsonld_description"

    # 3. Meta / OG description
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

    # 4. Raw short description section
    payload = extract_quoted_payload(short_description_anchor)
    if payload:
        return payload, "short_description_anchor_payload"

    short_lines = clean_section_lines(raw_short_description_section)
    if short_lines:
        return " ".join(short_lines[:5]), "short_description_section_lines"

    # 5. Visible text fallback
    fallback = clean_page_text(soup)[:2500]
    return fallback, "visible_text_fallback"


def extract_features(raw_features_section, raw_details_section):
    source_for_features = f"{raw_features_section} {raw_details_section}"

    # 1. Structured arrays
    for pattern in FEATURE_ARRAY_PATTERNS:
        match = re.search(pattern, source_for_features, flags=re.IGNORECASE | re.DOTALL)
        if match:
            payload = normalize_space(match.group(1))
            if payload and not contains_noise(payload):
                return payload, "features_json_array"

    # 2. HTML bullets
    section_soup = build_soup(source_for_features)
    bullets = []
    seen = set()

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

    # 3. Cleaned lines with feature hints
    lines = clean_section_lines(source_for_features)
    keep = []
    for line in lines:
        lowered = line.lower()
        if any(hint in lowered for hint in FEATURE_HINTS):
            keep.append(line)

    keep = dedupe_preserve_order(keep)
    if keep:
        return " | ".join(keep[:20]), "features_section_lines"

    # 4. Final hint-only fallback
    lowered = normalize_space(source_for_features).lower()
    hits = []
    for hint in FEATURE_HINTS:
        if hint in lowered:
            hits.append(hint)

    hits = dedupe_preserve_order(hits)
    if hits:
        return " | ".join(hits[:20]), "features_hint_hits"

    return "", "features_empty"


def extract_image(soup):
    og_img = soup.find("meta", attrs={"property": "og:image"})
    if og_img and og_img.get("content"):
        return normalize_space(og_img.get("content")), "og_image"

    tw_img = soup.find("meta", attrs={"name": "twitter:image"})
    if tw_img and tw_img.get("content"):
        return normalize_space(tw_img.get("content")), "twitter_image"

    jsonld = extract_from_jsonld(soup)
    if jsonld["image"]:
        return jsonld["image"], "jsonld_image"

    for img in soup.find_all("img"):
        src = img.get("src")
        if src and "productimages" in src:
            return normalize_space(src), "img_tag"

    return "", "image_empty"


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

        status_box.write(f"Processing row {i} of {total}: CVS RPC = {cvs_rpc or '(blank)'}")

        status_code = 0
        final_url = ""
        html_source = ""
        source_capture_status = "failed"
        source_capture_error = ""

        title_anchor = ""
        short_description_anchor = ""
        details_anchor = ""
        features_anchor = ""

        raw_title_section = ""
        raw_short_description_section = ""
        raw_details_section = ""
        raw_features_section = ""

        title_extracted = ""
        description_extracted = ""
        features_extracted = ""
        image_extracted = ""

        title_extraction_path = ""
        description_extraction_path = ""
        features_extraction_path = ""
        image_extraction_path = ""

        cleaning_flags = []

        try:
            status_code, final_url, html_source = fetch_page(session, retail_url)

            if status_code == 200:
                source_capture_status = "success"
                soup = build_soup(html_source)

                title_anchor, raw_title_section = pull_title_section(html_source)
                short_description_anchor, raw_short_description_section = pull_short_description_section(html_source)
                details_anchor, raw_details_section = pull_details_section(html_source)
                features_anchor, raw_features_section = pull_features_section(html_source)

                title_extracted, title_extraction_path = extract_title(
                    soup,
                    title_anchor,
                    raw_title_section,
                )

                description_extracted, description_extraction_path = extract_description(
                    soup,
                    short_description_anchor,
                    raw_short_description_section,
                    raw_details_section,
                )

                features_extracted, features_extraction_path = extract_features(
                    raw_features_section,
                    raw_details_section,
                )

                image_extracted, image_extraction_path = extract_image(soup)

                if not title_extracted:
                    cleaning_flags.append("missing_title")
                if not description_extracted:
                    cleaning_flags.append("missing_description")
                if not features_extracted:
                    cleaning_flags.append("missing_features")
                if not image_extracted:
                    cleaning_flags.append("missing_image")

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
            "retail_url": retail_url,
            "final_url": final_url,
            "status_code": status_code,
            "source_capture_status": source_capture_status,
            "source_capture_error": source_capture_error,
            "source_bytes": len(html_source.encode("utf-8", errors="ignore")) if html_source else 0,
            "source_length": len(html_source) if html_source else 0,

            "title_extracted": title_extracted,
            "description_extracted": description_extracted,
            "features_extracted": features_extracted,
            "image_extracted": image_extracted,

            "title_extraction_path": title_extraction_path,
            "description_extraction_path": description_extraction_path,
            "features_extraction_path": features_extraction_path,
            "image_extraction_path": image_extraction_path,

            "cleaning_flags": " | ".join(cleaning_flags),

            "title_anchor": title_anchor,
            "short_description_anchor": short_description_anchor,
            "details_anchor": details_anchor,
            "features_anchor": features_anchor,

            "raw_title_section": raw_title_section,
            "raw_short_description_section": raw_short_description_section,
            "raw_details_section": raw_details_section,
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
        with st.spinner("Fetching CVS pages, pulling title/description/details/features/image, and cleaning fields..."):
            try:
                results_df = process_items(df, int(max_rows))
            except Exception as exc:
                st.error(f"Run failed: {exc}")
                st.stop()

        st.success("Extraction complete.")
        st.dataframe(results_df.head(50), width="stretch")

        excel_bytes = make_excel_bytes(results_df)
        st.download_button(
            label="Download CVS Copy Excel",
            data=excel_bytes,
            file_name="cvs_copy_debugger.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
