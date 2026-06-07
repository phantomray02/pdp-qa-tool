
import re
import html
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

requests.adapters.DEFAULT_RETRIES = 2
st.set_page_config(layout="wide")
st.title("PDP QA Tool v4.7 — CVS Source-Anchor Parser")
st.caption("Anchor-based parser. Pulls raw source blocks, applies family-specific start/end anchors, blocks JSON/eligibility/rebate junk, and inherits proven family routes without backtracking.")

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
    text = text.replace("\\u0026", "&").replace("\\n", " ").replace("\\/", "/").replace('\\"', '"')
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


def split_sentences(text):
    txt = clean_text(text)
    if not txt:
        return []
    return [clean_text(x) for x in re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', txt) if clean_text(x)]


def lines_from_visible_text(text):
    return [clean_text(x) for x in re.split(r'\n+', str(text or '')) if clean_text(x)]


def is_title_like(text):
    txt = clean_text(text)
    if not txt:
        return True
    if len(txt) <= 115 and len(split_sentences(txt)) <= 1:
        return True
    return False


def has_nav_junk(text):
    txt = clean_text(text).lower()
    junk = [
        'skip to main content', 'cvs pharmacy', 'weekly ad', 'extra big deals', 'manage prescriptions',
        'schedule a vaccine', 'photo coupons', 'carepass', 'sign in account', 'use the cvs app',
        'how to get it', 'rating & reviews', 'extrabucks rewards', 'search cvs', 'same-day delivery policies',
        'shipping restrictions', 'summer manage prescriptions', 'delivery details', 'explore more at cvs.com',
        'additional resources', 'check your state\'s eligibility', 'federal law and some state laws requires cvs',
        'select the state on your driver\'s license or state id', 'customer reviews for', 'see all '
    ]
    return any(token in txt for token in junk)


def is_promo_meta(text):
    txt = clean_text(text).lower()
    promo_markers = ['buy ', 'free shipping', 'shop cvs now', 'best deals', 'coupon', 'coupons', 'most orders']
    return any(marker in txt for marker in promo_markers)


def has_rebate_copy(text):
    txt = clean_text(text).lower()
    markers = [
        'purchase by', 'postmarked', 'original receipt', 'restrictions apply', 'limit 1 per household',
        'mail in by', 'money back', 'satisfaction guaranteed', 'requests must be postmarked', 'orig. receipt/upc'
    ]
    return any(marker in txt for marker in markers)


def has_eligibility_copy(text):
    txt = clean_text(text).lower()
    markers = ['hsa/fsa', 'check with your provider', 'fsa-eligible', 'hsa-eligible', 'eligible in the us']
    return any(marker in txt for marker in markers)


def cleanup_frontmatter(text):
    txt = clean_text(text)
    if not txt:
        return ''
    txt = re.sub(r'^.*?\bItem\s*#\s*\d+\s*', '', txt)
    txt = re.sub(r'^[A-Z0-9][A-Za-z0-9\-\+&/,()\'’ ]{6,220}\s+\d+\s*(?:Ct|Sht|Boxes?|Rolls?|Packs?|CT)\b[^A-Z]{0,40}', '', txt)
    txt = re.sub(r'^\d+\s*(?:Ct|Sht|Boxes?|Rolls?|Packs?|CT)\b\s*,?\s*\d*\.?\d*\s*(?:lbs?|oz)?\.?\s*', '', txt, flags=re.I)
    return clean_text(txt)


def cutoff_at_first_marker(text, markers):
    txt = clean_text(text)
    if not txt:
        return ''
    low = txt.lower()
    cut_positions = []
    for marker in markers:
        pos = low.find(marker.lower())
        if pos != -1:
            cut_positions.append(pos)
    if cut_positions:
        txt = txt[:min(cut_positions)].strip(' -;,.')
    return clean_text(txt)


def normalize_spacing(text):
    return clean_text(text)


def desc_is_usable(text, min_len=140):
    txt = clean_text(text)
    if not txt:
        return False
    if len(txt) < min_len:
        return False
    if is_title_like(txt):
        return False
    if has_nav_junk(txt):
        return False
    if is_promo_meta(txt):
        return False
    return True


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


def collapse_duplicate_feature_fragments(values):
    values = clean_feature_list(values, keep_eligibility=False, keep_rebates=False)
    out = []
    for v in values:
        low = v.lower()
        if not any(low in existing.lower() or existing.lower() in low for existing in out):
            out.append(v)
    return out[:8]


def format_labeled_blocks(blocks):
    out = []
    for label, txt in blocks:
        if txt:
            out.append(f'[{label}]\n{txt}')
    return '\n\n'.join(out)

# ==========================================================
# Meta / JSON-LD helpers.
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
# Next.js/raw helpers.
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
            'features': clean_feature_list([clean_text(x) for x in bullets] if isinstance(bullets, list) else [], keep_eligibility=False, keep_rebates=False),
            'preview': preview[:8000],
            'resolved_bullets_raw': json.dumps(bullets, ensure_ascii=False)[:8000] if bullets is not None else '',
            'resolved_para_raw': str(para)[:8000] if para is not None else '',
        })
    return out

# ==========================================================
# Visible page extraction.
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
        if sum(len(x) for x in collected) > 18000:
            break
    return clean_text(' '.join(collected))


def extract_sections(visible_text, title):
    lines = lines_from_visible_text(visible_text)
    item_idx = details_idx = title_idx = whats_idx = None
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
# Source-anchor parsing.
# ==========================================================
HEADING_TOKEN_RE = re.compile(r'(?:WHAT\'?S INCLUDED|WHAT’S INCLUDED|[A-Z][A-Z0-9\'’/&\- ]{4,80}\s*[—:-])')

STOP_MARKERS_GLOBAL = [
    'rating & reviews', 'ingredients', 'directions', 'warnings', 'specifications',
    'same-day delivery policies', 'shipping restrictions', 'delivery details',
    'explore more at cvs.com', 'customers also bought', 'similar products', 'you may also like'
]

DESC_TAIL_MARKERS = [
    'what\'s included', 'what’s included', 'hsa/fsa', 'check with your provider',
    'fsa-eligible', 'hsa-eligible', 'purchase by', 'mail in by', 'original receipt',
    'restrictions apply', 'limit 1 per household', 'satisfaction guaranteed',
    'additional resources', 'check your state\'s eligibility'
]

FEATURE_TAIL_MARKERS = [
    'rating & reviews', 'same-day delivery policies', 'shipping restrictions',
    'delivery details', 'explore more at cvs.com', 'additional resources'
]


def mark_heading_boundaries(text):
    txt = clean_text(text)
    if not txt:
        return ''
    txt = re.sub(r'(?<=[a-z0-9\*\)])\s+(?=(?:WHAT\'?S INCLUDED|WHAT’S INCLUDED))', ' ||| ', txt)
    txt = re.sub(r'(?<=[a-z0-9\*\)])\s+(?=(?:[A-Z][A-Z0-9\'’/&\- ]{4,80}\s*[—:-]))', ' ||| ', txt)
    txt = re.sub(r'(?<=[a-z0-9\*\)])\s+(?=(?:OUR |UP TO |ALL DAY |UNDERWEAR-LIKE |ODOR CONTROL |OUTSTANDING |GENTLE FOR |HELPS IN |FRESHNESS |WETNESS |SAVE YOUR |MADE WITH |THE ORIGINAL |YOUR EVERYDAY |PLUSH TOILET |LASTS LONGER|FOR LARGE |FITS IN |HELPS REDUCE |BREAKS DOWN |QUICK CLEAN |GET THE JOB DONE |VIRTUALLY LINT FREE |EVERYDAY CLEANING ))', ' ||| ', txt)
    return clean_text(txt)


def remove_jsonish_noise(text):
    txt = clean_text(text)
    if not txt:
        return ''
    txt = re.sub(r'\\u[0-9a-fA-F]{4}', ' ', txt)
    txt = re.sub(r'\{[^{}]{0,120}\}', ' ', txt)
    txt = re.sub(r'\[[^\[\]]{0,120}\]', ' ', txt)
    txt = re.sub(r'"[A-Za-z0-9_]+":', ' ', txt)
    txt = re.sub(r'\s+', ' ', txt).strip()
    return clean_text(txt)


def extract_description_from_block(source_text):
    txt = cleanup_frontmatter(remove_jsonish_noise(source_text))
    if not txt:
        return '', '', ''
    # Stop at heading token if present.
    heading_match = HEADING_TOKEN_RE.search(txt)
    if heading_match:
        desc = clean_text(txt[:heading_match.start()])
        if desc:
            return desc, heading_match.group(0), 'before_heading_token'
    # Else stop at known tail markers.
    cut_desc = cutoff_at_first_marker(txt, DESC_TAIL_MARKERS + STOP_MARKERS_GLOBAL)
    if cut_desc and cut_desc != txt:
        return cut_desc, 'tail_marker', 'before_tail_marker'
    return txt, '', 'full_block'


def extract_feature_block(source_text):
    txt = cleanup_frontmatter(remove_jsonish_noise(source_text))
    if not txt:
        return '', '', ''
    start_reason = ''
    start_marker = ''
    start_idx = None
    for pattern in [r"WHAT\'?S INCLUDED", r"WHAT’S INCLUDED", r"[A-Z][A-Z0-9\'’/&\- ]{4,80}\s*[—:-]"]:
        m = re.search(pattern, txt)
        if m:
            start_idx = m.start()
            start_marker = m.group(0)
            start_reason = 'heading_start'
            break
    if start_idx is None:
        return '', '', 'no_feature_start'
    block = txt[start_idx:]
    block = cutoff_at_first_marker(block, FEATURE_TAIL_MARKERS)
    return clean_text(block), start_marker, start_reason


def parse_heading_lines(source_text):
    source = clean_text(source_text)
    if not source:
        return []
    source = mark_heading_boundaries(source)
    chunks = [clean_text(x) for x in source.split(' ||| ') if clean_text(x)]
    out = []
    for chunk in chunks:
        if re.search(r'[A-Z][A-Z0-9\'’/&\- ]{3,80}\s*[—:-]', chunk):
            out.append(chunk)
        elif chunk.upper() == chunk and len(chunk.split()) <= 16:
            out.append(chunk)
    for match in re.finditer(r'([A-Z0-9][A-Z0-9\'’/&\- ]{3,80})\s*[—:-]\s*(.+?)(?=(?:[A-Z0-9][A-Z0-9\'’/&\- ]{3,80}\s*[—:-])|$)', source, re.S):
        out.append(f"{clean_text(match.group(1))} — {clean_text(match.group(2))}")
    return collapse_duplicate_feature_fragments(out)


def split_details_area(details_text):
    txt = clean_text(details_text)
    if not txt:
        return {
            'cleaned':'', 'desc_block':'', 'desc_start':'', 'desc_stop':'', 'desc_stop_reason':'',
            'feature_block':'', 'feature_start':'', 'feature_start_reason':'', 'heading_lines':[],
            'heading_lines_alt':[], 'sentence_features':[]
        }
    cleaned = cleanup_frontmatter(txt)
    desc_block, desc_stop, desc_stop_reason = extract_description_from_block(cleaned)
    feature_block, feature_start, feature_start_reason = extract_feature_block(cleaned)
    heading_lines = parse_heading_lines(feature_block)
    heading_lines_alt = parse_heading_lines(mark_heading_boundaries(cleaned))
    sentence_features = clean_feature_list([s for s in split_sentences(feature_block or cleaned) if len(s) >= 45], keep_eligibility=False, keep_rebates=False)
    desc_start = 'after_item_cleanup' if desc_block else ''
    return {
        'cleaned': cleaned,
        'desc_block': desc_block,
        'desc_start': desc_start,
        'desc_stop': desc_stop,
        'desc_stop_reason': desc_stop_reason,
        'feature_block': feature_block,
        'feature_start': feature_start,
        'feature_start_reason': feature_start_reason,
        'heading_lines': heading_lines,
        'heading_lines_alt': heading_lines_alt,
        'sentence_features': sentence_features,
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
    return collapse_duplicate_feature_fragments(vals)

# ==========================================================
# Family keys.
# ==========================================================
def infer_brand(*parts):
    blob = ' '.join([clean_text(x) for x in parts]).lower()
    for brand in ['cottonelle', 'depend', 'poise', 'kleenex', 'scott', 'viva', 'kotex', 'thinx']:
        if brand in blob:
            return brand
    return 'unknown'


def parse_retail_url_parts(url):
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        path = parsed.path or ''
        slug = path.split('/shop/')[-1] if '/shop/' in path else path.strip('/').split('/')[-1]
        slug = slug.split('-prodid-')[0]
        prodid_match = re.search(r'prodid-(\d+)', path)
        prodid = prodid_match.group(1) if prodid_match else ''
        sku_id = query.get('skuId', [''])[0]
        return {'slug': slug, 'prodid': prodid, 'sku_id': sku_id}
    except Exception:
        return {'slug': '', 'prodid': '', 'sku_id': ''}


def build_canonical_family_key(retail_url, salsify_url, family_route, visible_details, vendor_desc):
    parts = parse_retail_url_parts(retail_url)
    brand = infer_brand(retail_url, salsify_url, visible_details, vendor_desc)
    if parts['prodid']:
        return f"{brand}|{family_route}|prodid:{parts['prodid']}"
    slug = re.sub(r'-?(\d+|ct|count|mega|rolls?|wipes?|boxes?|cube|cubes|flat|long|regular|large|medium|small|xl|xxl|s\/m|l\/xl)(?:-|$)', '-', parts['slug'].lower())
    slug = re.sub(r'-{2,}', '-', slug).strip('-')
    return f"{brand}|{family_route}|slug:{slug[:80]}"

# ==========================================================
# Family routing + selection.
# ==========================================================
def classify_family(retail_url, salsify_url, visible_details, vendor_desc):
    blob = ' '.join([retail_url or '', salsify_url or '', visible_details or '', vendor_desc or '']).lower()
    if 'cottonelle' in blob or 'kleenex' in blob or 'scott-' in (salsify_url or '').lower() or 'viva-' in (salsify_url or '').lower():
        return 'details_family'
    if 'depend-guards' in blob or 'depend-shields' in blob or 'washcloth' in blob:
        return 'vendor_hybrid_family'
    if 'depend-fit-flex' in blob or 'depend-fresh-protection' in blob or 'night-defense' in blob or 'depend-silhouette' in blob or 'real-fit' in blob:
        return 'variant_detail_family'
    if 'poise' in blob:
        return 'vendor_family'
    if 'u-by-kotex' in blob or 'thinx' in blob:
        return 'details_or_item_family'
    return 'general_family'


def clean_vendor_description(text):
    txt = cleanup_frontmatter(remove_jsonish_noise(text))
    txt = cutoff_at_first_marker(txt, DESC_TAIL_MARKERS + STOP_MARKERS_GLOBAL)
    return clean_text(txt)


def choose_description(family, details_split, item_split, vendor_objs, jsonld_desc, meta_desc):
    vendor_desc = clean_vendor_description(vendor_objs[0]['desc']) if vendor_objs else ''
    details_desc = details_split['desc_block']
    item_desc = item_split['desc_block']
    details_sent = ' '.join(split_sentences(details_split['cleaned'])[:4])
    item_sent = ' '.join(split_sentences(item_split['cleaned'])[:4])
    fallback_jsonld = clean_text(jsonld_desc)
    fallback_meta = clean_text(meta_desc)
    path = ''
    desc = ''
    flags = ''
    audit = {'source_block':'', 'start_anchor':'', 'end_anchor':'', 'stop_reason':''}

    routing = {
        'details_family': [('details_desc', details_desc), ('item_desc', item_desc), ('vendor_desc_clean', vendor_desc)],
        'vendor_hybrid_family': [('details_desc', details_desc), ('vendor_desc_clean', vendor_desc), ('item_desc', item_desc)],
        'variant_detail_family': [('details_desc', details_desc), ('item_desc', item_desc), ('vendor_desc_clean', vendor_desc), ('details_sentence_block', details_sent)],
        'vendor_family': [('vendor_desc_clean', vendor_desc), ('details_desc', details_desc), ('item_desc', item_desc)],
        'details_or_item_family': [('details_desc', details_desc), ('item_desc', item_desc), ('vendor_desc_clean', vendor_desc)],
        'general_family': [('details_desc', details_desc), ('item_desc', item_desc), ('vendor_desc_clean', vendor_desc), ('details_sentence_block', details_sent), ('item_sentence_block', item_sent)],
    }

    for candidate_path, candidate in routing.get(family, routing['general_family']):
        if desc_is_usable(candidate, min_len=120 if 'sentence' in candidate_path else 140):
            path = candidate_path.replace('details_desc', 'details_prose').replace('item_desc', 'item_prose')
            desc = clean_text(candidate)
            if 'details' in candidate_path:
                audit = {
                    'source_block': 'visible_details',
                    'start_anchor': details_split['desc_start'],
                    'end_anchor': details_split['desc_stop'],
                    'stop_reason': details_split['desc_stop_reason'],
                }
            elif 'item' in candidate_path:
                audit = {
                    'source_block': 'visible_item',
                    'start_anchor': item_split['desc_start'],
                    'end_anchor': item_split['desc_stop'],
                    'stop_reason': item_split['desc_stop_reason'],
                }
            else:
                audit = {
                    'source_block': 'vendor_paragraph',
                    'start_anchor': 'vendor_start',
                    'end_anchor': 'tail_marker',
                    'stop_reason': 'vendor_cleanup',
                }
            break

    if not desc:
        if fallback_jsonld:
            path, desc, flags = 'title_only_fallback', fallback_jsonld, 'title_like_only'
            audit = {'source_block':'jsonld', 'start_anchor':'jsonld_start', 'end_anchor':'jsonld_end', 'stop_reason':'fallback'}
        elif fallback_meta:
            path, desc, flags = 'title_only_fallback', fallback_meta, 'promo_or_title_only'
            audit = {'source_block':'meta_desc', 'start_anchor':'meta_start', 'end_anchor':'meta_end', 'stop_reason':'fallback'}

    return path, clean_text(desc), flags, audit


def choose_features(family, details_split, vendor_objs):
    vendor_features = vendor_objs[0]['features'] if vendor_objs else []
    heading_a = collapse_duplicate_feature_fragments(details_split['heading_lines'])
    heading_b = collapse_duplicate_feature_fragments(details_split['heading_lines_alt'])
    whats = collapse_duplicate_feature_fragments(extract_whats_features(details_split['feature_block']))
    sentence_features = collapse_duplicate_feature_fragments(details_split['sentence_features'])
    vendor_clean = collapse_duplicate_feature_fragments(vendor_features)
    audit = {'source_block':'', 'start_anchor':'', 'end_anchor':'', 'stop_reason':''}

    preferred = ['details_heading_lines_a', heading_a, 'visible_details', details_split['feature_start'], 'tail_marker', details_split['feature_start_reason']]
    if len(heading_a) >= 3:
        return preferred[0], heading_a, '', {'source_block':'visible_details','start_anchor':details_split['feature_start'],'end_anchor':'tail_marker','stop_reason':details_split['feature_start_reason']}
    if len(heading_b) >= 3:
        return 'details_heading_lines_b', heading_b, '', {'source_block':'visible_details','start_anchor':'synthetic_heading_boundary','end_anchor':'tail_marker','stop_reason':'synthetic_heading_block'}
    if len(whats) >= 2:
        return 'details_whats_lines', whats, '', {'source_block':'visible_details','start_anchor':'WHAT\'S INCLUDED','end_anchor':'tail_marker','stop_reason':'whats_included'}
    if len(vendor_clean) >= 3:
        return 'vendor_features_clean', vendor_clean, '', {'source_block':'vendor_bullets','start_anchor':'vendorDetailsBullets','end_anchor':'vendor_end','stop_reason':'vendor_bullets'}
    if len(sentence_features) >= 3:
        return 'details_sentence_features', sentence_features, '', {'source_block':'visible_details','start_anchor':'feature_sentence_block','end_anchor':'tail_marker','stop_reason':'sentence_feature_fallback'}
    return '', [], 'no_feature_block', audit

# ==========================================================
# Inheritance without backtracking.
# ==========================================================
def desc_path_rank(path):
    order = {
        'details_prose': 1,
        'item_prose': 2,
        'details_sentence_block': 3,
        'vendor_desc_clean': 4,
        'title_only_fallback': 99,
        '': 100,
    }
    return order.get(path or '', 50)


def feat_path_rank(path):
    order = {
        'details_heading_lines_a': 1,
        'details_heading_lines_b': 2,
        'details_whats_lines': 3,
        'vendor_features_clean': 4,
        'details_sentence_features': 5,
        '': 99,
    }
    return order.get(path or '', 50)


def apply_family_inheritance(summary_df, path_df):
    if summary_df.empty:
        return summary_df, path_df

    summary_df = summary_df.copy()
    path_df = path_df.copy()

    for fam_key, idx in summary_df.groupby('Canonical Family Key').groups.items():
        group = summary_df.loc[list(idx)].copy()
        if len(group) <= 1:
            continue

        strong_desc = group[
            (group['Description Path'].isin(['details_prose', 'item_prose', 'details_sentence_block', 'vendor_desc_clean'])) &
            (group['Best Description'].fillna('').str.len() >= 140)
        ].copy()
        if not strong_desc.empty:
            strong_desc['__rank'] = strong_desc['Description Path'].map(desc_path_rank)
            strong_desc['__len'] = strong_desc['Best Description'].fillna('').str.len()
            donor = strong_desc.sort_values(['__rank', '__len'], ascending=[True, False]).iloc[0]
            for row_idx in idx:
                path_now = clean_text(summary_df.at[row_idx, 'Description Path'])
                desc_now = clean_text(summary_df.at[row_idx, 'Best Description'])
                if (path_now == 'title_only_fallback' or len(desc_now) < 120) and str(summary_df.at[row_idx, 'SKU']) != str(donor['SKU']):
                    summary_df.at[row_idx, 'Description Path'] = f"{donor['Description Path']}__inherited"
                    summary_df.at[row_idx, 'Best Description'] = donor['Best Description']
                    summary_df.at[row_idx, 'Description Flags'] = clean_text(f"{summary_df.at[row_idx, 'Description Flags']} | inherited_from_sku:{donor['SKU']}")
                    summary_df.at[row_idx, 'Inherited From SKU'] = donor['SKU']
                    summary_df.at[row_idx, 'Inherited Description'] = True
                    path_match_idx = path_df[path_df['SKU'].astype(str) == str(summary_df.at[row_idx, 'SKU'])].index
                    if len(path_match_idx):
                        pm = path_match_idx[0]
                        path_df.at[pm, 'final_description_path'] = summary_df.at[row_idx, 'Description Path']
                        path_df.at[pm, 'final_description'] = donor['Best Description']
                        path_df.at[pm, 'inherited_from_sku'] = donor['SKU']
                        path_df.at[pm, 'inherited_description'] = True

        strong_feat = group[
            (pd.to_numeric(group['Best Feature Count'], errors='coerce').fillna(0) > 0) &
            (group['Feature Path'].fillna('') != '')
        ].copy()
        if not strong_feat.empty:
            strong_feat['__rank'] = strong_feat['Feature Path'].map(feat_path_rank)
            strong_feat['__count'] = pd.to_numeric(strong_feat['Best Feature Count'], errors='coerce').fillna(0)
            donor = strong_feat.sort_values(['__rank', '__count'], ascending=[True, False]).iloc[0]
            for row_idx in idx:
                count_now = pd.to_numeric(summary_df.at[row_idx, 'Best Feature Count'], errors='coerce')
                count_now = 0 if pd.isna(count_now) else count_now
                if count_now == 0 and str(summary_df.at[row_idx, 'SKU']) != str(donor['SKU']):
                    summary_df.at[row_idx, 'Feature Path'] = f"{donor['Feature Path']}__inherited"
                    summary_df.at[row_idx, 'Best Feature Count'] = donor['Best Feature Count']
                    summary_df.at[row_idx, 'Best Features'] = donor['Best Features']
                    summary_df.at[row_idx, 'Feature Flags'] = clean_text(f"{summary_df.at[row_idx, 'Feature Flags']} | inherited_from_sku:{donor['SKU']}")
                    summary_df.at[row_idx, 'Inherited From SKU'] = donor['SKU'] if not summary_df.at[row_idx, 'Inherited From SKU'] else summary_df.at[row_idx, 'Inherited From SKU']
                    summary_df.at[row_idx, 'Inherited Features'] = True
                    path_match_idx = path_df[path_df['SKU'].astype(str) == str(summary_df.at[row_idx, 'SKU'])].index
                    if len(path_match_idx):
                        pm = path_match_idx[0]
                        path_df.at[pm, 'final_feature_path'] = summary_df.at[row_idx, 'Feature Path']
                        path_df.at[pm, 'final_features'] = donor['Best Features']
                        path_df.at[pm, 'inherited_from_sku'] = donor['SKU']
                        path_df.at[pm, 'inherited_features'] = True

    return summary_df, path_df

# ==========================================================
# Row processor.
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
    family_route = classify_family(retail_url, salsify_url, sections['details'], vendor_objects[0]['desc'] if vendor_objects else '')
    canonical_family_key = build_canonical_family_key(retail_url, salsify_url, family_route, sections['details'], vendor_objects[0]['desc'] if vendor_objects else '')

    desc_path, best_desc, desc_flags, desc_audit = choose_description(family_route, details_split, item_split, vendor_objects, jsonld_desc, meta_desc)
    feat_path, best_features, feat_flags, feat_audit = choose_features(family_route, details_split, vendor_objects)

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
        'Family Route': family_route,
        'Canonical Family Key': canonical_family_key,
        'Description Path': desc_path,
        'Best Description': best_desc,
        'Description Flags': desc_flags,
        'Feature Path': feat_path,
        'Best Feature Count': len(best_features),
        'Best Features': ' | '.join(best_features),
        'Feature Flags': feat_flags,
        'Inherited From SKU': '',
        'Inherited Description': False,
        'Inherited Features': False,
        'Description Source Block': desc_audit.get('source_block', ''),
        'Description Start Anchor': desc_audit.get('start_anchor', ''),
        'Description End Anchor': desc_audit.get('end_anchor', ''),
        'Description Stop Reason': desc_audit.get('stop_reason', ''),
        'Feature Source Block': feat_audit.get('source_block', ''),
        'Feature Start Anchor': feat_audit.get('start_anchor', ''),
        'Feature End Anchor': feat_audit.get('end_anchor', ''),
        'Feature Stop Reason': feat_audit.get('stop_reason', ''),
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
        'Has Truncated Meta': False,
    }

    parser_paths = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Retail URL': retail_url,
        'Family Route': family_route,
        'Canonical Family Key': canonical_family_key,
        'meta_desc': meta_desc,
        'jsonld_desc': jsonld_desc,
        'vendor_desc_raw': vendor_objects[0]['desc'] if vendor_objects else '',
        'vendor_desc_clean': clean_vendor_description(vendor_objects[0]['desc']) if vendor_objects else '',
        'details_cleaned': details_split['cleaned'],
        'details_desc_block': details_split['desc_block'],
        'details_feature_block': details_split['feature_block'],
        'item_cleaned': item_split['cleaned'],
        'item_desc_block': item_split['desc_block'],
        'item_feature_block': item_split['feature_block'],
        'details_heading_lines': ' | '.join(details_split['heading_lines']),
        'details_heading_lines_alt': ' | '.join(details_split['heading_lines_alt']),
        'vendor_features_clean': ' | '.join(vendor_objects[0]['features']) if vendor_objects else '',
        'final_description_path': desc_path,
        'final_feature_path': feat_path,
        'final_description': best_desc,
        'final_features': ' | '.join(best_features),
        'inherited_from_sku': '',
        'inherited_description': False,
        'inherited_features': False,
    }

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
    }

    vendor_debug = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Retail URL': retail_url,
        'vendor_1_source': vendor_objects[0]['source'] if vendor_objects else '',
        'vendor_1_bullets_ref': vendor_objects[0]['bullets_ref'] if vendor_objects else '',
        'vendor_1_para_ref': vendor_objects[0]['para_ref'] if vendor_objects else '',
        'vendor_1_desc_raw': vendor_objects[0]['desc'] if vendor_objects else '',
        'vendor_1_desc_clean': clean_vendor_description(vendor_objects[0]['desc']) if vendor_objects else '',
        'vendor_1_features_clean': ' | '.join(vendor_objects[0]['features']) if vendor_objects else '',
        'vendor_1_preview': vendor_objects[0]['preview'] if vendor_objects else '',
        'vendor_1_resolved_bullets_raw': vendor_objects[0]['resolved_bullets_raw'] if vendor_objects else '',
        'vendor_1_resolved_para_raw': vendor_objects[0]['resolved_para_raw'] if vendor_objects else '',
    }

    marker = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Retail URL': retail_url,
        'Family Route': family_route,
        'Canonical Family Key': canonical_family_key,
        'Has vendorDetailsParagraph Token': 'vendorDetailsParagraph' in raw_text,
        'Has vendorDetailsBullets Token': 'vendorDetailsBullets' in raw_text,
        'Has Details marker': 'Details' in visible_text,
        'Has Item marker': 'Item #' in visible_text,
        'Has What Included marker': "WHAT'S INCLUDED" in visible_text or 'WHAT’S INCLUDED' in visible_text,
        'Details start idx': sections['details_idx'],
        'Item start idx': sections['item_idx'],
        'Whats start idx': sections['whats_idx'],
        'Title start idx': sections['title_idx'],
        'Details Preview': clean_text(sections['details'][:1000]),
        'Whats Preview': clean_text(sections['whats'][:1000]),
    }
    return summary, parser_paths, raw_debug, vendor_debug, marker

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

    if st.button('Run CVS source-anchor parser v4.7'):
        progress = st.progress(0)
        status = st.empty()
        summary_rows = []
        path_rows = []
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
                    summary, paths, raw_debug, vendor_debug, marker = fut.result()
                    summary_rows.append(summary)
                    path_rows.append(paths)
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

        summary_df = pd.DataFrame(summary_rows)
        path_df = pd.DataFrame(path_rows)
        raw_df = pd.DataFrame(raw_rows)
        vendor_df = pd.DataFrame(vendor_rows)
        marker_df = pd.DataFrame(marker_rows)
        errors_df = pd.DataFrame(error_rows)

        summary_df, path_df = apply_family_inheritance(summary_df, path_df)

        file_name = 'pdp_qa_tool_v4_7_output.xlsx'
        with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
            summary_df.to_excel(writer, index=False, sheet_name='Summary')
            path_df.to_excel(writer, index=False, sheet_name='Parser Paths')
            raw_df.to_excel(writer, index=False, sheet_name='Raw Windows')
            vendor_df.to_excel(writer, index=False, sheet_name='Vendor Debug')
            marker_df.to_excel(writer, index=False, sheet_name='Marker Debug')
            errors_df.to_excel(writer, index=False, sheet_name='Errors')

        if Path(file_name).exists():
            st.success(f'Done. Success rows: {len(summary_df)}. Error rows: {len(errors_df)}.')
            with open(file_name, 'rb') as f:
                st.download_button(
                    'Download v4.7 Excel output',
                    data=f,
                    file_name=file_name,
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
        else:
            st.error('The output file was not created.')
