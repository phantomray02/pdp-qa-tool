
import re
import html
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

requests.adapters.DEFAULT_RETRIES = 2
st.set_page_config(layout="wide")
st.title("PDP QA Tool v4 — CVS Live Extract Debugger")
st.caption("Focuses on extracting what is live on CVS. Exports multiple extraction strategies plus raw source windows to Excel.")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

html_cache = {}
MAX_CACHE = 100

# =========================================
# HTTP / CACHE
# =========================================
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

# =========================================
# TEXT HELPERS
# =========================================
def clean_text(text):
    if not text:
        return ""
    text = str(text)
    text = text.replace("\\u0026", "&")
    text = text.replace("\\n", " ")
    text = text.replace("\\/", "/")
    text = text.replace('\\"', '"')
    text = html.unescape(text)
    text = re.sub(r'^T\d+,', '', text)
    text = re.sub(r'\$\w+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def split_sentences(text):
    txt = clean_text(text)
    if not txt:
        return []
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', txt)
    parts = [clean_text(p) for p in parts if len(clean_text(p)) > 10]
    return parts


def has_nav_junk(text):
    txt = clean_text(text).lower()
    junk_markers = [
        'skip to main content', 'cvs pharmacy', 'weekly ad', 'extra big deals',
        'manage prescriptions', 'schedule a vaccine', 'photo coupons', 'carepass',
        'sign in account', 'use the cvs app', 'how to get it', 'rating & reviews'
    ]
    return any(j in txt for j in junk_markers)


def normalize_title(s):
    return re.sub(r'\s+', ' ', clean_text(s)).strip().lower()


def dedupe_keep_order(values):
    seen = set()
    out = []
    for v in values:
        key = clean_text(v).lower()
        if key and key not in seen:
            seen.add(key)
            out.append(clean_text(v))
    return out

# =========================================
# NEXT JS RAW SOURCE HELPERS
# =========================================
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


def get_vendor_ref_candidates(raw_text, data_map):
    normalized_raw = raw_text.replace('\\"', '"')
    patterns = [
        r'\{\s*"vendorDetailsBullets"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*,\s*"vendorDetailsParagraph"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*\}',
        r'vendorDetailsBullets"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*,\s*"vendorDetailsParagraph"\s*:\s*"(\$[0-9a-zA-Z]+)"'
    ]
    candidates = []
    for v in data_map.values():
        if isinstance(v, dict) and 'vendorDetailsBullets' in v and 'vendorDetailsParagraph' in v:
            candidates.append((v.get('vendorDetailsBullets'), v.get('vendorDetailsParagraph'), 'data_map', ''))
    for pat in patterns:
        for m in re.finditer(pat, normalized_raw):
            b, p = m.group(1), m.group(2)
            start = max(0, m.start() - 300)
            end = min(len(normalized_raw), m.end() + 2500)
            preview = normalized_raw[start:end]
            candidates.append((b, p, 'vendor_regex', preview))
    seen = set()
    out = []
    for b, p, src, preview in candidates:
        key = (b, p)
        if key in seen:
            continue
        seen.add(key)
        bullets = resolve_ref_any(raw_text, data_map, b)
        para = resolve_ref_any(raw_text, data_map, p)
        features = [clean_text(x) for x in bullets] if isinstance(bullets, list) else []
        features = [x for x in features if len(x) > 20]
        desc = clean_text(para) if isinstance(para, str) else ''
        out.append({
            'source': src,
            'bullets_ref': b or '',
            'para_ref': p or '',
            'preview': preview,
            'desc': desc,
            'features': dedupe_keep_order(features)[:5],
        })
    return out

# =========================================
# VISIBLE TEXT HELPERS
# =========================================
def get_visible_text(html_text):
    soup = BeautifulSoup(html_text, 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    text = soup.get_text('\n', strip=True)
    text = html.unescape(text)
    text = re.sub(r'\n+', '\n', text)
    return text


def extract_section_with_stops(visible_text, start_pat=None):
    txt = visible_text.replace('\xa0', ' ')
    section = txt
    if start_pat:
        m = re.search(start_pat, txt, re.S | re.I)
        if m:
            section = m.group(1) if m.groups() else txt[m.end():]
    stops = [
        'Rating & reviews', 'Ingredients', 'Directions', 'Warnings', 'Specifications',
        'Same-Day Delivery policies', 'Shipping restrictions', 'FAQ', 'Q:', 'A:',
        'Delivery Details', 'Explore more at CVS.com', 'From ', 'Show Hidden Columns',
        'CVS Health', 'Customers also bought'
    ]
    cut = len(section)
    for stop in stops:
        idx = section.find(stop)
        if idx != -1 and idx < cut:
            cut = idx
    return section[:cut].strip()


def extract_section_after_item(visible_text):
    return extract_section_with_stops(visible_text, r'Item\s*#\s*\d+\s*(.*)')


def extract_section_after_title(visible_text, title):
    if not title:
        return ''
    idx = visible_text.find(title)
    if idx == -1:
        return ''
    return extract_section_with_stops(visible_text[idx + len(title):])


def extract_meta_description(html_text):
    try:
        soup = BeautifulSoup(html_text, 'html.parser')
        for tag in [
            soup.find('meta', attrs={'name': 'description'}),
            soup.find('meta', attrs={'property': 'og:description'}),
            soup.find('meta', attrs={'name': 'twitter:description'}),
        ]:
            if tag and tag.get('content'):
                return clean_text(tag.get('content', ''))
    except Exception:
        pass
    return ''


def extract_jsonld_description(html_text):
    try:
        soup = BeautifulSoup(html_text, 'html.parser')
        vals = []
        for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
            txt = script.string or script.get_text(' ', strip=True)
            if not txt:
                continue
            try:
                obj = json.loads(txt)
                items = obj if isinstance(obj, list) else [obj]
                for item in items:
                    if isinstance(item, dict) and item.get('description'):
                        vals.append(clean_text(str(item.get('description'))))
            except Exception:
                continue
        vals = [v for v in vals if len(v) > 20]
        return max(vals, key=len) if vals else ''
    except Exception:
        return ''

# =========================================
# FEATURE STRATEGIES
# =========================================
def feature_pattern_b(section):
    txt = clean_text(section)
    if not txt:
        return []
    raw_lines = re.split(r'\n+|\s{2,}', txt)
    out = []
    for line in raw_lines:
        line = clean_text(line)
        if len(line) < 20:
            continue
        if ('—' in line or ':' in line or ' - ' in line or ';' in line) and len(line.split()) >= 4:
            out.append(line)
    return dedupe_keep_order(out)[:5]


def feature_pattern_c(section):
    txt = clean_text(section)
    if not txt:
        return []
    sents = split_sentences(txt)
    out = []
    for sent in sents:
        sent_clean = clean_text(sent)
        if len(sent_clean) >= 35 and len(sent_clean.split()) >= 6 and not has_nav_junk(sent_clean):
            out.append(sent_clean)
    return dedupe_keep_order(out)[:5]


def feature_pattern_d(section):
    txt = clean_text(section)
    if not txt:
        return []
    vals = re.split(r'•|\u2022|\*+', txt)
    vals = [clean_text(x) for x in vals if len(clean_text(x)) > 20]
    vals = [x for x in vals if not has_nav_junk(x)]
    return dedupe_keep_order(vals)[:5]


def description_first_n_sentences(section, n=3):
    sents = split_sentences(section)
    return clean_text(' '.join(sents[:n])) if sents else ''


def description_before_features(section, features, default_sent_count=3):
    txt = clean_text(section)
    if not txt:
        return ''
    if features:
        first = features[0]
        key = first.split()[0] if first.split() else first[:20]
        idx = txt.find(key)
        if idx > 0:
            return clean_text(txt[:idx])
    return description_first_n_sentences(txt, default_sent_count)


def score_description_quality(desc):
    txt = clean_text(desc)
    if not txt:
        return -999
    score = 0
    if len(txt) >= 80:
        score += 20
    if len(txt) >= 180:
        score += 15
    if not has_nav_junk(txt):
        score += 25
    if txt.lower().startswith('buy '):
        score -= 10
    if 'cvs pharmacy' in txt.lower():
        score -= 40
    if len(split_sentences(txt)) >= 2:
        score += 10
    return score


def score_feature_quality(features):
    if not isinstance(features, list):
        return -999
    feats = [clean_text(x) for x in features if clean_text(x)]
    feats = [x for x in feats if len(x) > 18 and not has_nav_junk(x)]
    if not feats:
        return -999
    score = 0
    score += min(len(feats), 5) * 15
    avg_len = sum(len(x) for x in feats) / max(len(feats), 1)
    if avg_len >= 40:
        score += 15
    if avg_len >= 70:
        score += 10
    return score

# =========================================
# STRATEGY COLLECTION
# =========================================
def collect_strategies(salsify_title, html_text):
    raw_text = get_nextjs_chunks(html_text)
    data_map = build_data_map(raw_text)
    visible_text = get_visible_text(html_text)
    item_section = extract_section_after_item(visible_text)
    title_section = extract_section_after_title(visible_text, salsify_title)

    vendor_variants = get_vendor_ref_candidates(raw_text, data_map)
    meta_desc = extract_meta_description(html_text)
    jsonld_desc = extract_jsonld_description(html_text)

    desc_strategies = {}
    feat_strategies = {}

    # Raw vendor strategies.
    for i, vv in enumerate(vendor_variants[:5], start=1):
        desc_strategies[f'vendor_desc_{i}'] = vv.get('desc', '')
        feat_strategies[f'vendor_features_{i}'] = vv.get('features', [])

    # Meta / JSONLD.
    desc_strategies['meta_desc'] = meta_desc
    desc_strategies['jsonld_desc'] = jsonld_desc

    # Visible item section.
    item_b = feature_pattern_b(item_section)
    item_c = feature_pattern_c(item_section)
    item_d = feature_pattern_d(item_section)
    feat_strategies['visible_item_pattern_b'] = item_b
    feat_strategies['visible_item_pattern_c'] = item_c
    feat_strategies['visible_item_pattern_d'] = item_d

    desc_strategies['visible_item_desc_1sent'] = description_first_n_sentences(item_section, 1)
    desc_strategies['visible_item_desc_2sent'] = description_first_n_sentences(item_section, 2)
    desc_strategies['visible_item_desc_3sent'] = description_first_n_sentences(item_section, 3)
    desc_strategies['visible_item_before_b'] = description_before_features(item_section, item_b, 3)
    desc_strategies['visible_item_before_c'] = description_before_features(item_section, item_c, 3)
    desc_strategies['visible_item_before_d'] = description_before_features(item_section, item_d, 3)

    # Visible title section.
    title_b = feature_pattern_b(title_section)
    title_c = feature_pattern_c(title_section)
    title_d = feature_pattern_d(title_section)
    feat_strategies['visible_title_pattern_b'] = title_b
    feat_strategies['visible_title_pattern_c'] = title_c
    feat_strategies['visible_title_pattern_d'] = title_d

    desc_strategies['visible_title_desc_1sent'] = description_first_n_sentences(title_section, 1)
    desc_strategies['visible_title_desc_2sent'] = description_first_n_sentences(title_section, 2)
    desc_strategies['visible_title_desc_3sent'] = description_first_n_sentences(title_section, 3)
    desc_strategies['visible_title_before_b'] = description_before_features(title_section, title_b, 3)
    desc_strategies['visible_title_before_c'] = description_before_features(title_section, title_c, 3)
    desc_strategies['visible_title_before_d'] = description_before_features(title_section, title_d, 3)

    # Build raw windows for debugging.
    desc_window = ''
    feat_window = ''
    normalized_raw = raw_text.replace('\\"', '"')

    # Around raw vendor markers first.
    m_desc = re.search(r'vendorDetailsParagraph', normalized_raw)
    if m_desc:
        start = max(0, m_desc.start() - 300)
        end = min(len(normalized_raw), m_desc.end() + 2800)
        desc_window = normalized_raw[start:end]
    if not desc_window and vendor_variants:
        desc_window = vendor_variants[0].get('preview', '')

    m_feat = re.search(r'vendorDetailsBullets', normalized_raw)
    if m_feat:
        start = max(0, m_feat.start() - 300)
        end = min(len(normalized_raw), m_feat.end() + 2800)
        feat_window = normalized_raw[start:end]
    if not feat_window and vendor_variants:
        feat_window = vendor_variants[0].get('preview', '')

    # Fallback raw-ish visible windows if vendor windows absent.
    if not desc_window:
        desc_window = item_section[:3500]
    if not feat_window:
        feat_window = item_section[:3500]

    return {
        'raw_text': raw_text,
        'data_map': data_map,
        'visible_text': visible_text,
        'item_section': item_section,
        'title_section': title_section,
        'vendor_variants': vendor_variants,
        'desc_strategies': desc_strategies,
        'feat_strategies': feat_strategies,
        'raw_desc_source_window': desc_window,
        'raw_feature_source_window': feat_window,
    }


def choose_best_description(desc_strategies):
    ranked = []
    for name, desc in desc_strategies.items():
        if clean_text(desc):
            ranked.append((score_description_quality(desc), name, clean_text(desc)))
    ranked.sort(reverse=True)
    return ranked[0] if ranked else (-999, '', '')


def choose_best_features(feat_strategies):
    ranked = []
    for name, feats in feat_strategies.items():
        feats_clean = [clean_text(x) for x in feats if clean_text(x)] if isinstance(feats, list) else []
        ranked.append((score_feature_quality(feats_clean), name, dedupe_keep_order(feats_clean)[:5]))
    ranked.sort(reverse=True)
    return ranked[0] if ranked else (-999, '', [])

# =========================================
# CSV ROW PROCESSING
# =========================================
def process_row(row):
    retail_url = row.get('retail_url', '')
    salsify_url = row.get('salsify_url', '')
    sku = row.get('sku', '')
    cvs_rpc = row.get('cvs_rpc') or row.get('CVS RPC') or ''
    salsify_title = clean_text(row.get('salsify_title', ''))

    html_text = get_html(retail_url)
    collected = collect_strategies(salsify_title, html_text)

    desc_score, best_desc_strategy, best_desc = choose_best_description(collected['desc_strategies'])
    feat_score, best_feat_strategy, best_feats = choose_best_features(collected['feat_strategies'])

    summary_row = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Salsify URL': salsify_url,
        'Retail URL': retail_url,
        'Best Description Strategy': best_desc_strategy,
        'Best Description Quality': desc_score,
        'Best Description': best_desc,
        'Best Feature Strategy': best_feat_strategy,
        'Best Feature Quality': feat_score,
        'Best Feature Count': len(best_feats),
        'Best Features': ' | '.join(best_feats),
        'Has NextF': 'self.__next_f.push([1,' in html_text,
        'Has vendorDetailsBullets Token': 'vendorDetailsBullets' in collected['raw_text'],
        'Has vendorDetailsParagraph Token': 'vendorDetailsParagraph' in collected['raw_text'],
        'Raw Text Length': len(collected['raw_text']),
        'Data Map Key Count': len(collected['data_map']),
        'Visible Item Section Preview': clean_text(collected['item_section'][:1200]),
        'Visible Title Section Preview': clean_text(collected['title_section'][:1200]),
    }

    strategy_row = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Salsify URL': salsify_url,
        'Retail URL': retail_url,
        'desc_meta': collected['desc_strategies'].get('meta_desc', ''),
        'desc_jsonld': collected['desc_strategies'].get('jsonld_desc', ''),
        'desc_visible_item_1sent': collected['desc_strategies'].get('visible_item_desc_1sent', ''),
        'desc_visible_item_2sent': collected['desc_strategies'].get('visible_item_desc_2sent', ''),
        'desc_visible_item_3sent': collected['desc_strategies'].get('visible_item_desc_3sent', ''),
        'desc_visible_item_before_b': collected['desc_strategies'].get('visible_item_before_b', ''),
        'desc_visible_item_before_c': collected['desc_strategies'].get('visible_item_before_c', ''),
        'desc_visible_item_before_d': collected['desc_strategies'].get('visible_item_before_d', ''),
        'desc_visible_title_1sent': collected['desc_strategies'].get('visible_title_desc_1sent', ''),
        'desc_visible_title_2sent': collected['desc_strategies'].get('visible_title_desc_2sent', ''),
        'desc_visible_title_3sent': collected['desc_strategies'].get('visible_title_desc_3sent', ''),
        'desc_visible_title_before_b': collected['desc_strategies'].get('visible_title_before_b', ''),
        'desc_visible_title_before_c': collected['desc_strategies'].get('visible_title_before_c', ''),
        'desc_visible_title_before_d': collected['desc_strategies'].get('visible_title_before_d', ''),
        'desc_vendor_1': collected['desc_strategies'].get('vendor_desc_1', ''),
        'desc_vendor_2': collected['desc_strategies'].get('vendor_desc_2', ''),
        'desc_vendor_3': collected['desc_strategies'].get('vendor_desc_3', ''),
        'feat_visible_item_b': ' | '.join(collected['feat_strategies'].get('visible_item_pattern_b', [])),
        'feat_visible_item_c': ' | '.join(collected['feat_strategies'].get('visible_item_pattern_c', [])),
        'feat_visible_item_d': ' | '.join(collected['feat_strategies'].get('visible_item_pattern_d', [])),
        'feat_visible_title_b': ' | '.join(collected['feat_strategies'].get('visible_title_pattern_b', [])),
        'feat_visible_title_c': ' | '.join(collected['feat_strategies'].get('visible_title_pattern_c', [])),
        'feat_visible_title_d': ' | '.join(collected['feat_strategies'].get('visible_title_pattern_d', [])),
        'feat_vendor_1': ' | '.join(collected['feat_strategies'].get('vendor_features_1', [])),
        'feat_vendor_2': ' | '.join(collected['feat_strategies'].get('vendor_features_2', [])),
        'feat_vendor_3': ' | '.join(collected['feat_strategies'].get('vendor_features_3', [])),
    }

    raw_row = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Salsify URL': salsify_url,
        'Retail URL': retail_url,
        'raw_desc_source_window': collected['raw_desc_source_window'],
        'raw_feature_source_window': collected['raw_feature_source_window'],
        'raw_text_preview': collected['raw_text'][:5000],
        'visible_item_section_raw': collected['item_section'][:5000],
        'visible_title_section_raw': collected['title_section'][:5000],
    }

    return summary_row, strategy_row, raw_row

# =========================================
# MAIN
# =========================================
if uploaded_file:
    df = pd.read_csv(uploaded_file)
    df.columns = [c.strip().lower() for c in df.columns]

    column_map = {
        'salsify url': 'salsify_url',
        'retail url': 'retail_url',
        'sku id': 'sku',
        'product sku': 'sku',
        'cvs rpc': 'cvs_rpc',
        'title': 'salsify_title',
        'salsify title': 'salsify_title',
    }
    df.rename(columns=column_map, inplace=True)

    if 'salsify_title' not in df.columns:
        df['salsify_title'] = ''

    required_cols = ['sku', 'salsify_url', 'retail_url']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        st.error(f"Missing required columns: {missing}")
        st.write(list(df.columns))
        st.stop()

    if st.button('Run CVS live extraction v4'):
        progress = st.progress(0)
        status = st.empty()

        summary_rows = []
        strategy_rows = []
        raw_rows = []

        total = len(df)
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(process_row, row.to_dict()) for _, row in df.iterrows()]
            for i, fut in enumerate(as_completed(futures), start=1):
                try:
                    summary_row, strategy_row, raw_row = fut.result()
                    summary_rows.append(summary_row)
                    strategy_rows.append(strategy_row)
                    raw_rows.append(raw_row)
                except Exception:
                    pass
                progress.progress(i / total)
                status.write(f'Processed {i}/{total}')

        if summary_rows:
            summary_df = pd.DataFrame(summary_rows)
            strategies_df = pd.DataFrame(strategy_rows)
            raw_df = pd.DataFrame(raw_rows)

            file_name = 'pdp_qa_tool_v4_output.xlsx'
            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                summary_df.to_excel(writer, index=False, sheet_name='Summary')
                strategies_df.to_excel(writer, index=False, sheet_name='Strategy Matrix')
                raw_df.to_excel(writer, index=False, sheet_name='Raw Windows')

            with open(file_name, 'rb') as f:
                st.success('Done.')
                st.download_button(
                    'Download v4 Excel output',
                    data=f,
                    file_name=file_name,
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
