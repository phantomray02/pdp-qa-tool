
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
st.title("PDP QA Tool v4.4.1 — CVS Details Area Debugger")
st.caption("Patched build with hard error capture, guaranteed workbook output, and separate Details-area debug columns so the download button always appears when any rows process.")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])
html_cache = {}
MAX_CACHE = 100

# ==========================================================
# HTTP
# ==========================================================
def get_html(url: str) -> str:
    if not url:
        return ""
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

# ==========================================================
# TEXT HELPERS
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


def lines_from_visible_text(text):
    return [clean_text(x) for x in re.split(r'\n+', str(text or '')) if clean_text(x)]


def split_sentences(text):
    txt = clean_text(text)
    if not txt:
        return []
    return [clean_text(x) for x in re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', txt) if clean_text(x)]


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
    junk = [
        'skip to main content', 'cvs pharmacy', 'weekly ad', 'extra big deals', 'manage prescriptions',
        'schedule a vaccine', 'photo coupons', 'carepass', 'sign in account', 'use the cvs app',
        'how to get it', 'rating & reviews', 'extrabucks rewards', 'search cvs',
        'same-day delivery policies', 'shipping restrictions', 'summer manage prescriptions'
    ]
    return any(j in txt for j in junk)


def is_eligibility_or_rebate(text):
    txt = clean_text(text).lower()
    flags = [
        'hsa/fsa', 'purchase by', 'postmarked', 'original receipt',
        'restrictions apply', 'check with your provider', 'limit 1 per household'
    ]
    return any(f in txt for f in flags)


def cleanup_frontmatter(text):
    txt = clean_text(text)
    if not txt:
        return ''
    txt = re.sub(r'^.*?\bItem\s*#\s*\d+\s*', '', txt)
    txt = re.sub(r'^[A-Z0-9][A-Za-z0-9\-\+&/,()\'’ ]{6,180}\s+\d+\s*(?:Ct|Sht|Boxes?|Rolls?|Packs?|CT)\b[^A-Z]{0,30}', '', txt)
    txt = re.sub(r'^\d+\s*(?:Ct|Sht|Boxes?|Rolls?|Packs?|CT)\b\s*,?\s*\d*\.?\d*\s*(?:lbs?|oz)?\.?\s*', '', txt, flags=re.I)
    return clean_text(txt)


def clean_feature_line(text):
    txt = clean_text(text)
    if not txt:
        return ''
    txt = txt.replace(' | ', ' ').replace('|', ' ')
    txt = re.sub(r'\s+', ' ', txt).strip(' -;:')
    if has_nav_junk(txt):
        return ''
    return txt


def clean_feature_list(values, keep_eligibility=False):
    out = []
    for v in values:
        vv = clean_feature_line(v)
        if not vv or len(vv) < 20:
            continue
        if not keep_eligibility and is_eligibility_or_rebate(vv):
            continue
        out.append(vv)
    return dedupe_keep_order(out)[:5]


def format_labeled_blocks(blocks):
    out = []
    for label, txt in blocks:
        if txt:
            out.append(f'[{label}]\n{txt}')
    return '\n\n'.join(out)

# ==========================================================
# RAW / NEXTJS HELPERS
# ==========================================================
def get_nextjs_chunks(html_text):
    matches = re.findall(r'self\.__next_f\.push\(\[1,(.*?)\]\)', html_text or '', re.DOTALL)
    chunks = []
    for m in matches:
        t = m.strip()
        if t.startswith('"') and t.endswith('"'):
            t = t[1:-1]
        chunks.append(t)
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
    n, i = len(raw_text), 0

    def is_key_start(pos):
        return re.match(r'([0-9a-zA-Z]{1,3}):(?=[\[\{T"])', raw_text[pos:])

    while i < n:
        m = is_key_start(i)
        if not m:
            i += 1
            continue
        key = m.group(1)
        val_start = i + len(key) + 1
        j, depth, in_str, esc = val_start, 0, False, False
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
    m = re.search(rf'{re.escape(str(target_key))}:', raw_text)
    if not m:
        return None
    start, n, j = m.end(), len(raw_text), m.end()
    depth, in_str, esc = 0, False, False

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


def around(text, marker, back=350, forward=4500):
    if not text or not marker:
        return ''
    m = re.search(re.escape(marker), text)
    if not m:
        return ''
    start = max(0, m.start() - back)
    end = min(len(text), m.end() + forward)
    return text[start:end]


def find_vendor_objects(raw_text, data_map):
    normalized = raw_text.replace('\\"', '"')
    patterns = [
        r'\{\s*"vendorDetailsBullets"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*,\s*"vendorDetailsParagraph"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*\}',
        r'vendorDetailsBullets"\s*:\s*"(\$[0-9a-zA-Z]+)"\s*,\s*"vendorDetailsParagraph"\s*:\s*"(\$[0-9a-zA-Z]+)"'
    ]
    candidates = []
    for v in data_map.values():
        if isinstance(v, dict) and 'vendorDetailsBullets' in v and 'vendorDetailsParagraph' in v:
            candidates.append((v.get('vendorDetailsBullets'), v.get('vendorDetailsParagraph'), 'data_map', ''))
    for pat in patterns:
        for m in re.finditer(pat, normalized):
            b, p = m.group(1), m.group(2)
            preview = normalized[max(0, m.start() - 300):min(len(normalized), m.end() + 3000)]
            candidates.append((b, p, 'vendor_regex', preview))
    seen, out = set(), []
    for b, p, source, preview in candidates:
        key = (b, p)
        if key in seen:
            continue
        seen.add(key)
        bullets = resolve_ref_any(raw_text, data_map, b)
        para = resolve_ref_any(raw_text, data_map, p)
        out.append({
            'source': source,
            'bullets_ref': b or '',
            'para_ref': p or '',
            'desc': clean_text(para) if isinstance(para, str) else '',
            'features': clean_feature_list([clean_text(x) for x in bullets] if isinstance(bullets, list) else [], keep_eligibility=True),
            'preview': preview,
            'resolved_bullets_raw': json.dumps(bullets, ensure_ascii=False)[:7000] if bullets is not None else '',
            'resolved_para_raw': str(para)[:7000] if para is not None else '',
        })
    return out

# ==========================================================
# VISIBLE SECTIONS
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
        if sum(len(x) for x in collected) > 12000:
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
            elif re.search(r'[A-Z][A-Z0-9\'’/&\- ]{5,}:', line):
                whats_idx = i
    details = section_from_lines(lines, details_idx) if details_idx is not None else ''
    item = section_from_lines(lines, item_idx) if item_idx is not None else ''
    title_section = section_from_lines(lines, title_idx) if title_idx is not None else ''
    whats = section_from_lines(lines, whats_idx) if whats_idx is not None else ''
    if (not item or has_nav_junk(item) or len(item) < 80) and details:
        item = details
    return {'lines': lines, 'details': details, 'item': item, 'title_section': title_section, 'whats': whats}


def split_details_area(details_text):
    txt = clean_text(details_text)
    if not txt:
        return '', '', '', '', '', ''
    txt_clean = cleanup_frontmatter(txt)
    m = re.search(r'(.+?)(?=(?:WHAT\'?S INCLUDED|WHAT’S INCLUDED|[A-Z][A-Z0-9\'’/&\- ]{4,}\s*[—:-]))', txt_clean, re.S)
    prose = clean_text(m.group(1)) if m else txt_clean

    start_idx = None
    for token in ["WHAT'S INCLUDED", 'WHAT’S INCLUDED']:
        idx = txt_clean.find(token)
        if idx != -1:
            start_idx = idx if start_idx is None else min(start_idx, idx)

    headings_block = ''
    if start_idx is not None:
        headings_block = clean_text(txt_clean[start_idx:])
    else:
        m2 = re.search(r'([A-Z][A-Z0-9\'’/&\- ]{4,}\s*[—:-].+)$', txt_clean, re.S)
        if m2:
            headings_block = clean_text(m2.group(1))

    whats_only = ''
    for pat in [r'(WHAT\'?S INCLUDED\s*[—:-].+)$', r'(WHAT’S INCLUDED\s*[—:-].+)$']:
        m3 = re.search(pat, txt_clean, re.S)
        if m3:
            whats_only = clean_text(m3.group(1))
            break

    heading_lines = []
    parse_source = headings_block or txt_clean
    for m4 in re.finditer(r'([A-Z0-9][A-Z0-9\'’/&\- ]{3,60})\s*[—:-]\s*(.+?)(?=(?:[A-Z0-9][A-Z0-9\'’/&\- ]{3,60}\s*[—:-])|$)', parse_source, re.S):
        heading_lines.append(f"{clean_text(m4.group(1))} — {clean_text(m4.group(2))}")

    heading_only_lines = ' | '.join(clean_feature_list(heading_lines, keep_eligibility=True))
    sentence_only_lines = ' | '.join(clean_feature_list([s for s in split_sentences(txt_clean) if len(s) >= 40], keep_eligibility=True))
    return txt_clean, prose, headings_block, whats_only, heading_only_lines, sentence_only_lines


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
# FEATURE EXTRACTION THEORIES
# ==========================================================
def extract_whats_features(section):
    txt = clean_text(section)
    if not txt:
        return []
    vals = []
    for pat in [
        r"WHAT\'?S INCLUDED\s*[—:-]\s*(.+?)(?=(?:[A-Z][A-Z0-9 '&/\-]{4,}\s*[—:-])|$)",
        r"WHAT’S INCLUDED\s*[—:-]\s*(.+?)(?=(?:[A-Z][A-Z0-9 '&/\-]{4,}\s*[—:-])|$)",
    ]:
        m = re.search(pat, txt, re.S)
        if m:
            vals.append("WHAT'S INCLUDED — " + clean_text(m.group(1)))
    return vals

# ==========================================================
# CHOOSERS
# ==========================================================
def choose_desc(theories):
    priority = [
        'vendor_desc_1', 'vendor_desc_2', 'vendor_desc_3',
        'details_prose', 'visible_details_3sent', 'visible_item_3sent',
        'jsonld_desc', 'meta_desc'
    ]
    for key in priority:
        txt = cleanup_frontmatter(theories.get(key, ''))
        if not txt or has_nav_junk(txt):
            continue
        if key in ['jsonld_desc', 'meta_desc'] and len(txt) < 90:
            continue
        return key, txt
    for key in priority:
        txt = cleanup_frontmatter(theories.get(key, ''))
        if txt and not has_nav_junk(txt):
            return key, txt
    return '', ''


def choose_feat(feat_theories):
    priority = [
        'details_heading_lines_clean', 'details_whats_clean',
        'details_heading_lines_rawkeep', 'details_sentences_clean',
        'vendor_features_1'
    ]
    for key in priority:
        vals = feat_theories.get(key, [])
        vals = vals if isinstance(vals, list) else []
        if len(vals) >= 3:
            return key, vals
    for key in priority:
        vals = feat_theories.get(key, [])
        vals = vals if isinstance(vals, list) else []
        if vals:
            return key, vals
    return '', []

# ==========================================================
# ROW PROCESSING
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

    details_clean, details_prose, details_headings_block, details_whats_only, details_heading_only, details_sentence_only = split_details_area(sections['details'])
    item_clean, item_prose, item_headings_block, item_whats_only, item_heading_only, item_sentence_only = split_details_area(sections['item'])

    desc_theories = {
        'meta_desc': meta_desc,
        'jsonld_desc': jsonld_desc,
        'vendor_desc_1': vendor_objects[0]['desc'] if len(vendor_objects) > 0 else '',
        'vendor_desc_2': vendor_objects[1]['desc'] if len(vendor_objects) > 1 else '',
        'vendor_desc_3': vendor_objects[2]['desc'] if len(vendor_objects) > 2 else '',
        'details_prose': details_prose,
        'visible_details_3sent': ' '.join(split_sentences(details_prose)[:3]),
        'visible_item_3sent': ' '.join(split_sentences(item_prose)[:3]),
    }

    details_heading_clean = clean_feature_list(details_heading_only.split(' | ') if details_heading_only else [], keep_eligibility=False)
    details_heading_rawkeep = clean_feature_list(details_heading_only.split(' | ') if details_heading_only else [], keep_eligibility=True)
    details_whats_clean = clean_feature_list(extract_whats_features(details_whats_only or details_headings_block), keep_eligibility=False)
    details_sentences_clean = clean_feature_list(details_sentence_only.split(' | ') if details_sentence_only else [], keep_eligibility=False)

    feat_theories = {
        'vendor_features_1': vendor_objects[0]['features'] if len(vendor_objects) > 0 else [],
        'details_heading_lines_clean': details_heading_clean,
        'details_heading_lines_rawkeep': details_heading_rawkeep,
        'details_whats_clean': details_whats_clean,
        'details_sentences_clean': details_sentences_clean,
    }

    best_desc_strategy, best_desc = choose_desc(desc_theories)
    best_feat_strategy, best_feats = choose_feat(feat_theories)

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
        'Best Feature Strategy': best_feat_strategy,
        'Best Feature Count': len(best_feats),
        'Best Features': ' | '.join(best_feats),
        'Has NextF': 'self.__next_f.push([1,' in html_text,
        'Has vendorDetailsBullets Token': 'vendorDetailsBullets' in raw_text,
        'Has vendorDetailsParagraph Token': 'vendorDetailsParagraph' in raw_text,
        'Raw Text Length': len(raw_text),
        'Data Map Key Count': len(data_map),
        'Visible Item Section Preview': clean_text(sections['item'][:1200]),
        'Visible Details Section Preview': clean_text(sections['details'][:1200]),
        'Visible Title Section Preview': clean_text(sections['title_section'][:1200]),
        'Visible Whats Included Preview': clean_text(sections['whats'][:1200]),
    }

    strategy = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Retail URL': retail_url,
        'desc_meta': meta_desc,
        'desc_jsonld': jsonld_desc,
        'desc_vendor_1': desc_theories['vendor_desc_1'],
        'desc_details_prose': details_prose,
        'desc_details_3sent': desc_theories['visible_details_3sent'],
        'desc_item_3sent': desc_theories['visible_item_3sent'],
        'feat_vendor_1': ' | '.join(feat_theories['vendor_features_1']),
        'feat_details_heading_lines_clean': ' | '.join(details_heading_clean),
        'feat_details_heading_lines_rawkeep': ' | '.join(details_heading_rawkeep),
        'feat_details_whats_clean': ' | '.join(details_whats_clean),
        'feat_details_sentences_clean': ' | '.join(details_sentences_clean),
    }

    raw_debug = {
        'SKU': sku,
        'CVS RPC': cvs_rpc,
        'Retail URL': retail_url,
        'raw_desc_source_window': format_labeled_blocks([
            ('RAW vendorDetailsParagraph', raw_vendor_desc[:4500]),
            ('VISIBLE around Details', raw_details[:4500]),
            ('VISIBLE around Item #', raw_item[:4500]),
            ('VISIBLE around title', raw_title[:3500]),
            ('VISIBLE details section', sections['details'][:4500]),
            ('VISIBLE item section', sections['item'][:4500]),
        ]),
        'raw_feature_source_window': format_labeled_blocks([
            ('RAW vendorDetailsBullets', raw_vendor_feat[:4500]),
            ('VISIBLE around WHAT\'S INCLUDED', raw_whats[:4500]),
            ('VISIBLE around Details', raw_details[:4500]),
            ('VISIBLE details section', sections['details'][:4500]),
            ('VISIBLE whats section', sections['whats'][:4500]),
        ]),
        'raw_vendor_desc_window': raw_vendor_desc[:7000],
        'raw_vendor_feature_window': raw_vendor_feat[:7000],
        'raw_details_window': raw_details[:7000],
        'raw_item_window': raw_item[:7000],
        'raw_whats_window': raw_whats[:7000],
        'raw_title_window': raw_title[:7000],
        'visible_details_section_raw': sections['details'][:9000],
        'visible_item_section_raw': sections['item'][:9000],
        'visible_whats_section_raw': sections['whats'][:9000],
        'visible_title_section_raw': sections['title_section'][:9000],
        'details_cleaned_block': details_clean[:9000],
        'details_prose_block': details_prose[:9000],
        'details_headings_block': details_headings_block[:9000],
        'details_whats_only_block': details_whats_only[:9000],
        'details_heading_only_lines': details_heading_only[:9000],
        'details_sentence_only_lines': details_sentence_only[:9000],
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
        'vendor_1_preview': vendor_objects[0]['preview'][:7000] if len(vendor_objects) > 0 else '',
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
        'Has What Included marker': "WHAT'S INCLUDED" in visible_text or 'WHAT’S INCLUDED' in visible_text,
        'Details Preview': clean_text(sections['details'][:1000]),
        'Whats Preview': clean_text(sections['whats'][:1000]),
    }
    return summary, strategy, raw_debug, vendor_debug, marker

# ==========================================================
# MAIN
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

    if st.button('Run CVS details area debug v4.4.1'):
        progress = st.progress(0)
        status = st.empty()
        summary_rows, strategy_rows, raw_rows, vendor_rows, marker_rows, error_rows = [], [], [], [], [], []
        total = len(df)

        with ThreadPoolExecutor(max_workers=8) as ex:
            future_map = {}
            futures = []
            for _, row in df.iterrows():
                row_dict = row.to_dict()
                fut = ex.submit(process_row, row_dict)
                future_map[fut] = row_dict
                futures.append(fut)

            for i, fut in enumerate(as_completed(futures), start=1):
                row_dict = future_map[fut]
                try:
                    s, stg, r, v, m = fut.result()
                    summary_rows.append(s)
                    strategy_rows.append(stg)
                    raw_rows.append(r)
                    vendor_rows.append(v)
                    marker_rows.append(m)
                except Exception as e:
                    error_rows.append({
                        'sku': row_dict.get('sku', ''),
                        'retail_url': row_dict.get('retail_url', ''),
                        'salsify_url': row_dict.get('salsify_url', ''),
                        'error_type': type(e).__name__,
                        'error_message': str(e),
                    })
                progress.progress(i / total)
                status.write(f'Processed {i}/{total} | Success: {len(summary_rows)} | Errors: {len(error_rows)}')

        file_name = 'pdp_qa_tool_v4_4_1_output.xlsx'
        try:
            with pd.ExcelWriter(file_name, engine='openpyxl') as writer:
                pd.DataFrame(summary_rows).to_excel(writer, index=False, sheet_name='Summary')
                pd.DataFrame(strategy_rows).to_excel(writer, index=False, sheet_name='Strategy Matrix')
                pd.DataFrame(raw_rows).to_excel(writer, index=False, sheet_name='Raw Windows')
                pd.DataFrame(vendor_rows).to_excel(writer, index=False, sheet_name='Vendor Debug')
                pd.DataFrame(marker_rows).to_excel(writer, index=False, sheet_name='Marker Debug')
                pd.DataFrame(error_rows).to_excel(writer, index=False, sheet_name='Errors')
        except Exception as e:
            st.error(f'Excel write failed: {type(e).__name__}: {e}')
            st.stop()

        if Path(file_name).exists():
            st.success(f'Done. Success rows: {len(summary_rows)}. Error rows: {len(error_rows)}.')
            with open(file_name, 'rb') as f:
                st.download_button(
                    'Download v4.4.1 Excel output',
                    data=f,
                    file_name=file_name,
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                )
        else:
            st.error('The output file was not created.')
