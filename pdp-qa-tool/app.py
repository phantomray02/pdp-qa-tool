
import re
import html
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

requests.adapters.DEFAULT_RETRIES = 2
st.set_page_config(layout="wide")
st.title("PDP QA Tool v4.5.1 — CVS Pro Debugger")
st.caption("Patched build. Restores meta/JSON-LD helper functions, keeps multi-theory scoring, logs reject reasons, and exports a full option matrix.")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
html_cache = {}
MAX_CACHE = 100

# ==========================================================
# HTTP.
# ==========================================================
def get_html(url: str) -> str:
    if not url:
        return ""
    if url in html_cache:
        html_cache[url] = html_cache.pop(url)
        return html_cache[url]
    try:
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Connection": "keep-alive"},
            timeout=18,
        )
        if response.status_code == 200:
            html_cache[url] = response.text
            while len(html_cache) > MAX_CACHE:
                html_cache.pop(next(iter(html_cache)))
            return response.text
    except Exception:
        pass
    return ""

# ==========================================================
# Text helpers.
# ==========================================================
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


def dedupe_keep_order(values):
    seen = set()
    out = []
    for value in values:
        vv = clean_text(value)
        key = vv.lower()
        if vv and key not in seen:
            seen.add(key)
            out.append(vv)
    return out


def lines_from_visible_text(text):
    return [clean_text(x) for x in re.split(r'\n+', str(text or '')) if clean_text(x)]


def split_sentences(text):
    txt = clean_text(text)
    if not txt:
        return []
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', txt)
    return [clean_text(x) for x in parts if clean_text(x)]


def normalize_spacing(text):
    return clean_text(text)


def has_nav_junk(text):
    txt = clean_text(text).lower()
    junk = [
        'skip to main content', 'cvs pharmacy', 'weekly ad', 'extra big deals', 'manage prescriptions',
        'schedule a vaccine', 'photo coupons', 'carepass', 'sign in account', 'use the cvs app',
        'how to get it', 'rating & reviews', 'extrabucks rewards', 'search cvs', 'same-day delivery policies',
        'shipping restrictions', 'summer manage prescriptions', 'delivery details', 'explore more at cvs.com'
    ]
    return any(token in txt for token in junk)


def is_promo_meta(text):
    txt = clean_text(text).lower()
    promo_markers = ['buy ', 'free shipping', 'shop cvs now', 'best deals', 'coupon', 'coupons', 'most orders']
    return any(token in txt for token in promo_markers)


def is_truncated_meta(text):
    txt = clean_text(text).lower()
    if not txt:
        return False
    bad_starts = ['st orders.', 'free shipping on most orders.', 'shop cvs now to see coupons']
    return any(txt.startswith(x) for x in bad_starts)


def has_rebate_copy(text):
    txt = clean_text(text).lower()
    markers = ['purchase by', 'postmarked', 'original receipt', 'restrictions apply', 'limit 1 per household', 'mail in by']
    return any(marker in txt for marker in markers)


def has_eligibility_copy(text):
    txt = clean_text(text).lower()
    markers = ['hsa/fsa', 'check with your provider', 'fsa-eligible', 'hsa-eligible']
    return any(marker in txt for marker in markers)


def cleanup_frontmatter(text):
    txt = clean_text(text)
    if not txt:
        return ''
    txt = re.sub(r'^.*?\bItem\s*#\s*\d+\s*', '', txt)
    txt = re.sub(r'^[A-Z0-9][A-Za-z0-9\-\+&/,()\'’ ]{6,200}\s+\d+\s*(?:Ct|Sht|Boxes?|Rolls?|Packs?|CT)\b[^A-Z]{0,35}', '', txt)
    txt = re.sub(r'^\d+\s*(?:Ct|Sht|Boxes?|Rolls?|Packs?|CT)\b\s*,?\s*\d*\.?\d*\s*(?:lbs?|oz)?\.?\s*', '', txt, flags=re.I)
    return clean_text(txt)


def looks_like_title_only(text):
    txt = clean_text(text)
    if not txt:
        return True
    return len(txt) <= 120 and txt.count('.') <= 1


def clean_feature_line(text):
    txt = clean_text(text)
    if not txt:
        return ''
    txt = txt.replace('|', ' ')
    txt = re.sub(r'\s+', ' ', txt).strip(' -;:')
    if has_nav_junk(txt):
        return ''
    return txt


def clean_feature_list(values, keep_eligibility=False, keep_rebates=False):
    out = []
    for value in values:
        vv = clean_feature_line(value)
        if not vv or len(vv) < 20:
            continue
        if not keep_eligibility and has_eligibility_copy(vv):
            continue
        if not keep_rebates and has_rebate_copy(vv):
            continue
        out.append(vv)
    return dedupe_keep_order(out)[:8]


def feature_penalty_score(text):
    score = 0
    if has_eligibility_copy(text):
        score -= 3
    if has_rebate_copy(text):
        score -= 5
    if 'packaging may vary' in clean_text(text).lower():
        score -= 1
    return score


def format_labeled_blocks(blocks):
    out = []
    for label, txt in blocks:
        if txt:
            out.append(f'[{label}]\n{txt}')
    return '\n\n'.join(out)

# ==========================================================
# Meta / JSON-LD helpers. Restored in v4.5.1.
# ==========================================================
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

# ==========================================================
# Next.js raw helpers.
# ==========================================================
def get_nextjs_chunks(html_text):
    matches = re.findall(r'self\.__next_f\.push\(\[1,(.*?)\]\)', html_text or '', re.DOTALL)
    chunks = []
    for match in matches:
        text = match.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        chunks.append(text)
    return '\n'.join(chunks)


def try_parse_jsonish(val):
    if not isinstance(val, str):
        return None
    vv = val.strip()
    if not vv:
        return None
    candidates = [vv, vv.replace('\\"', '"'), html.unescape(vv).replace('\\"', '"')]
    for candidate in candidates:
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
        match = is_key_start(i)
        if not match:
            i += 1
            continue
        key = match.group(1)
        val_start = i + len(key) + 1
        j = val_start
        depth = 0
        in_str = False
        esc = False
        while j < n:
            ch = raw_text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
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
    match = re.search(rf'{re.escape(str(target_key))}:', raw_text)
    if not match:
        return None
    start = match.end()
    n = len(raw_text)
    j = start
    depth = 0
    in_str = False
    esc = False

    def is_key_start(pos):
        return re.match(r'([0-9a-zA-Z]{1,3}):(?=[\[\{T"])', raw_text[pos:])

    while j < n:
        ch = raw_text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
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


def around(text, marker, back=400, forward=5000):
    if not text or not marker:
        return ''
    match = re.search(re.escape(marker), text)
    if not match:
        return ''
    start = max(0, match.start() - back)
    end = min(len(text), match.end() + forward)
    return text[start:end]


def find_vendor_objects(raw_text, data_map):
    normalized = raw_text.replace('\\"', '"')
    patterns = [
        r'\{\s*"vendorDetailsBullets"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*,\s*"vendorDetailsParagraph"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*\}',
        r'vendorDetailsBullets"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*,\s*"vendorDetailsParagraph"\s*:\s*"(\$[0-9a-zA-Z]+)"'
    ]
    candidates = []
    for value in data_map.values():
        if isinstance(value, dict) and 'vendorDetailsBullets' in value and 'vendorDetailsParagraph' in value:
            candidates.append((value.get('vendorDetailsBullets'), value.get('vendorDetailsParagraph'), 'data_map', ''))
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            bullets_ref, para_ref = match.group(1), match.group(2)
            preview = normalized[max(0, match.start() - 300):min(len(normalized), match.end() + 3200)]
            candidates.append((bullets_ref, para_ref, 'vendor_regex', preview))
    seen = set()
    out = []
    for bullets_ref, para_ref, source, preview in candidates:
        key = (bullets_ref, para_ref)
        if key in seen:
            continue
        seen.add(key)
        bullets = resolve_ref_any(raw_text, data_map, bullets_ref)
        para = resolve_ref_any(raw_text, data_map, para_ref)
        out.append({
            'source': source,
            'bullets_ref': bullets_ref or '',
            'para_ref': para_ref or '',
            'desc': clean_text(para) if isinstance(para, str) else '',
            'features': clean_feature_list([clean_text(x) for x in bullets] if isinstance(bullets, list) else [], keep_eligibility=True, keep_rebates=True),
            'preview': preview[:8000],
            'resolved_bullets_raw': json.dumps(bullets, ensure_ascii=False)[:8000] if bullets is not None else '',
            'resolved_para_raw': str(para)[:8000] if para is not None else '',
        })
    return out

# ==========================================================
# Visible page sections.
# ==========================================================
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
        if any(stop in line.lower() for stop in stops):
            break
        collected.append(line)
        if sum(len(x) for x in collected) > 16000:
            break
    return clean_text(' '.join(collected))


def extract_sections(visible_text, title):
    lines = lines_from_visible_text(visible_text)
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
        if whats_idx is None:
            if "what's included" in low or 'what’s included' in low:
                whats_idx = i
            elif re.search(r'^[A-Z][A-Z0-9\'’/&\- ]{5,}:', line):
                whats_idx = i
            elif re.search(r'^[A-Z][A-Z0-9\'’/&\- ]{5,}\s*[—:-]', line):
                whats_idx = i
    details = section_from_lines(lines, details_idx) if details_idx is not None else ''
    item = section_from_lines(lines, item_idx) if item_idx is not None else ''
    title_section = section_from_lines(lines, title_idx) if title_idx is not None else ''
    whats = section_from_lines(lines, whats_idx) if whats_idx is not None else ''
    if (not item or has_nav_junk(item) or len(item) < 120) and details:
        item = details
    return {
        'lines': lines,
        'details': details,
        'item': item,
        'title_section': title_section,
        'whats': whats,
        'item_idx': item_idx,
        'details_idx': details_idx,
        'title_idx': title_idx,
        'whats_idx': whats_idx,
    }

# ==========================================================
# Details-area splitting theories.
# ==========================================================
def mark_heading_boundaries(text):
    txt = clean_text(text)
    if not txt:
        return ''
    txt = re.sub(r'(?<=[a-z0-9\*\)])\s+(?=(?:WHAT\'?S INCLUDED|WHAT’S INCLUDED))', ' ||| ', txt)
    txt = re.sub(r'(?<=[a-z0-9\*\)])\s+(?=(?:[A-Z][A-Z0-9\'’/&\- ]{4,}\s*[—:-]))', ' ||| ', txt)
    txt = re.sub(r'(?<=[a-z0-9\*\)])\s+(?=(?:OUR |UP TO |ALL DAY |UNDERWEAR-LIKE |ODOR CONTROL |OUTSTANDING |GENTLE FOR |HELPS IN |FRESHNESS |WETNESS |SAVE YOUR |MADE WITH |THE ORIGINAL |YOUR EVERYDAY |PLUSH TOILET |LASTS LONGER|FOR LARGE |FITS IN |HELPS REDUCE |BREAKS DOWN ))', ' ||| ', txt)
    return normalize_spacing(txt)


def split_details_area(details_text):
    txt = clean_text(details_text)
    if not txt:
        return {'cleaned': '', 'prose_a': '', 'prose_b': '', 'prose_c': '', 'heading_block_a': '', 'heading_block_b': '', 'whats_only': '', 'heading_lines_a': [], 'heading_lines_b': [], 'sentences_clean': []}

    txt_clean = cleanup_frontmatter(txt)
    txt_marked = mark_heading_boundaries(txt_clean)

    m_a = re.search(r'(.+?)(?=(?:WHAT\'?S INCLUDED|WHAT’S INCLUDED|[A-Z][A-Z0-9\'’/&\- ]{4,}\s*[—:-]))', txt_clean, re.S)
    prose_a = clean_text(m_a.group(1)) if m_a else txt_clean
    prose_b = clean_text(txt_marked.split(' ||| ')[0]) if ' ||| ' in txt_marked else prose_a
    prose_c = clean_text(' '.join(split_sentences(txt_clean)[:4]))

    heading_block_a = ''
    for token in ["WHAT'S INCLUDED", 'WHAT’S INCLUDED']:
        idx = txt_clean.find(token)
        if idx != -1:
            heading_block_a = clean_text(txt_clean[idx:])
            break
    if not heading_block_a:
        m_h = re.search(r'([A-Z][A-Z0-9\'’/&\- ]{4,}\s*[—:-].+)$', txt_clean, re.S)
        if m_h:
            heading_block_a = clean_text(m_h.group(1))

    heading_block_b = clean_text(' '.join(txt_marked.split(' ||| ')[1:])) if ' ||| ' in txt_marked else heading_block_a

    whats_only = ''
    for pat in [r'(WHAT\'?S INCLUDED\s*[—:-].+)$', r'(WHAT’S INCLUDED\s*[—:-].+)$']:
        m_w = re.search(pat, txt_clean, re.S)
        if m_w:
            whats_only = clean_text(m_w.group(1))
            break

    def parse_heading_lines(source_text):
        source = clean_text(source_text)
        if not source:
            return []
        source = mark_heading_boundaries(source)
        chunks = [clean_text(x) for x in source.split(' ||| ') if clean_text(x)]
        out = []
        for chunk in chunks:
            if re.search(r'[A-Z][A-Z0-9\'’/&\- ]{3,60}\s*[—:-]', chunk):
                out.append(chunk)
            elif chunk.upper() == chunk and len(chunk.split()) <= 12:
                out.append(chunk)
        for match in re.finditer(r'([A-Z0-9][A-Z0-9\'’/&\- ]{3,60})\s*[—:-]\s*(.+?)(?=(?:[A-Z0-9][A-Z0-9\'’/&\- ]{3,60}\s*[—:-])|$)', source, re.S):
            out.append(f"{clean_text(match.group(1))} — {clean_text(match.group(2))}")
        return clean_feature_list(out, keep_eligibility=True, keep_rebates=True)

    return {
        'cleaned': txt_clean,
        'prose_a': prose_a,
        'prose_b': prose_b,
        'prose_c': prose_c,
        'heading_block_a': heading_block_a,
        'heading_block_b': heading_block_b,
        'whats_only': whats_only,
        'heading_lines_a': parse_heading_lines(heading_block_a or txt_clean),
        'heading_lines_b': parse_heading_lines(heading_block_b or txt_marked),
        'sentences_clean': clean_feature_list([s for s in split_sentences(txt_clean) if len(s) >= 45], keep_eligibility=False, keep_rebates=False),
    }


def extract_whats_features(section):
    txt = clean_text(section)
    if not txt:
        return []
    vals = []
    for pat in [r"WHAT\'?S INCLUDED\s*[—:-]\s*(.+?)(?=(?:[A-Z][A-Z0-9 '&/\-]{4,}\s*[—:-])|$)", r"WHAT’S INCLUDED\s*[—:-]\s*(.+?)(?=(?:[A-Z][A-Z0-9 '&/\-]{4,}\s*[—:-])|$)"]:
        m = re.search(pat, txt, re.S)
        if m:
            vals.append("WHAT'S INCLUDED — " + clean_text(m.group(1)))
    return vals

# ==========================================================
# Scoring.
# ==========================================================
def evaluate_description_candidate(name, text):
    txt = cleanup_frontmatter(text)
    reasons = []
    score = 0
    if not txt:
        reasons.append('empty')
        return {'name': name, 'text': '', 'score': -999, 'reasons': '; '.join(reasons)}
    if has_nav_junk(txt):
        reasons.append('nav_junk')
        score -= 100
    if is_promo_meta(txt):
        reasons.append('promo_meta')
        score -= 120
    if is_truncated_meta(txt):
        reasons.append('truncated_meta')
        score -= 120
    if looks_like_title_only(txt):
        reasons.append('title_like')
        score -= 40
    if has_rebate_copy(txt):
        reasons.append('rebate_copy')
        score -= 8
    if has_eligibility_copy(txt):
        reasons.append('eligibility_copy')
        score -= 2
    length = len(txt)
    score += min(length // 40, 20)
    if name.startswith('details_prose'):
        score += 65
    elif name.startswith('vendor_desc'):
        score += 55
    elif name.startswith('visible_item'):
        score += 40
    elif name.startswith('visible_details'):
        score += 45
    elif name == 'jsonld_desc':
        score += 10
    elif name == 'meta_desc':
        score -= 20
    score += min(len(split_sentences(txt)) * 2, 12)
    if length >= 120:
        score += 8
    if length >= 250:
        score += 6
    return {'name': name, 'text': txt, 'score': score, 'reasons': '; '.join(reasons)}


def evaluate_feature_candidate(name, values):
    cleaned_values = clean_feature_list(values if isinstance(values, list) else [], keep_eligibility=False, keep_rebates=False)
    permissive_values = clean_feature_list(values if isinstance(values, list) else [], keep_eligibility=True, keep_rebates=True)
    reasons = []
    score = 0
    if not permissive_values:
        reasons.append('empty')
        return {'name': name, 'values': [], 'score': -999, 'reasons': '; '.join(reasons), 'values_joined': ''}
    if not cleaned_values:
        cleaned_values = permissive_values[:]
        reasons.append('only_permissive_values')
        score -= 10
    score += len(cleaned_values) * 12
    joined = ' | '.join(cleaned_values)
    score += feature_penalty_score(joined)
    if name.startswith('details_heading'):
        score += 35
    elif name.startswith('details_whats'):
        score += 28
    elif name.startswith('details_sentences'):
        score += 15
    elif name.startswith('vendor_features'):
        score += 8
    if 'WHAT' in joined.upper():
        score += 3
    if len(joined) > 200:
        score += 4
    return {'name': name, 'values': cleaned_values, 'score': score, 'reasons': '; '.join(reasons), 'values_joined': joined}

# ==========================================================
# Row processing.
# ==========================================================
def process_row(row):
    retail_url = row.get('retail_url', '')
    salsify_url = row.get('salsify_url', '')
    sku = row.get('sku', '')
    cvs_rpc = row.get('cvs_rpc') or row.get('CVS RPC') or ''
    salsify_title = clean_text(row.get('salsify_title', ''))

    html_text = get_html(retail_url)
    raw_text = get_nextjs_chunks(html_text)
    data_map = build_data_map(raw_text)
    visible_text = get_visible_text(html_text)
    sections = extract_sections(visible_text, salsify_title)
    vendor_objects = find_vendor_objects(raw_text, data_map)
    meta_desc = extract_meta_description(html_text)
    jsonld_desc = extract_jsonld_description(html_text)

    details_split = split_details_area(sections['details'])
    item_split = split_details_area(sections['item'])

    desc_candidates = {
        'meta_desc': meta_desc,
        'jsonld_desc': jsonld_desc,
        'vendor_desc_1': vendor_objects[0]['desc'] if len(vendor_objects) > 0 else '',
        'vendor_desc_2': vendor_objects[1]['desc'] if len(vendor_objects) > 1 else '',
        'vendor_desc_3': vendor_objects[2]['desc'] if len(vendor_objects) > 2 else '',
        'details_prose_a': details_split['prose_a'],
        'details_prose_b': details_split['prose_b'],
        'details_prose_c': details_split['prose_c'],
        'visible_details_3sent': ' '.join(split_sentences(details_split['cleaned'])[:3]),
        'visible_item_3sent': ' '.join(split_sentences(item_split['cleaned'])[:3]),
    }
    feat_candidates = {
        'vendor_features_1': vendor_objects[0]['features'] if len(vendor_objects) > 0 else [],
        'details_heading_lines_a': details_split['heading_lines_a'],
        'details_heading_lines_b': details_split['heading_lines_b'],
        'details_whats_lines': extract_whats_features(details_split['whats_only'] or details_split['heading_block_a']),
        'details_sentences_clean': details_split['sentences_clean'],
    }

    desc_evals = [evaluate_description_candidate(name, text) for name, text in desc_candidates.items()]
    feat_evals = [evaluate_feature_candidate(name, values) for name, values in feat_candidates.items()]
    best_desc_eval = max(desc_evals, key=lambda x: x['score'])
    best_feat_eval = max(feat_evals, key=lambda x: x['score'])

    best_desc = best_desc_eval['text'] if best_desc_eval['score'] > -80 else ''
    best_desc_strategy = best_desc_eval['name'] if best_desc else ''
    best_features = best_feat_eval['values'] if best_feat_eval['score'] > -50 else []
    best_feat_strategy = best_feat_eval['name'] if best_features else ''

    raw_vendor_desc = around(raw_text.replace('\\"', '"'), 'vendorDetailsParagraph')
    raw_vendor_feat = around(raw_text.replace('\\"', '"'), 'vendorDetailsBullets')
    raw_details = around(visible_text, 'Details')
    raw_item = around(visible_text, 'Item #')
    raw_whats = around(visible_text, "WHAT'S INCLUDED") or around(visible_text, 'WHAT’S INCLUDED')
    raw_title = around(visible_text, salsify_title) if salsify_title else ''

    summary = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Salsify URL': salsify_url,
        'Retail URL': retail_url,
        'Best Description Strategy': best_desc_strategy,
        'Best Description': best_desc,
        'Best Description Score': best_desc_eval['score'],
        'Best Description Reject Reason': best_desc_eval['reasons'],
        'Best Feature Strategy': best_feat_strategy,
        'Best Feature Count': len(best_features),
        'Best Features': ' | '.join(best_features),
        'Best Feature Score': best_feat_eval['score'],
        'Best Feature Reject Reason': best_feat_eval['reasons'],
        'Has NextF': 'self.__next_f.push([1,' in html_text,
        'Has vendorDetailsBullets Token': 'vendorDetailsBullets' in raw_text,
        'Has vendorDetailsParagraph Token': 'vendorDetailsParagraph' in raw_text,
        'Raw Text Length': len(raw_text),
        'Data Map Key Count': len(data_map),
        'Visible Item Section Preview': clean_text(sections['item'][:1200]),
        'Visible Details Section Preview': clean_text(sections['details'][:1200]),
        'Visible Title Section Preview': clean_text(sections['title_section'][:1200]),
        'Visible Whats Included Preview': clean_text(sections['whats'][:1200]),
        'Has Promo Meta': is_promo_meta(meta_desc),
        'Has Truncated Meta': is_truncated_meta(meta_desc),
    }

    matrix = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Retail URL': retail_url,
        'meta_desc': meta_desc,
        'jsonld_desc': jsonld_desc,
        'vendor_desc_1': desc_candidates['vendor_desc_1'],
        'vendor_desc_2': desc_candidates['vendor_desc_2'],
        'vendor_desc_3': desc_candidates['vendor_desc_3'],
        'details_prose_a': desc_candidates['details_prose_a'],
        'details_prose_b': desc_candidates['details_prose_b'],
        'details_prose_c': desc_candidates['details_prose_c'],
        'visible_details_3sent': desc_candidates['visible_details_3sent'],
        'visible_item_3sent': desc_candidates['visible_item_3sent'],
        'vendor_features_1': ' | '.join(feat_candidates['vendor_features_1']),
        'details_heading_lines_a': ' | '.join(feat_candidates['details_heading_lines_a']),
        'details_heading_lines_b': ' | '.join(feat_candidates['details_heading_lines_b']),
        'details_whats_lines': ' | '.join(feat_candidates['details_whats_lines']),
        'details_sentences_clean': ' | '.join(feat_candidates['details_sentences_clean']),
    }

    scorecard = {'SKU': sku, 'CVS RPC': cvs_rpc, 'Retail URL': retail_url}
    for eval_row in desc_evals:
        scorecard[f"desc_score__{eval_row['name']}"] = eval_row['score']
        scorecard[f"desc_reason__{eval_row['name']}"] = eval_row['reasons']
    for eval_row in feat_evals:
        scorecard[f"feat_score__{eval_row['name']}"] = eval_row['score']
        scorecard[f"feat_reason__{eval_row['name']}"] = eval_row['reasons']

    raw_debug = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Retail URL': retail_url,
        'raw_desc_source_window': format_labeled_blocks([
            ('RAW vendorDetailsParagraph', raw_vendor_desc[:5000]),
            ('VISIBLE around Details', raw_details[:5000]),
            ('VISIBLE around Item #', raw_item[:5000]),
            ('VISIBLE around title', raw_title[:3500]),
            ('VISIBLE details section', sections['details'][:5000]),
            ('VISIBLE item section', sections['item'][:5000]),
        ]),
        'raw_feature_source_window': format_labeled_blocks([
            ('RAW vendorDetailsBullets', raw_vendor_feat[:5000]),
            ('VISIBLE around WHAT\'S INCLUDED', raw_whats[:5000]),
            ('VISIBLE around Details', raw_details[:5000]),
            ('VISIBLE details section', sections['details'][:5000]),
            ('VISIBLE whats section', sections['whats'][:5000]),
        ]),
        'raw_vendor_desc_window': raw_vendor_desc[:9000],
        'raw_vendor_feature_window': raw_vendor_feat[:9000],
        'raw_details_window': raw_details[:9000],
        'raw_item_window': raw_item[:9000],
        'raw_whats_window': raw_whats[:9000],
        'raw_title_window': raw_title[:9000],
        'visible_details_section_raw': sections['details'][:12000],
        'visible_item_section_raw': sections['item'][:12000],
        'visible_whats_section_raw': sections['whats'][:12000],
        'visible_title_section_raw': sections['title_section'][:12000],
        'details_cleaned_block': details_split['cleaned'][:12000],
        'details_prose_block_a': details_split['prose_a'][:12000],
        'details_prose_block_b': details_split['prose_b'][:12000],
        'details_prose_block_c': details_split['prose_c'][:12000],
        'details_headings_block_a': details_split['heading_block_a'][:12000],
        'details_headings_block_b': details_split['heading_block_b'][:12000],
        'details_whats_only_block': details_split['whats_only'][:12000],
        'details_heading_only_lines_a': ' | '.join(details_split['heading_lines_a'])[:12000],
        'details_heading_only_lines_b': ' | '.join(details_split['heading_lines_b'])[:12000],
        'details_sentence_only_lines': ' | '.join(details_split['sentences_clean'])[:12000],
    }

    vendor_debug = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Retail URL': retail_url,
        'vendor_1_source': vendor_objects[0]['source'] if len(vendor_objects) > 0 else '',
        'vendor_1_bullets_ref': vendor_objects[0]['bullets_ref'] if len(vendor_objects) > 0 else '',
        'vendor_1_para_ref': vendor_objects[0]['para_ref'] if len(vendor_objects) > 0 else '',
        'vendor_1_desc': vendor_objects[0]['desc'] if len(vendor_objects) > 0 else '',
        'vendor_1_features': ' | '.join(vendor_objects[0]['features']) if len(vendor_objects) > 0 else '',
        'vendor_1_preview': vendor_objects[0]['preview'] if len(vendor_objects) > 0 else '',
        'vendor_1_resolved_bullets_raw': vendor_objects[0]['resolved_bullets_raw'] if len(vendor_objects) > 0 else '',
        'vendor_1_resolved_para_raw': vendor_objects[0]['resolved_para_raw'] if len(vendor_objects) > 0 else '',
    }

    marker = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Retail URL': retail_url,
        'Has vendorDetailsParagraph Token': 'vendorDetailsParagraph' in raw_text,
        'Has vendorDetailsBullets Token': 'vendorDetailsBullets' in raw_text,
        'Has Details marker': 'Details' in visible_text,
        'Has Item marker': 'Item #' in visible_text,
        'Has What Included marker': "WHAT'S INCLUDED" in visible_text or 'WHAT’s INCLUDED' in visible_text or 'WHAT’S INCLUDED' in visible_text,
        'Details start idx': sections['details_idx'],
        'Item start idx': sections['item_idx'],
        'Whats start idx': sections['whats_idx'],
        'Title start idx': sections['title_idx'],
        'Details Preview': clean_text(sections['details'][:1000]),
        'Whats Preview': clean_text(sections['whats'][:1000]),
    }
    return summary, matrix, scorecard, raw_debug, vendor_debug, marker

# ==========================================================
# MAIN.
# ==========================================================
if uploaded_file:
    df = pd.read_csv(uploaded_file)
    df.columns = [c.strip().lower() for c in df.columns]
    df.rename(columns={
        'salsify url': 'salsify_url',
        'retail url': 'retail_url',
        'sku id': 'sku',
        'product sku': 'sku',
        'cvs rpc': 'cvs_rpc',
        'title': 'salsify_title',
        'salsify title': 'salsify_title',
    }, inplace=True)
    if 'salsify_title' not in df.columns:
        df['salsify_title'] = ''
    required = ['sku', 'salsify_url', 'retail_url']
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f'Missing required columns: {missing}')
        st.write(list(df.columns))
        st.stop()

    if st.button('Run CVS pro debugger v4.5.1'):
        progress = st.progress(0)
        status = st.empty()
        summary_rows = []
        matrix_rows = []
        score_rows = []
        raw_rows = []
        vendor_rows = []
        marker_rows = []
        error_rows = []
        total = len(df)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            future_map = {}
            for _, row in df.iterrows():
                row_dict = row.to_dict()
                fut = executor.submit(process_row, row_dict)
                futures.append(fut)
                future_map[fut] = row_dict
            for i, fut in enumerate(as_completed(futures), start=1):
                row_dict = future_map[fut]
                try:
                    summary, matrix, scorecard, raw_debug, vendor_debug, marker = fut.result()
                    summary_rows.append(summary)
                    matrix_rows.append(matrix)
                    score_rows.append(scorecard)
                    raw_rows.append(raw_debug)
                    vendor_rows.append(vendor_debug)
                    marker_rows.append(marker)
                except Exception as exc:
                    error_rows.append({
                        'sku': row_dict.get('sku', ''),
                        'retail_url': row_dict.get('retail_url', ''),
                        'salsify_url': row_dict.get('salsify_url', ''),
                        'error_type': type(exc).__name__,
                        'error_message': str(exc),
                    })
                progress.progress(i / total)
                status.write(f'Processed {i}/{total} | Success: {len(summary_rows)} | Errors: {len(error_rows)}')

        file_name = 'pdp_qa_tool_v4_5_1_output.xlsx'
        try:
            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                pd.DataFrame(summary_rows).to_excel(writer, index=False, sheet_name='Summary')
                pd.DataFrame(matrix_rows).to_excel(writer, index=False, sheet_name='Option Matrix')
                pd.DataFrame(score_rows).to_excel(writer, index=False, sheet_name='Scorecard')
                pd.DataFrame(raw_rows).to_excel(writer, index=False, sheet_name='Raw Windows')
                pd.DataFrame(vendor_rows).to_excel(writer, index=False, sheet_name='Vendor Debug')
                pd.DataFrame(marker_rows).to_excel(writer, index=False, sheet_name='Marker Debug')
                pd.DataFrame(error_rows).to_excel(writer, index=False, sheet_name='Errors')
        except Exception as exc:
            st.error(f'Excel write failed: {type(exc).__name__}: {exc}')
            st.stop()

        if Path(file_name).exists():
            st.success(f'Done. Success rows: {len(summary_rows)}. Error rows: {len(error_rows)}.')
            with open(file_name, 'rb') as f:
                st.download_button(
                    'Download v4.5.1 Excel output',
                    data=f,
                    file_name=file_name,
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
        else:
            st.error('The output file was not created.')
