
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
st.title("PDP QA Tool v4.3 — CVS Raw Source Area Debugger")
st.caption("Raw-source-first debugger. Pulls source windows around Details / Item / vendor refs / title / What’s Included and exports multiple extraction theories side by side.")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

html_cache = {}
MAX_CACHE = 100

# ============================================================
# HTTP
# ============================================================
def get_html(url):
    if url in html_cache:
        html_cache[url] = html_cache.pop(url)
        return html_cache[url]
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Connection": "keep-alive"},
            timeout=18,
        )
        if r.status_code == 200:
            html_cache[url] = r.text
            while len(html_cache) > MAX_CACHE:
                html_cache.pop(next(iter(html_cache)))
            return r.text
    except Exception:
        pass
    return ""

# ============================================================
# TEXT HELPERS
# ============================================================
def clean_text(text):
    if text is None:
        return ""
    text = str(text)
    if not text:
        return ""
    text = text.replace("\\u0026", "&")
    text = text.replace("\\n", " ")
    text = text.replace("\\/", "/")
    text = text.replace('\\"', '"')
    text = html.unescape(text)
    text = re.sub(r'^T\d+,', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def lines_from_visible_text(text):
    raw_lines = re.split(r'\n+', str(text or ""))
    out = []
    for line in raw_lines:
        line = clean_text(line)
        if line:
            out.append(line)
    return out


def split_sentences(text):
    txt = clean_text(text)
    if not txt:
        return []
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', txt)
    return [clean_text(p) for p in parts if len(clean_text(p)) > 10]


def dedupe_keep_order(values):
    seen = set()
    out = []
    for v in values:
        vv = clean_text(v)
        key = vv.lower()
        if vv and key not in seen:
            seen.add(key)
            out.append(vv)
    return out


def has_nav_junk(text):
    txt = clean_text(text).lower()
    junk_markers = [
        'skip to main content', 'cvs pharmacy', 'weekly ad', 'extra big deals', 'manage prescriptions',
        'schedule a vaccine', 'photo coupons', 'carepass', 'sign in account', 'use the cvs app',
        'how to get it', 'rating & reviews', 'extrabucks rewards', 'search cvs', 'same-day delivery policies',
        'shipping restrictions', 'summer manage prescriptions'
    ]
    return any(j in txt for j in junk_markers)


def looks_like_short_title(text):
    txt = clean_text(text)
    return (not txt) or (len(txt) <= 110 and txt.count('.') <= 1)


def cleanup_frontmatter(text):
    txt = clean_text(text)
    if not txt:
        return ""
    txt = re.sub(r'^.*?\bItem\s*#\s*\d+\s*', '', txt)
    txt = re.sub(r'^[A-Z0-9][A-Za-z0-9\-\+&/,()\'’ ]{6,140}\s+\d+\s*(?:Ct|Sht|Boxes?|Rolls?|Packs?|CT)\b[^A-Z]{0,20}', '', txt)
    txt = re.sub(r'^\d+\s*(?:Ct|Sht|Boxes?|Rolls?|Packs?|CT)\b\s*,?\s*\d*\.?\d*\s*(?:lbs?|oz)?\.?\s*', '', txt, flags=re.I)
    return clean_text(txt)


def clean_feature_line(text):
    txt = clean_text(text)
    if not txt:
        return ""
    txt = txt.replace(' \| ', ' ').replace('|', ' ')
    txt = re.sub(r'\s+', ' ', txt).strip(' -;:')
    if has_nav_junk(txt):
        return ""
    return txt


def clean_feature_list(values):
    out = []
    for v in values:
        vv = clean_feature_line(v)
        if vv and len(vv) > 20:
            out.append(vv)
    return dedupe_keep_order(out)[:5]


def format_labeled_blocks(blocks):
    out = []
    for label, txt in blocks:
        if txt:
            out.append(f'[{label}]\n{txt}')
    return '\n\n'.join(out)

# ============================================================
# RAW SOURCE HELPERS
# ============================================================
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
    return '\n'.join(chunks)


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
                elif ch == '\\':
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in '[{(':
                    depth += 1
                elif ch in ']})':
                    depth = max(0, depth - 1)
                elif depth == 0 and is_key_start(j):
                    break
            j += 1
        val = raw_text[val_start:j].strip()
        if val.startswith('{') or val.startswith('['):
            parsed = try_parse_jsonish(val)
            data_map[key] = parsed if parsed is not None else val
        else:
            if val.startswith('T') and ',' in val:
                data_map[key] = val.split(',', 1)[1].strip().strip('"')
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
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch in '[{(':
                depth += 1
            elif ch in ']})':
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
    if val.startswith('{') or val.startswith('['):
        parsed = try_parse_jsonish(val)
        return parsed if parsed is not None else val
    if val.startswith('T') and ',' in val:
        return val.split(',', 1)[1].strip().strip('"')
    return val.strip().strip('"')


def resolve_ref_any(raw_text, data_map, value):
    if value is None:
        return None
    if isinstance(value, str) and value.startswith('$'):
        key = value[1:]
        if key in data_map:
            return data_map.get(key)
        return parse_top_level_value(get_top_level_value(raw_text, key))
    return value


def window_around_marker(text, marker, back=350, forward=3500):
    if not text or not marker:
        return ''
    m = re.search(re.escape(marker), text)
    if not m:
        return ''
    start = max(0, m.start() - back)
    end = min(len(text), m.end() + forward)
    return text[start:end]


def find_vendor_objects(raw_text, data_map):
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
            preview = normalized_raw[max(0, m.start()-300):min(len(normalized_raw), m.end()+2800)]
            candidates.append((b, p, 'vendor_regex', preview))
    seen = set()
    out = []
    for b, p, source, preview in candidates:
        key = (b, p)
        if key in seen:
            continue
        seen.add(key)
        bullets = resolve_ref_any(raw_text, data_map, b)
        para = resolve_ref_any(raw_text, data_map, p)
        bullet_features = clean_feature_list([clean_text(x) for x in bullets] if isinstance(bullets, list) else [])
        out.append({
            'source': source,
            'bullets_ref': b or '',
            'para_ref': p or '',
            'desc': clean_text(para) if isinstance(para, str) else '',
            'features': bullet_features,
            'preview': preview,
            'resolved_bullets_raw': json.dumps(bullets, ensure_ascii=False)[:5000] if bullets is not None else '',
            'resolved_para_raw': str(para)[:5000] if para is not None else '',
        })
    return out

# ============================================================
# HTML / VISIBLE SECTIONS
# ============================================================
def get_visible_text(html_text):
    soup = BeautifulSoup(html_text, 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    text = soup.get_text('\n', strip=True)
    text = html.unescape(text)
    text = re.sub(r'\n+', '\n', text)
    return text


def section_from_lines(lines, start_idx):
    if start_idx is None or start_idx < 0 or start_idx >= len(lines):
        return ''
    stops = [
        'rating & reviews', 'ingredients', 'directions', 'warnings', 'specifications',
        'same-day delivery policies', 'shipping restrictions', 'faq', 'q:', 'a:',
        'delivery details', 'explore more at cvs.com', 'show hidden columns',
        'customers also bought', 'similar products', 'you may also like', 'read reviews'
    ]
    collected = []
    for i in range(start_idx, len(lines)):
        line = lines[i]
        low = line.lower()
        if any(stop in low for stop in stops):
            break
        collected.append(line)
        if sum(len(x) for x in collected) > 8000:
            break
    return clean_text(' '.join(collected))


def find_section_indices(lines, title):
    item_idx = None
    details_idx = None
    title_idx = None
    whats_idx = None
    for i, line in enumerate(lines):
        low = line.lower()
        if item_idx is None and re.search(r'item\s*#\s*\d+', line, re.I):
            item_idx = i + 1
        if details_idx is None and low == 'details':
            details_idx = i + 1
        if title_idx is None and title and clean_text(line).lower() == clean_text(title).lower():
            title_idx = i + 1
        if whats_idx is None and (
            "what's included" in low or 'what’s included' in low or
            'depend fresh protection:' in low or 'poise daily liners:' in low or 'depend xl washcloths:' in low or
            'poise overnight incontinence pads:' in low or 'kleenex trusted care:' in low or 'depend xl washcloths:' in low
        ):
            whats_idx = i
    return item_idx, details_idx, title_idx, whats_idx


def extract_line_based_sections(visible_text, title):
    lines = lines_from_visible_text(visible_text)
    item_idx, details_idx, title_idx, whats_idx = find_section_indices(lines, title)
    item_section = section_from_lines(lines, item_idx) if item_idx is not None else ''
    details_section = section_from_lines(lines, details_idx) if details_idx is not None else ''
    title_section = section_from_lines(lines, title_idx) if title_idx is not None else ''
    whats_section = section_from_lines(lines, whats_idx) if whats_idx is not None else ''
    if (not item_section or has_nav_junk(item_section) or looks_like_short_title(item_section)) and details_section:
        item_section = details_section
    return {
        'lines': lines,
        'item_section': item_section,
        'details_section': details_section,
        'title_section': title_section,
        'whats_section': whats_section,
    }


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

# ============================================================
# RAW EXTRACTION THEORIES
# ============================================================
def description_first_n_sentences(section, n=3):
    sents = split_sentences(cleanup_frontmatter(section))
    return clean_text(' '.join(sents[:n])) if sents else ''


def description_before_headings(section):
    txt = cleanup_frontmatter(section)
    if not txt:
        return ''
    m = re.search(r'(.+?)(?=(?:WHAT\'?S INCLUDED|WHAT’S INCLUDED|[A-Z][A-Z0-9\'’/&\- ]{4,}\s*[—:-]))', txt, re.S)
    if m:
        return clean_text(m.group(1))
    return description_first_n_sentences(txt, 3)


def extract_heading_features(section):
    txt = clean_text(section)
    if not txt:
        return []
    out = []
    for m in re.finditer(r'([A-Z0-9][A-Z0-9\'’/&\- ]{3,60})\s*[—:-]\s*(.+?)(?=(?:[A-Z0-9][A-Z0-9\'’/&\- ]{3,60}\s*[—:-])|$)', txt, re.S):
        heading = clean_text(m.group(1))
        body = clean_text(m.group(2))
        out.append(f'{heading} — {body}')
    return clean_feature_list(out)


def extract_sentence_features(section):
    sents = split_sentences(cleanup_frontmatter(section))
    out = []
    for sent in sents:
        if len(sent) >= 40 and len(sent.split()) >= 6 and not looks_like_short_title(sent):
            out.append(sent)
    return clean_feature_list(out)


def extract_whats_features(section):
    txt = clean_text(section)
    if not txt:
        return []
    out = []
    patterns = [
        r"WHAT\'?S INCLUDED\s*[—:-]\s*(.+?)(?=(?:[A-Z][A-Z0-9 '&/\-]{4,}\s*[—:-])|$)",
        r"WHAT’S INCLUDED\s*[—:-]\s*(.+?)(?=(?:[A-Z][A-Z0-9 '&/\-]{4,}\s*[—:-])|$)",
    ]
    for pat in patterns:
        m = re.search(pat, txt, re.S)
        if m:
            out.append("WHAT'S INCLUDED — " + clean_text(m.group(1)))
    out.extend(extract_heading_features(txt))
    return clean_feature_list(out)


def extract_colon_features(section):
    parts = re.split(r'\s{2,}|\n+', str(section or ''))
    out = []
    for part in parts:
        part = clean_text(part)
        if len(part) >= 20 and (':' in part or '—' in part or ' - ' in part):
            out.append(part)
    return clean_feature_list(out)

# ============================================================
# COLLECT ALL THEORIES
# ============================================================
def collect_theories(title, html_text):
    raw_text = get_nextjs_chunks(html_text)
    data_map = build_data_map(raw_text)
    visible_text = get_visible_text(html_text)
    sections = extract_line_based_sections(visible_text, title)

    vendor_objects = find_vendor_objects(raw_text, data_map)
    meta_desc = extract_meta_description(html_text)
    jsonld_desc = extract_jsonld_description(html_text)

    desc = {}
    feat = {}

    for i, obj in enumerate(vendor_objects[:5], start=1):
        desc[f'vendor_desc_{i}'] = obj.get('desc', '')
        feat[f'vendor_features_{i}'] = obj.get('features', [])

    desc['meta_desc'] = meta_desc
    desc['jsonld_desc'] = jsonld_desc
    desc['visible_details_2sent'] = description_first_n_sentences(sections['details_section'], 2)
    desc['visible_details_3sent'] = description_first_n_sentences(sections['details_section'], 3)
    desc['visible_details_before_headings'] = description_before_headings(sections['details_section'])
    desc['visible_item_2sent'] = description_first_n_sentences(sections['item_section'], 2)
    desc['visible_item_3sent'] = description_first_n_sentences(sections['item_section'], 3)
    desc['visible_item_before_headings'] = description_before_headings(sections['item_section'])
    desc['visible_whats_2sent'] = description_first_n_sentences(sections['whats_section'], 2)

    feat['visible_details_whats'] = extract_whats_features(sections['details_section'])
    feat['visible_details_headings'] = extract_heading_features(sections['details_section'])
    feat['visible_details_sentences'] = extract_sentence_features(sections['details_section'])
    feat['visible_details_colon'] = extract_colon_features(sections['details_section'])
    feat['visible_item_whats'] = extract_whats_features(sections['item_section'])
    feat['visible_item_headings'] = extract_heading_features(sections['item_section'])
    feat['visible_item_sentences'] = extract_sentence_features(sections['item_section'])
    feat['visible_item_colon'] = extract_colon_features(sections['item_section'])
    feat['visible_whats_section'] = extract_whats_features(sections['whats_section'])

    normalized_raw = raw_text.replace('\\"', '"')
    raw_vendor_desc = window_around_marker(normalized_raw, 'vendorDetailsParagraph')
    raw_vendor_feat = window_around_marker(normalized_raw, 'vendorDetailsBullets')
    raw_details = window_around_marker(visible_text, 'Details')
    raw_item = window_around_marker(visible_text, 'Item #')
    raw_whats = window_around_marker(visible_text, "WHAT'S INCLUDED") or window_around_marker(visible_text, 'WHAT’S INCLUDED')
    raw_title = window_around_marker(visible_text, title) if title else ''

    raw_desc_window = format_labeled_blocks([
        ('RAW vendorDetailsParagraph', raw_vendor_desc[:4500]),
        ('VISIBLE around Details', raw_details[:4500]),
        ('VISIBLE around Item #', raw_item[:4500]),
        ('VISIBLE around title', raw_title[:3500]),
        ('VISIBLE details section', sections['details_section'][:4500]),
        ('VISIBLE item section', sections['item_section'][:4500]),
    ])

    raw_feat_window = format_labeled_blocks([
        ('RAW vendorDetailsBullets', raw_vendor_feat[:4500]),
        ('VISIBLE around WHAT\'S INCLUDED', raw_whats[:4500]),
        ('VISIBLE around Details', raw_details[:4500]),
        ('VISIBLE details section', sections['details_section'][:4500]),
        ('VISIBLE item section', sections['item_section'][:4500]),
        ('VISIBLE whats section', sections['whats_section'][:4500]),
    ])

    return {
        'raw_text': raw_text,
        'data_map': data_map,
        'visible_text': visible_text,
        'sections': sections,
        'vendor_objects': vendor_objects,
        'desc': desc,
        'feat': feat,
        'raw_desc_window': raw_desc_window,
        'raw_feat_window': raw_feat_window,
        'raw_vendor_desc': raw_vendor_desc[:5000],
        'raw_vendor_feat': raw_vendor_feat[:5000],
        'raw_details': raw_details[:5000],
        'raw_item': raw_item[:5000],
        'raw_whats': raw_whats[:5000],
        'raw_title': raw_title[:5000],
    }

# ============================================================
# BEST GUESS PICKERS
# ============================================================
def choose_best_desc(desc_map):
    priority = [
        'vendor_desc_1', 'vendor_desc_2', 'vendor_desc_3',
        'visible_details_before_headings', 'visible_details_3sent', 'visible_details_2sent',
        'visible_item_before_headings', 'visible_item_3sent', 'visible_item_2sent',
        'jsonld_desc', 'meta_desc'
    ]
    for key in priority:
        txt = cleanup_frontmatter(desc_map.get(key, ''))
        if not txt:
            continue
        if has_nav_junk(txt):
            continue
        if key == 'meta_desc' and looks_like_promo_meta(txt):
            continue
        if key == 'jsonld_desc' and looks_like_short_title(txt):
            continue
        if len(txt) >= 90:
            return key, txt
    for key in priority:
        txt = cleanup_frontmatter(desc_map.get(key, ''))
        if txt and not has_nav_junk(txt):
            return key, txt
    return '', ''


def choose_best_feat(feat_map):
    priority = [
        'visible_details_whats', 'visible_item_whats', 'visible_whats_section',
        'visible_details_headings', 'visible_item_headings',
        'visible_details_sentences', 'visible_item_sentences',
        'vendor_features_1', 'vendor_features_2', 'vendor_features_3',
        'visible_details_colon', 'visible_item_colon'
    ]
    for key in priority:
        vals = clean_feature_list(feat_map.get(key, []) if isinstance(feat_map.get(key, []), list) else [])
        if len(vals) >= 3:
            return key, vals
    for key in priority:
        vals = clean_feature_list(feat_map.get(key, []) if isinstance(feat_map.get(key, []), list) else [])
        if vals:
            return key, vals
    return '', []

# ============================================================
# PER-ROW PROCESSING
# ============================================================
def process_row(row):
    retail_url = row.get('retail_url', '')
    salsify_url = row.get('salsify_url', '')
    sku = row.get('sku', '')
    cvs_rpc = row.get('cvs_rpc') or row.get('CVS RPC') or ''
    salsify_title = clean_text(row.get('salsify_title', ''))

    html_text = get_html(retail_url)
    bundle = collect_theories(salsify_title, html_text)

    best_desc_strategy, best_desc = choose_best_desc(bundle['desc'])
    best_feat_strategy, best_feats = choose_best_feat(bundle['feat'])

    sections = bundle['sections']

    summary_row = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Salsify URL': salsify_url,
        'Retail URL': retail_url,
        'Best Description Strategy': best_desc_strategy,
        'Best Description': best_desc,
        'Best Feature Strategy': best_feat_strategy,
        'Best Feature Count': len(best_feats),
        'Best Features': ' | '.join(best_feats),
        'Has NextF': 'self.__next_f.push([1,' in html_text,
        'Has vendorDetailsBullets Token': 'vendorDetailsBullets' in bundle['raw_text'],
        'Has vendorDetailsParagraph Token': 'vendorDetailsParagraph' in bundle['raw_text'],
        'Raw Text Length': len(bundle['raw_text']),
        'Data Map Key Count': len(bundle['data_map']),
        'Visible Item Section Preview': clean_text(sections['item_section'][:1200]),
        'Visible Details Section Preview': clean_text(sections['details_section'][:1200]),
        'Visible Title Section Preview': clean_text(sections['title_section'][:1200]),
        'Visible Whats Included Preview': clean_text(sections['whats_section'][:1200]),
    }

    strategy_row = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Salsify URL': salsify_url,
        'Retail URL': retail_url,
        'desc_meta': bundle['desc'].get('meta_desc', ''),
        'desc_jsonld': bundle['desc'].get('jsonld_desc', ''),
        'desc_vendor_1': bundle['desc'].get('vendor_desc_1', ''),
        'desc_vendor_2': bundle['desc'].get('vendor_desc_2', ''),
        'desc_vendor_3': bundle['desc'].get('vendor_desc_3', ''),
        'desc_visible_details_before_headings': bundle['desc'].get('visible_details_before_headings', ''),
        'desc_visible_details_3sent': bundle['desc'].get('visible_details_3sent', ''),
        'desc_visible_item_before_headings': bundle['desc'].get('visible_item_before_headings', ''),
        'desc_visible_item_3sent': bundle['desc'].get('visible_item_3sent', ''),
        'feat_vendor_1': ' | '.join(bundle['feat'].get('vendor_features_1', [])),
        'feat_vendor_2': ' | '.join(bundle['feat'].get('vendor_features_2', [])),
        'feat_vendor_3': ' | '.join(bundle['feat'].get('vendor_features_3', [])),
        'feat_visible_details_whats': ' | '.join(bundle['feat'].get('visible_details_whats', [])),
        'feat_visible_details_headings': ' | '.join(bundle['feat'].get('visible_details_headings', [])),
        'feat_visible_details_sentences': ' | '.join(bundle['feat'].get('visible_details_sentences', [])),
        'feat_visible_details_colon': ' | '.join(bundle['feat'].get('visible_details_colon', [])),
        'feat_visible_item_whats': ' | '.join(bundle['feat'].get('visible_item_whats', [])),
        'feat_visible_item_headings': ' | '.join(bundle['feat'].get('visible_item_headings', [])),
        'feat_visible_item_sentences': ' | '.join(bundle['feat'].get('visible_item_sentences', [])),
        'feat_visible_item_colon': ' | '.join(bundle['feat'].get('visible_item_colon', [])),
        'feat_visible_whats_section': ' | '.join(bundle['feat'].get('visible_whats_section', [])),
    }

    raw_row = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Salsify URL': salsify_url,
        'Retail URL': retail_url,
        'raw_desc_source_window': bundle['raw_desc_window'],
        'raw_feature_source_window': bundle['raw_feat_window'],
        'raw_vendor_desc_window': bundle['raw_vendor_desc'],
        'raw_vendor_feature_window': bundle['raw_vendor_feat'],
        'raw_details_window': bundle['raw_details'],
        'raw_item_window': bundle['raw_item'],
        'raw_whats_window': bundle['raw_whats'],
        'raw_title_window': bundle['raw_title'],
        'visible_details_section_raw': sections['details_section'][:6000],
        'visible_item_section_raw': sections['item_section'][:6000],
        'visible_whats_section_raw': sections['whats_section'][:6000],
        'visible_title_section_raw': sections['title_section'][:6000],
    }

    vendor_debug = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Retail URL': retail_url,
        'vendor_1_source': bundle['vendor_objects'][0]['source'] if len(bundle['vendor_objects']) > 0 else '',
        'vendor_1_bullets_ref': bundle['vendor_objects'][0]['bullets_ref'] if len(bundle['vendor_objects']) > 0 else '',
        'vendor_1_para_ref': bundle['vendor_objects'][0]['para_ref'] if len(bundle['vendor_objects']) > 0 else '',
        'vendor_1_desc': bundle['vendor_objects'][0]['desc'] if len(bundle['vendor_objects']) > 0 else '',
        'vendor_1_features': ' | '.join(bundle['vendor_objects'][0]['features']) if len(bundle['vendor_objects']) > 0 else '',
        'vendor_1_preview': bundle['vendor_objects'][0]['preview'][:5000] if len(bundle['vendor_objects']) > 0 else '',
        'vendor_1_resolved_bullets_raw': bundle['vendor_objects'][0]['resolved_bullets_raw'] if len(bundle['vendor_objects']) > 0 else '',
        'vendor_1_resolved_para_raw': bundle['vendor_objects'][0]['resolved_para_raw'] if len(bundle['vendor_objects']) > 0 else '',
    }

    marker_debug = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Retail URL': retail_url,
        'Has vendorDetailsParagraph Token': 'vendorDetailsParagraph' in bundle['raw_text'],
        'Has vendorDetailsBullets Token': 'vendorDetailsBullets' in bundle['raw_text'],
        'Has Details marker': 'Details' in bundle['visible_text'],
        'Has Item marker': 'Item #' in bundle['visible_text'],
        'Has What Included marker': "WHAT'S INCLUDED" in bundle['visible_text'] or 'WHAT’S INCLUDED' in bundle['visible_text'],
        'Details Preview': clean_text(sections['details_section'][:1000]),
        'Whats Preview': clean_text(sections['whats_section'][:1000]),
    }

    return summary_row, strategy_row, raw_row, vendor_debug, marker_debug

# ============================================================
# MAIN
# ============================================================
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
        st.error(f'Missing required columns: {missing}')
        st.write(list(df.columns))
        st.stop()

    if st.button('Run CVS raw source area debug v4.3'):
        progress = st.progress(0)
        status = st.empty()

        summary_rows = []
        strategy_rows = []
        raw_rows = []
        vendor_rows = []
        marker_rows = []

        total = len(df)
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(process_row, row.to_dict()) for _, row in df.iterrows()]
            for i, fut in enumerate(as_completed(futures), start=1):
                try:
                    summary_row, strategy_row, raw_row, vendor_row, marker_row = fut.result()
                    summary_rows.append(summary_row)
                    strategy_rows.append(strategy_row)
                    raw_rows.append(raw_row)
                    vendor_rows.append(vendor_row)
                    marker_rows.append(marker_row)
                except Exception:
                    pass
                progress.progress(i / total)
                status.write(f'Processed {i}/{total}')

        if summary_rows:
            summary_df = pd.DataFrame(summary_rows)
            strategies_df = pd.DataFrame(strategy_rows)
            raw_df = pd.DataFrame(raw_rows)
            vendor_df = pd.DataFrame(vendor_rows)
            marker_df = pd.DataFrame(marker_rows)

            file_name = 'pdp_qa_tool_v4_3_output.xlsx'
            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                summary_df.to_excel(writer, index=False, sheet_name='Summary')
                strategies_df.to_excel(writer, index=False, sheet_name='Strategy Matrix')
                raw_df.to_excel(writer, index=False, sheet_name='Raw Windows')
                vendor_df.to_excel(writer, index=False, sheet_name='Vendor Debug')
                marker_df.to_excel(writer, index=False, sheet_name='Marker Debug')

            with open(file_name, 'rb') as f:
                st.success('Done.')
                st.download_button(
                    'Download v4.3 Excel output',
                    data=f,
                    file_name=file_name,
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
