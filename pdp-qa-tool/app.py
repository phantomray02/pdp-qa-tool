
import re
import html
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from difflib import SequenceMatcher

requests.adapters.DEFAULT_RETRIES = 2
st.set_page_config(layout="wide")
st.title("CVS Extraction Matrix Debugger v2")
st.caption("Debug-only tester that exports many extraction variants to Excel for side-by-side analysis.")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

html_cache = {}
MAX_CACHE = 100


def get_html(url):
    if url in html_cache:
        html_cache[url] = html_cache.pop(url)
        return html_cache[url]
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Connection": "keep-alive"},
            timeout=12,
        )
        if r.status_code == 200:
            html_cache[url] = r.text
            while len(html_cache) > MAX_CACHE:
                html_cache.pop(next(iter(html_cache)))
            return r.text
    except Exception:
        pass
    return ""


def normalize_text(t):
    if not isinstance(t, str):
        return ""
    return re.sub(r'[^a-z0-9\s]', '', t.lower())


def keyword_score(a, b):
    return int(SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio() * 100)


def clean_cvs_text(text):
    if not text:
        return ""
    text = text.replace("\\u0026", "&")
    text = text.replace("\\n", " ")
    text = text.replace("\\/", "/")
    text = text.replace('\\"', '"')
    text = html.unescape(text)
    text = re.sub(r'^T\d+,', '', text)
    text = re.sub(r'\]\).*?self\.__next_f\.push\(\[1,"', '', text)
    text = re.sub(r'"\]\).*', '', text)
    text = re.sub(r'\$\w+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def split_sentences(text):
    txt = clean_cvs_text(text)
    if not txt:
        return []
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', txt)
    parts = [clean_cvs_text(p) for p in parts if len(clean_cvs_text(p)) > 10]
    return parts


def get_nextjs_chunks(html_text):
    pattern = r'self\.__next_f\.push\(\[1,(.*?)\]\)'
    matches = re.findall(pattern, html_text, re.DOTALL)
    chunks = []
    for m in matches:
        try:
            text = m.strip()
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            chunks.append(text)
        except Exception:
            continue
    return "\n".join(chunks)


def try_parse_jsonish(val):
    if not isinstance(val, str):
        return None
    vv = val.strip()
    if not vv:
        return None
    for candidate in [vv, vv.replace('\\"', '"'), html.unescape(vv).replace('\\"', '"')]:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def build_data_map(raw_text):
    data_map = {}
    n = len(raw_text)
    i = 0

    def is_key_start(pos):
        return re.match(r'([0-9a-zA-Z]{1,3}):(?=[\[\{T"])', raw_text[pos:])

    while i < n:
        m = is_key_start(i)
        if not m:
            i += 1
            continue
        key = m.group(1)
        val_start = i + len(key) + 1
        j = val_start
        depth = 0
        in_str = False
        escape = False
        while j < n:
            ch = raw_text[j]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in "[{(":
                    depth += 1
                elif ch in "]})":
                    depth = max(0, depth - 1)
                elif depth == 0 and is_key_start(j):
                    break
            j += 1
        val = raw_text[val_start:j].strip()
        if val.startswith("{") or val.startswith("["):
            parsed = try_parse_jsonish(val)
            data_map[key] = parsed if parsed is not None else val
        else:
            if val.startswith("T") and "," in val:
                data_map[key] = val.split(",", 1)[1].strip().strip('"')
            else:
                data_map[key] = val.strip().strip('"')
        i = j
    return data_map


def get_top_level_value(raw_text, target_key):
    pattern = rf'{re.escape(str(target_key))}:'
    m = re.search(pattern, raw_text)
    if not m:
        return None
    start = m.end()
    n = len(raw_text)
    j = start
    depth = 0
    in_str = False
    escape = False

    def is_key_start(pos):
        return re.match(r'([0-9a-zA-Z]{1,3}):(?=[\[\{T"])', raw_text[pos:])

    while j < n:
        ch = raw_text[j]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in "[{(":
                depth += 1
            elif ch in "]})":
                depth = max(0, depth - 1)
            elif depth == 0 and is_key_start(j):
                break
        j += 1
    return raw_text[start:j].strip()


def parse_top_level_value(val):
    if val is None:
        return None
    val = val.strip()
    if not val:
        return None
    if val.startswith("{") or val.startswith("["):
        parsed = try_parse_jsonish(val)
        return parsed if parsed is not None else val
    if val.startswith("T") and "," in val:
        return val.split(",", 1)[1].strip().strip('"')
    return val.strip().strip('"')


def resolve_ref_any(raw_text, data_map, value):
    if value is None:
        return None
    if isinstance(value, str) and value.startswith("$"):
        key = value[1:]
        if key in data_map:
            return data_map.get(key)
        return parse_top_level_value(get_top_level_value(raw_text, key))
    return value


def get_salsify_text(url):
    html_text = get_html(url)
    soup = BeautifulSoup(html_text, "html.parser")
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script:
        return {}
    data = json.loads(script.string)
    try:
        props = data["props"]["pageProps"]["product"]["propertySets"][0]["properties"]
    except Exception:
        return {}
    text_map = {}
    for p in props:
        key = p.get("property")
        values = p.get("values", [])
        if values:
            text_map[key] = values[0]
    return {
        "title": text_map.get("PRODUCT_TITLE", ""),
        "description": text_map.get("DESCRIPTION", ""),
        "feature1": text_map.get("FEATURE_1", ""),
        "feature2": text_map.get("FEATURE_2", ""),
        "feature3": text_map.get("FEATURE_3", ""),
        "feature4": text_map.get("FEATURE_4", ""),
        "feature5": text_map.get("FEATURE_5", ""),
    }


def get_visible_text(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    text = html.unescape(text)
    text = re.sub(r'\n+', '\n', text)
    return text


def extract_section_with_stops(visible_text, start_pat=None, stop_markers=None):
    txt = visible_text.replace('\xa0', ' ')
    section = txt
    if start_pat:
        m = re.search(start_pat, txt, re.S | re.I)
        if m:
            section = m.group(1) if m.groups() else txt[m.end():]
    stop_markers = stop_markers or [
        "Rating & reviews", "Ingredients", "Directions", "Warnings", "Specifications",
        "Same-Day Delivery policies", "Shipping restrictions", "FAQ", "Q:", "A:",
        "Delivery Details", "Explore more at CVS.com", "From ", "Show Hidden Columns",
        "Product details", "Details"
    ]
    cut = len(section)
    for stop in stop_markers:
        idx = section.find(stop)
        if idx != -1 and idx < cut:
            cut = idx
    return section[:cut].strip()


def extract_section_after_item(visible_text):
    return extract_section_with_stops(visible_text, r'Item\s*#\s*\d+\s*(.*)')


def extract_section_after_title(visible_text, title):
    if not title:
        return ""
    try:
        idx = visible_text.find(title)
        if idx == -1:
            return ""
        return extract_section_with_stops(visible_text[idx + len(title):])
    except Exception:
        return ""


def extract_meta_description(html_text):
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for tag in [
            soup.find("meta", attrs={"name": "description"}),
            soup.find("meta", attrs={"property": "og:description"}),
            soup.find("meta", attrs={"name": "twitter:description"}),
        ]:
            if tag and tag.get("content"):
                return clean_cvs_text(tag.get("content", ""))
    except Exception:
        pass
    return ""


def extract_jsonld_description(html_text):
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        vals = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            txt = script.string or script.get_text(" ", strip=True)
            if not txt:
                continue
            try:
                obj = json.loads(txt)
                items = obj if isinstance(obj, list) else [obj]
                for item in items:
                    if isinstance(item, dict) and item.get("description"):
                        vals.append(clean_cvs_text(str(item.get("description"))))
            except Exception:
                continue
        vals = [v for v in vals if len(v) > 20]
        return max(vals, key=len) if vals else ""
    except Exception:
        return ""


def extract_feature_lines_pattern_a(section):
    txt = clean_cvs_text(section)
    if not txt:
        return []
    pat = re.compile(
        r'((?:[A-Z0-9#&/\+\-\'\u2019\u2018\s]{4,90})\s*(?:—|-|:)\s*.+?)(?=(?:[A-Z0-9#&/\+\-\'\u2019\u2018\s]{4,90}\s*(?:—|-|:)\s*)|$)',
        re.S,
    )
    vals = [clean_cvs_text(m[0]) for m in pat.findall(txt)]
    return [v for v in vals if len(v) > 20][:5]


def extract_feature_lines_pattern_b(section):
    txt = clean_cvs_text(section)
    if not txt:
        return []
    raw_lines = re.split(r'\n+|\s{2,}', txt)
    out = []
    for line in raw_lines:
        line = clean_cvs_text(line)
        if len(line) < 20:
            continue
        if ("—" in line or ":" in line or " - " in line or ";" in line) and len(line.split()) >= 4:
            out.append(line)
    seen = set()
    dedup = []
    for x in out:
        k = normalize_text(x)
        if k not in seen:
            seen.add(k)
            dedup.append(x)
    return dedup[:5]


def extract_feature_lines_pattern_c(section):
    txt = clean_cvs_text(section)
    sents = split_sentences(txt)
    # candidate features are later descriptive sentences, not the first one or two if they look paragraph-like.
    out = []
    for sent in sents:
        if len(sent) >= 35 and len(sent.split()) >= 6:
            out.append(sent)
    seen = set()
    dedup = []
    for x in out:
        k = normalize_text(x)
        if k not in seen:
            seen.add(k)
            dedup.append(x)
    return dedup[:5]


def extract_feature_lines_pattern_d(section):
    txt = clean_cvs_text(section)
    raw = re.split(r'•|\u2022|\*+', txt)
    vals = [clean_cvs_text(x) for x in raw if len(clean_cvs_text(x)) > 20]
    seen = set()
    out = []
    for x in vals:
        k = normalize_text(x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out[:5]


def split_description_before_feature(section, features):
    txt = clean_cvs_text(section)
    if not txt:
        return ""
    if features:
        first = features[0]
        key = first.split()[0] if first.split() else first[:20]
        idx = txt.find(key)
        if idx > 0:
            return clean_cvs_text(txt[:idx])
    sents = split_sentences(txt)
    if sents:
        return clean_cvs_text(" ".join(sents[:2]))
    return txt


def description_first_sentence(section):
    sents = split_sentences(section)
    return clean_cvs_text(sents[0]) if sents else ""


def description_first_two_sentences(section):
    sents = split_sentences(section)
    return clean_cvs_text(" ".join(sents[:2])) if sents else ""


def description_first_three_sentences(section):
    sents = split_sentences(section)
    return clean_cvs_text(" ".join(sents[:3])) if sents else ""


def get_vendor_block_variants(raw_text, data_map):
    normalized_raw = raw_text.replace('\\"', '"')
    patterns = [
        r'\{\s*"vendorDetailsBullets"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*,\s*"vendorDetailsParagraph"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*\}',
        r'vendorDetailsBullets"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*,\s*"vendorDetailsParagraph"\s*:\s*"(\$[0-9a-zA-Z]+)"'
    ]
    candidates = []
    for v in data_map.values():
        if isinstance(v, dict) and "vendorDetailsBullets" in v and "vendorDetailsParagraph" in v:
            candidates.append((v.get("vendorDetailsBullets"), v.get("vendorDetailsParagraph"), "data_map", ""))
    for pat in patterns:
        for m in re.finditer(pat, normalized_raw):
            b, p = m.group(1), m.group(2)
            start = max(0, m.start() - 250)
            end = min(len(normalized_raw), m.end() + 2500)
            preview = clean_cvs_text(normalized_raw[start:end][:1000])
            candidates.append((b, p, "vendor_regex", preview))
    seen = set()
    out = []
    for b, p, source, preview in candidates:
        key = (b, p)
        if key in seen:
            continue
        seen.add(key)
        bullets = resolve_ref_any(raw_text, data_map, b)
        para = resolve_ref_any(raw_text, data_map, p)
        feat = [clean_cvs_text(x) for x in bullets] if isinstance(bullets, list) else []
        feat = [x for x in feat if len(x) > 20][:5]
        desc = clean_cvs_text(para) if isinstance(para, str) else ""
        out.append({
            "source": source,
            "bullets_ref": b,
            "para_ref": p,
            "preview": preview,
            "desc": desc,
            "features": feat,
        })
    return out


def best_feature_match_score(salsify_features, candidate_features):
    if not candidate_features:
        return 0
    scores = []
    for s in salsify_features:
        vals = [keyword_score(s or "", c) for c in candidate_features if isinstance(c, str)]
        scores.append(max(vals) if vals else 0)
    return int(sum(scores) / len(scores)) if scores else 0


def choose_preview(*vals):
    for v in vals:
        if isinstance(v, str) and v.strip():
            return clean_cvs_text(v[:1000])
    return ""


def build_variants(salsify_text, retail_html):
    raw_text = get_nextjs_chunks(retail_html)
    data_map = build_data_map(raw_text)
    visible_text = get_visible_text(retail_html)
    title = salsify_text.get("title", "")

    section_item = extract_section_after_item(visible_text)
    section_title = extract_section_after_title(visible_text, title)
    section_all = clean_cvs_text(visible_text[:4000])

    meta_desc = extract_meta_description(retail_html)
    jsonld_desc = extract_jsonld_description(retail_html)
    vendor_variants = get_vendor_block_variants(raw_text, data_map)

    variants = []

    def append_variant(name, desc, feats, preview="", source="", bullets_ref="", para_ref=""):
        variants.append({
            "Variant": name,
            "CVS Description Candidate": clean_cvs_text(desc),
            "CVS Features Candidate": feats[:5] if isinstance(feats, list) else [],
            "Variant Preview": choose_preview(preview, desc, " | ".join(feats) if isinstance(feats, list) else ""),
            "Variant Source": source,
            "Vendor Bullets Ref": bullets_ref,
            "Vendor Paragraph Ref": para_ref,
            "Raw Text Length": len(raw_text),
            "Data Map Key Count": len(data_map),
            "Has NextF": "self.__next_f.push([1," in retail_html,
            "Has vendorDetailsBullets Token": "vendorDetailsBullets" in raw_text,
            "Has vendorDetailsParagraph Token": "vendorDetailsParagraph" in raw_text,
            "Raw Preview": clean_cvs_text(raw_text[:1000]),
            "Visible Item Section Preview": clean_cvs_text(section_item[:1000]),
            "Visible Title Section Preview": clean_cvs_text(section_title[:1000]),
        })

    s_features = [salsify_text.get(f"feature{i}", "") for i in range(1, 6)]

    # Vendor-based variants.
    for idx, vv in enumerate(vendor_variants[:8], start=1):
        append_variant(
            f"vendor_variant_{idx}",
            vv.get("desc", ""),
            vv.get("features", []),
            preview=vv.get("preview", ""),
            source=vv.get("source", ""),
            bullets_ref=vv.get("bullets_ref", ""),
            para_ref=vv.get("para_ref", ""),
        )

    vendor_best = max(vendor_variants, key=lambda x: (len(x.get("features", [])), len(x.get("desc", "")))) if vendor_variants else {}
    append_variant("vendor_best", vendor_best.get("desc", ""), vendor_best.get("features", []), vendor_best.get("preview", ""), vendor_best.get("source", ""), vendor_best.get("bullets_ref", ""), vendor_best.get("para_ref", ""))

    # Meta / JSON-LD.
    append_variant("meta_desc", meta_desc, [])
    append_variant("jsonld_desc", jsonld_desc, [])

    # Visible section variants based on Item # section.
    feats_a_item = extract_feature_lines_pattern_a(section_item)
    feats_b_item = extract_feature_lines_pattern_b(section_item)
    feats_c_item = extract_feature_lines_pattern_c(section_item)
    feats_d_item = extract_feature_lines_pattern_d(section_item)

    append_variant("visible_item_pattern_a", split_description_before_feature(section_item, feats_a_item), feats_a_item, preview=section_item, source="visible_item")
    append_variant("visible_item_pattern_b", split_description_before_feature(section_item, feats_b_item), feats_b_item, preview=section_item, source="visible_item")
    append_variant("visible_item_pattern_c", split_description_before_feature(section_item, feats_c_item), feats_c_item, preview=section_item, source="visible_item")
    append_variant("visible_item_pattern_d", split_description_before_feature(section_item, feats_d_item), feats_d_item, preview=section_item, source="visible_item")
    append_variant("visible_item_desc_1sent", description_first_sentence(section_item), [])
    append_variant("visible_item_desc_2sent", description_first_two_sentences(section_item), [])
    append_variant("visible_item_desc_3sent", description_first_three_sentences(section_item), [])

    # Visible section variants based on title section.
    feats_a_title = extract_feature_lines_pattern_a(section_title)
    feats_b_title = extract_feature_lines_pattern_b(section_title)
    feats_c_title = extract_feature_lines_pattern_c(section_title)
    feats_d_title = extract_feature_lines_pattern_d(section_title)

    append_variant("visible_title_pattern_a", split_description_before_feature(section_title, feats_a_title), feats_a_title, preview=section_title, source="visible_title")
    append_variant("visible_title_pattern_b", split_description_before_feature(section_title, feats_b_title), feats_b_title, preview=section_title, source="visible_title")
    append_variant("visible_title_pattern_c", split_description_before_feature(section_title, feats_c_title), feats_c_title, preview=section_title, source="visible_title")
    append_variant("visible_title_pattern_d", split_description_before_feature(section_title, feats_d_title), feats_d_title, preview=section_title, source="visible_title")
    append_variant("visible_title_desc_1sent", description_first_sentence(section_title), [])
    append_variant("visible_title_desc_2sent", description_first_two_sentences(section_title), [])
    append_variant("visible_title_desc_3sent", description_first_three_sentences(section_title), [])

    # Coarse whole-visible-text sentence variants.
    append_variant("visible_all_desc_1sent", description_first_sentence(section_all), [], preview=section_all)
    append_variant("visible_all_desc_2sent", description_first_two_sentences(section_all), [], preview=section_all)
    append_variant("visible_all_desc_3sent", description_first_three_sentences(section_all), [], preview=section_all)

    # Add scoring fields.
    enriched = []
    for v in variants:
        desc = v["CVS Description Candidate"]
        feats = v["CVS Features Candidate"]
        enriched.append({
            **v,
            "Desc Score vs Salsify": keyword_score(salsify_text.get("description", ""), desc),
            "Feature Score vs Salsify": best_feature_match_score(s_features, feats),
            "Candidate Feature Count": len(feats),
            "Candidate Desc Length": len(clean_cvs_text(desc)),
        })
    return enriched


def process_row(row):
    retail_url = row.get("retail_url", "")
    salsify_url = row.get("salsify_url", "")
    sku = row.get("sku", "")
    cvs_rpc = row.get("cvs_rpc") or row.get("CVS RPC") or ""

    retail_html = get_html(retail_url)
    s = get_salsify_text(salsify_url)
    variants = build_variants(s, retail_html)

    rows = []
    for v in variants:
        rows.append({
            "SKU": sku,
            "CVS RPC": cvs_rpc,
            "Salsify URL": salsify_url,
            "Retail URL": retail_url,
            "Salsify Title": s.get("title", ""),
            "Salsify Description": s.get("description", ""),
            "Salsify Feature 1": s.get("feature1", ""),
            "Salsify Feature 2": s.get("feature2", ""),
            "Salsify Feature 3": s.get("feature3", ""),
            "Salsify Feature 4": s.get("feature4", ""),
            "Salsify Feature 5": s.get("feature5", ""),
            **v,
            "CVS Features Candidate": " | ".join(v.get("CVS Features Candidate", [])),
        })
    return rows


if uploaded_file:
    df = pd.read_csv(uploaded_file)
    df.columns = [c.strip().lower() for c in df.columns]
    column_map = {
        "salsify url": "salsify_url",
        "retail url": "retail_url",
        "sku id": "sku",
        "product sku": "sku",
        "cvs rpc": "cvs_rpc"
    }
    df.rename(columns=column_map, inplace=True)

    required_cols = ["sku", "salsify_url", "retail_url"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        st.error(f"Missing required columns: {missing}")
        st.write(list(df.columns))
        st.stop()

    if st.button("Run extraction matrix v2"):
        progress = st.progress(0)
        status = st.empty()
        rows = []
        total = len(df)
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(process_row, row.to_dict()) for _, row in df.iterrows()]
            for i, fut in enumerate(as_completed(futures), start=1):
                try:
                    res = fut.result()
                    if res:
                        rows.extend(res)
                except Exception:
                    pass
                progress.progress(i / total)
                status.write(f"Processed {i}/{total}")

        if rows:
            out_df = pd.DataFrame(rows)
            best_desc = out_df.sort_values(["SKU", "Desc Score vs Salsify", "Candidate Desc Length"], ascending=[True, False, False]).groupby("SKU", as_index=False).first()
            best_feat = out_df.sort_values(["SKU", "Feature Score vs Salsify", "Candidate Feature Count"], ascending=[True, False, False]).groupby("SKU", as_index=False).first()
            variant_summary = out_df.groupby("Variant", as_index=False).agg(
                rows=("SKU", "count"),
                avg_desc_score=("Desc Score vs Salsify", "mean"),
                avg_feat_score=("Feature Score vs Salsify", "mean"),
                variants_with_desc=("Candidate Desc Length", lambda s: int((s > 20).sum())),
                variants_with_feats=("Candidate Feature Count", lambda s: int((s > 0).sum())),
            )
            file_name = "cvs_extraction_matrix_v2.xlsx"
            with pd.ExcelWriter(file_name, engine="openpyxl") as writer:
                out_df.to_excel(writer, index=False, sheet_name="Extract Matrix")
                best_desc.to_excel(writer, index=False, sheet_name="Best Desc By SKU")
                best_feat.to_excel(writer, index=False, sheet_name="Best Feat By SKU")
                variant_summary.to_excel(writer, index=False, sheet_name="Variant Summary")
            with open(file_name, "rb") as f:
                st.success("Done.")
                st.download_button(
                    "Download extraction matrix v2",
                    data=f,
                    file_name=file_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
