
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
st.title("PDP QA Tool v4.6.1 — CVS Patch-Only Parser")
st.caption("Patch on top of v4.6. Keeps working routes locked, adds sibling inheritance for shared PDP variants, and avoids backtracking on rows already parsing correctly.")

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


def has_nav_junk(text):
    txt = clean_text(text).lower()
    junk = [
        'skip to main content', 'cvs pharmacy', 'weekly ad', 'extra big deals', 'manage prescriptions',
        'schedule a vaccine', 'photo coupons', 'carepass', 'sign in account', 'use the cvs app',
        'how to get it', 'rating & reviews', 'extrabucks rewards', 'search cvs', 'same-day delivery policies',
        'shipping restrictions', 'summer manage prescriptions', 'delivery details', 'explore more at cvs.com',
        'additional resources', 'check your state\'s eligibility', 'federal law and some state laws requires cvs',
        'select the state on your driver\'s license or state id'
    ]
    return any(token in txt for token in junk)


def is_promo_meta(text):
    txt = clean_text(text).lower()
    promo_markers = ['buy ', 'free shipping', 'shop cvs now', 'best deals', 'coupon', 'coupons', 'most orders']
    return any(token in txt for token in promo_markers)


def is_title_like(text):
    txt = clean_text(text)
    if not txt:
        return True
    return len(txt) <= 110 and len(split_sentences(txt)) <= 1


def has_rebate_copy(text):
    txt = clean_text(text).lower()
    markers = ['purchase by', 'postmarked', 'original receipt', 'restrictions apply', 'limit 1 per household', 'mail in by', 'money back', 'satisfaction guaranteed']
    return any(marker in txt for marker in markers)


def has_eligibility_copy(text):
    txt = clean_text(text).lower()
    markers = ['hsa/fsa', 'check with your provider', 'fsa-eligible', 'hsa-eligible', 'eligible in the us']
    return any(marker in txt for marker in markers)


def cutoff_at_markers(text, markers):
    txt = clean_text(text)
    if not txt:
        return ''
    low = txt.lower()
    cut_positions = [low.find(marker.lower()) for marker in markers if low.find(marker.lower()) != -1]
    if cut_positions:
        txt = txt[:min(cut_positions)].strip(' -;,.')
    return clean_text(txt)


def cleanup_frontmatter(text):
    txt = clean_text(text)
    if not txt:
        return ''
    txt = re.sub(r'^.*?\bItem\s*#\s*\d+\s*', '', txt)
    txt = re.sub(r'^[A-Z0-9][A-Za-z0-9\-\+&/,()\'’ ]{6,220}\s+\d+\s*(?:Ct|Sht|Boxes?|Rolls?|Packs?|CT)\b[^A-Z]{0,35}', '', txt)
    txt = re.sub(r'^\d+\s*(?:Ct|Sht|Boxes?|Rolls?|Packs?|CT)\b\s*,?\s*\d*\.?\d*\s*(?:lbs?|oz)?\.?\s*', '', txt, flags=re.I)
    return clean_text(txt)


def desc_is_usable(text, min_len=140):
    txt = clean_text(text)
    if not txt:
        return False
    if has_nav_junk(txt) or is_promo_meta(txt):
        return False
    if len(txt) < min_len:
        return False
    if is_title_like(txt):
        return False
    return True


def best_sentence_block(text, min_sentences=2, max_sentences=4):
    sents = split_sentences(text)
    if len(sents) < min_sentences:
        return ''
    return clean_text(' '.join(sents[:max_sentences]))


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


def split_feature_string(text):
    txt = clean_text(text)
    if not txt:
        return []
    parts = [clean_text(x) for x in re.split(r'\s*\|\s*', txt) if clean_text(x)]
    return clean_feature_list(parts, keep_eligibility=False, keep_rebates=False)


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
# Raw helpers.
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
            data_map[key] = val.split(',', 1)[1].strip().strip('"') if val.startswith('T') and ',' in val else val.strip().strip('"')
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
    return val.split(',', 1)[1].strip().strip('"') if val.startswith('T') and ',' in val else val.strip().strip('"')


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
# Visible section helpers.
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
    stops = ['rating & reviews','ingredients','directions','warnings','specifications','same-day delivery policies','shipping restrictions','faq','q:','a:','delivery details','explore more at cvs.com','show hidden columns','customers also bought','similar products','you may also like','read reviews']
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
            elif re.search(r'^[A-Z][A-Z0-9\'’/&\- ]{5,}:', line) or re.search(r'^[A-Z][A-Z0-9\'’/&\- ]{5,}\s*[—:-]', line):
                whats_idx = i
    details = section_from_lines(lines, details_idx) if details_idx is not None else ''
    item = section_from_lines(lines, item_idx) if item_idx is not None else ''
    title_section = section_from_lines(lines, title_idx) if title_idx is not None else ''
    whats = section_from_lines(lines, whats_idx) if whats_idx is not None else ''
    if (not item or has_nav_junk(item) or len(item) < 120) and details:
        item = details
    return {'lines': lines, 'details': details, 'item': item, 'title_section': title_section, 'whats': whats, 'item_idx': item_idx, 'details_idx': details_idx, 'title_idx': title_idx, 'whats_idx': whats_idx}

# ==========================================================
# Details split / heading parse.
# ==========================================================
def mark_heading_boundaries(text):
    txt = clean_text(text)
    if not txt:
        return ''
    txt = re.sub(r'(?<=[a-z0-9\*\)])\s+(?=(?:WHAT\'?S INCLUDED|WHAT’S INCLUDED))', ' ||| ', txt)
    txt = re.sub(r'(?<=[a-z0-9\*\)])\s+(?=(?:[A-Z][A-Z0-9\'’/&\- ]{4,}\s*[—:-]))', ' ||| ', txt)
    txt = re.sub(r'(?<=[a-z0-9\*\)])\s+(?=(?:OUR |UP TO |ALL DAY |UNDERWEAR-LIKE |ODOR CONTROL |OUTSTANDING |GENTLE FOR |HELPS IN |FRESHNESS |WETNESS |SAVE YOUR |MADE WITH |THE ORIGINAL |YOUR EVERYDAY |PLUSH TOILET |LASTS LONGER|FOR LARGE |FITS IN |HELPS REDUCE |BREAKS DOWN |QUICK CLEAN |GET THE JOB DONE |VIRTUALLY LINT FREE |EVERYDAY CLEANING ))', ' ||| ', txt)
    return clean_text(txt)


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
    return clean_feature_list(out, keep_eligibility=False, keep_rebates=False)


def split_details_area(details_text):
    txt = clean_text(details_text)
    if not txt:
        return {'cleaned':'','prose':'','prose_alt':'','heading_block':'','heading_lines':[],'heading_lines_alt':[],'whats_only':'','sentence_features':[]}
    txt_clean = cleanup_frontmatter(txt)
    txt_marked = mark_heading_boundaries(txt_clean)
    match = re.search(r'(.+?)(?=(?:WHAT\'?S INCLUDED|WHAT’S INCLUDED|[A-Z][A-Z0-9\'’/&\- ]{4,}\s*[—:-]))', txt_clean, re.S)
    prose = clean_text(match.group(1)) if match else txt_clean
    prose_alt = clean_text(txt_marked.split(' ||| ')[0]) if ' ||| ' in txt_marked else prose
    heading_block = ''
    for token in ["WHAT'S INCLUDED", 'WHAT’S INCLUDED']:
        idx = txt_clean.find(token)
        if idx != -1:
            heading_block = clean_text(txt_clean[idx:])
            break
    if not heading_block and ' ||| ' in txt_marked:
        heading_block = clean_text(' '.join(txt_marked.split(' ||| ')[1:]))
    if not heading_block:
        m2 = re.search(r'([A-Z][A-Z0-9\'’/&\- ]{4,}\s*[—:-].+)$', txt_clean, re.S)
        if m2:
            heading_block = clean_text(m2.group(1))
    whats_only = ''
    for pat in [r'(WHAT\'?S INCLUDED\s*[—:-].+)$', r'(WHAT’S INCLUDED\s*[—:-].+)$']:
        m3 = re.search(pat, txt_clean, re.S)
        if m3:
            whats_only = clean_text(m3.group(1))
            break
    return {
        'cleaned': txt_clean,
        'prose': prose,
        'prose_alt': prose_alt,
        'heading_block': heading_block,
        'heading_lines': parse_heading_lines(heading_block),
        'heading_lines_alt': parse_heading_lines(txt_marked),
        'whats_only': whats_only,
        'sentence_features': clean_feature_list([s for s in split_sentences(heading_block or txt_clean) if len(s) >= 45], keep_eligibility=False, keep_rebates=False),
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
    return clean_feature_list(vals, keep_eligibility=False, keep_rebates=False)

# ==========================================================
# Family routing + parser.
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
    txt = cleanup_frontmatter(text)
    txt = cutoff_at_markers(txt, ['hsa/fsa','check with your provider','fsa-eligible','hsa-eligible','purchase by','mail in by','original receipt','restrictions apply','limit 1 per household','satisfaction guaranteed'])
    return clean_text(txt)


def choose_description(family, details_split, item_split, vendor_objs, jsonld_desc, meta_desc):
    vendor_desc = clean_vendor_description(vendor_objs[0]['desc']) if vendor_objs else ''
    details_prose = cleanup_frontmatter(details_split['prose'])
    details_prose_alt = cleanup_frontmatter(details_split['prose_alt'])
    item_prose = cleanup_frontmatter(item_split['prose'])
    details_sent = best_sentence_block(details_split['cleaned'])
    item_sent = best_sentence_block(item_split['cleaned'])
    fallback_jsonld = clean_text(jsonld_desc)
    fallback_meta = clean_text(meta_desc)
    path = ''
    desc = ''
    flags = ''

    if family == 'details_family':
        if desc_is_usable(details_prose):
            path, desc = 'details_prose', details_prose
        elif desc_is_usable(details_prose_alt):
            path, desc = 'details_prose_alt', details_prose_alt
        elif desc_is_usable(item_prose):
            path, desc = 'item_prose', item_prose
        elif desc_is_usable(vendor_desc):
            path, desc = 'vendor_desc_clean', vendor_desc
    elif family == 'vendor_hybrid_family':
        if desc_is_usable(details_prose):
            path, desc = 'details_prose', details_prose
        elif desc_is_usable(vendor_desc):
            path, desc = 'vendor_desc_clean', vendor_desc
        elif desc_is_usable(item_prose):
            path, desc = 'item_prose', item_prose
    elif family == 'variant_detail_family':
        if desc_is_usable(details_prose):
            path, desc = 'details_prose', details_prose
        elif desc_is_usable(item_prose):
            path, desc = 'item_prose', item_prose
        elif desc_is_usable(vendor_desc):
            path, desc = 'vendor_desc_clean', vendor_desc
        elif desc_is_usable(details_sent, min_len=120):
            path, desc = 'details_sentence_block', details_sent
    elif family == 'vendor_family':
        if desc_is_usable(vendor_desc):
            path, desc = 'vendor_desc_clean', vendor_desc
        elif desc_is_usable(details_prose):
            path, desc = 'details_prose', details_prose
        elif desc_is_usable(item_prose):
            path, desc = 'item_prose', item_prose
    elif family == 'details_or_item_family':
        if desc_is_usable(details_prose):
            path, desc = 'details_prose', details_prose
        elif desc_is_usable(item_prose):
            path, desc = 'item_prose', item_prose
        elif desc_is_usable(vendor_desc):
            path, desc = 'vendor_desc_clean', vendor_desc
    else:
        for candidate_path, candidate in [('details_prose', details_prose), ('item_prose', item_prose), ('vendor_desc_clean', vendor_desc), ('details_sentence_block', details_sent), ('item_sentence_block', item_sent)]:
            if desc_is_usable(candidate, min_len=120):
                path, desc = candidate_path, candidate
                break

    if not desc:
        if fallback_jsonld:
            path, desc, flags = 'title_only_fallback', fallback_jsonld, 'title_like_only'
        elif fallback_meta:
            path, desc, flags = 'title_only_fallback', fallback_meta, 'promo_or_title_only'
    return path, clean_text(desc), flags


def choose_features(family, details_split, vendor_objs):
    vendor_features = vendor_objs[0]['features'] if vendor_objs else []
    heading_a = clean_feature_list(details_split['heading_lines'], keep_eligibility=False, keep_rebates=False)
    heading_b = clean_feature_list(details_split['heading_lines_alt'], keep_eligibility=False, keep_rebates=False)
    whats = extract_whats_features(details_split['whats_only'] or details_split['heading_block'])
    sentence_features = clean_feature_list(details_split['sentence_features'], keep_eligibility=False, keep_rebates=False)
    vendor_clean = clean_feature_list(vendor_features, keep_eligibility=False, keep_rebates=False)

    if family in ['details_family','variant_detail_family','details_or_item_family','general_family','vendor_hybrid_family']:
        if len(heading_a) >= 3:
            return 'details_heading_lines_a', heading_a, ''
        if len(heading_b) >= 3:
            return 'details_heading_lines_b', heading_b, ''
        if len(whats) >= 2:
            return 'details_whats_lines', whats, ''
        if len(vendor_clean) >= 3:
            return 'vendor_features_clean', vendor_clean, ''
        if len(sentence_features) >= 3:
            return 'details_sentence_features', sentence_features, ''
    else:
        if len(vendor_clean) >= 3:
            return 'vendor_features_clean', vendor_clean, ''
        if len(heading_a) >= 3:
            return 'details_heading_lines_a', heading_a, ''
        if len(whats) >= 2:
            return 'details_whats_lines', whats, ''
        if len(sentence_features) >= 3:
            return 'details_sentence_features', sentence_features, ''
    return '', [], 'no_feature_block'

# ==========================================================
# Post-processing: sibling inheritance. This is the 4.6.1 patch.
# ==========================================================
def desc_path_rank(path):
    order = {
        'details_prose': 1,
        'details_prose_alt': 2,
        'item_prose': 3,
        'details_sentence_block': 4,
        'vendor_desc_clean': 5,
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


def apply_sibling_inheritance(summary_df, parser_df):
    if summary_df.empty:
        return summary_df, parser_df

    summary_df = summary_df.copy()
    parser_df = parser_df.copy()

    # Use Retail URL first, then fallback to visible title family if needed.
    for retail_url, idx in summary_df.groupby('Retail URL').groups.items():
        group = summary_df.loc[list(idx)].copy()
        if len(group) <= 1:
            continue

        strong_desc_group = group[
            (group['Description Path'].isin(['details_prose', 'details_prose_alt', 'item_prose', 'vendor_desc_clean', 'details_sentence_block'])) &
            (group['Best Description'].fillna('').str.len() >= 140)
        ].copy()
        if not strong_desc_group.empty:
            strong_desc_group['__rank'] = strong_desc_group['Description Path'].map(desc_path_rank)
            strong_desc_group['__len'] = strong_desc_group['Best Description'].fillna('').str.len()
            donor_desc = strong_desc_group.sort_values(['__rank', '__len'], ascending=[True, False]).iloc[0]
            for row_idx in idx:
                current_path = clean_text(summary_df.at[row_idx, 'Description Path'])
                current_desc = clean_text(summary_df.at[row_idx, 'Best Description'])
                if (current_path == 'title_only_fallback' or len(current_desc) < 120) and donor_desc['SKU'] != summary_df.at[row_idx, 'SKU']:
                    summary_df.at[row_idx, 'Description Path'] = f"{donor_desc['Description Path']}__inherited"
                    summary_df.at[row_idx, 'Best Description'] = donor_desc['Best Description']
                    flags = clean_text(summary_df.at[row_idx, 'Description Flags'])
                    summary_df.at[row_idx, 'Description Flags'] = clean_text((flags + ' | inherited_from_sibling').strip(' |'))

        strong_feat_group = group[(pd.to_numeric(group['Best Feature Count'], errors='coerce').fillna(0) > 0) & (group['Feature Path'].fillna('') != '')].copy()
        if not strong_feat_group.empty:
            strong_feat_group['__rank'] = strong_feat_group['Feature Path'].map(feat_path_rank)
            strong_feat_group['__count'] = pd.to_numeric(strong_feat_group['Best Feature Count'], errors='coerce').fillna(0)
            donor_feat = strong_feat_group.sort_values(['__rank', '__count'], ascending=[True, False]).iloc[0]
            for row_idx in idx:
                current_count = pd.to_numeric(summary_df.at[row_idx, 'Best Feature Count'], errors='coerce')
                current_count = 0 if pd.isna(current_count) else current_count
                if current_count == 0 and donor_feat['SKU'] != summary_df.at[row_idx, 'SKU']:
                    summary_df.at[row_idx, 'Feature Path'] = f"{donor_feat['Feature Path']}__inherited"
                    summary_df.at[row_idx, 'Best Feature Count'] = donor_feat['Best Feature Count']
                    summary_df.at[row_idx, 'Best Features'] = donor_feat['Best Features']
                    flags = clean_text(summary_df.at[row_idx, 'Feature Flags'])
                    summary_df.at[row_idx, 'Feature Flags'] = clean_text((flags + ' | inherited_from_sibling').strip(' |'))

        # Keep parser paths sheet aligned with summary after inheritance.
        shared = set(summary_df.loc[list(idx), 'SKU'].astype(str)) & set(parser_df['SKU'].astype(str))
        for sku in shared:
            srow = summary_df[summary_df['SKU'].astype(str) == sku].iloc[0]
            prow_idx = parser_df[parser_df['SKU'].astype(str) == sku].index[0]
            parser_df.at[prow_idx, 'final_description_path'] = srow['Description Path']
            parser_df.at[prow_idx, 'final_feature_path'] = srow['Feature Path']
            parser_df.at[prow_idx, 'final_description'] = srow['Best Description']
            parser_df.at[prow_idx, 'final_features'] = srow['Best Features']

    return summary_df, parser_df

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
    family = classify_family(retail_url, salsify_url, sections['details'], vendor_objects[0]['desc'] if vendor_objects else '')

    desc_path, best_desc, desc_flags = choose_description(family, details_split, item_split, vendor_objects, jsonld_desc, meta_desc)
    feat_path, best_features, feat_flags = choose_features(family, details_split, vendor_objects)

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
        'Family Route': family,
        'Description Path': desc_path,
        'Best Description': best_desc,
        'Description Flags': desc_flags,
        'Feature Path': feat_path,
        'Best Feature Count': len(best_features),
        'Best Features': ' | '.join(best_features),
        'Feature Flags': feat_flags,
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
        'Family Route': family,
        'meta_desc': meta_desc,
        'jsonld_desc': jsonld_desc,
        'vendor_desc_raw': vendor_objects[0]['desc'] if vendor_objects else '',
        'vendor_desc_clean': clean_vendor_description(vendor_objects[0]['desc']) if vendor_objects else '',
        'details_prose': details_split['prose'],
        'details_prose_alt': details_split['prose_alt'],
        'item_prose': item_split['prose'],
        'details_heading_lines': ' | '.join(details_split['heading_lines']),
        'details_heading_lines_alt': ' | '.join(details_split['heading_lines_alt']),
        'details_whats_lines': ' | '.join(extract_whats_features(details_split['whats_only'] or details_split['heading_block'])),
        'vendor_features_clean': ' | '.join(vendor_objects[0]['features']) if vendor_objects else '',
        'details_sentence_features': ' | '.join(details_split['sentence_features']),
        'final_description_path': desc_path,
        'final_feature_path': feat_path,
        'final_description': best_desc,
        'final_features': ' | '.join(best_features),
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
        'details_cleaned_block': details_split['cleaned'][:12000],
        'details_prose_block': details_split['prose'][:12000],
        'details_prose_block_alt': details_split['prose_alt'][:12000],
        'details_headings_block': details_split['heading_block'][:12000],
        'details_whats_only_block': details_split['whats_only'][:12000],
        'details_heading_only_lines': ' | '.join(details_split['heading_lines'])[:12000],
        'details_heading_only_lines_alt': ' | '.join(details_split['heading_lines_alt'])[:12000],
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
        'Family Route': family,
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

    if st.button('Run CVS patch-only parser v4.6.1'):
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

        summary_df, path_df = apply_sibling_inheritance(summary_df, path_df)

        file_name = 'pdp_qa_tool_v4_6_1_output.xlsx'
        try:
            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                summary_df.to_excel(writer, index=False, sheet_name='Summary')
                path_df.to_excel(writer, index=False, sheet_name='Parser Paths')
                raw_df.to_excel(writer, index=False, sheet_name='Raw Windows')
                vendor_df.to_excel(writer, index=False, sheet_name='Vendor Debug')
                marker_df.to_excel(writer, index=False, sheet_name='Marker Debug')
                errors_df.to_excel(writer, index=False, sheet_name='Errors')
        except Exception as exc:
            st.error(f'Excel write failed: {type(exc).__name__}: {exc}')
            st.stop()

        if Path(file_name).exists():
            st.success(f'Done. Success rows: {len(summary_df)}. Error rows: {len(errors_df)}.')
            with open(file_name, 'rb') as f:
                st.download_button(
                    'Download v4.6.1 Excel output',
                    data=f,
                    file_name=file_name,
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
        else:
            st.error('The output file was not created.')
