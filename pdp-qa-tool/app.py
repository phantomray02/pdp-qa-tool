from pathlib import Path
import re

APP_PATH = Path('app.py')
if not APP_PATH.exists():
    raise SystemExit('Put this script in the same folder as app.py, then run: python patch_selected_retailer_scope.py')

text = APP_PATH.read_text(encoding='utf-8', errors='replace')
backup = APP_PATH.with_suffix('.py.bak_selected_retailer_scope')
backup.write_text(text, encoding='utf-8')

# 1. Kroger specific: do not dedupe shared Kroger PDP URLs.
old = '''    if dedupe_by_url and not out.empty:\n        out = out.drop_duplicates(subset=["retail_url"], keep="first").copy()\n'''
new = '''    if dedupe_by_url and not out.empty:\n        # Selected-retailer isolation: do not collapse Kroger rows by URL.\n        # Kroger can intentionally have multiple SKU7/version rows that share one PDP.\n        if selected_retailer_norm != "Kroger":\n            out = out.drop_duplicates(subset=["retail_url"], keep="first").copy()\n'''
if old in text:
    text = text.replace(old, new, 1)

# 2. Add generic selected-retailer builder for the wide SKU/RPC matrix.
helper = r'''

def build_selected_retailer_df_from_wide_source(df, selected_retailer):
    """Build the processing queue for exactly one selected retailer.

    This is the key rule for the one-template SKU/RPC matrix:
    if the user selects Kroger, load only Kroger RPC/Salsify URL/Retailer URL rows;
    if the user selects CVS, load only CVS rows; etc. Never queue all retailer URLs.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    selected_norm = normalize_retailer_name(selected_retailer)
    source = df.copy()
    source.columns = [str(c).strip().lower() for c in source.columns]

    config = None
    for retailer, rpc_col, salsify_col, url_col in WIDE_SALSIFY_RETAILER_CONFIG:
        if normalize_retailer_name(retailer) == selected_norm:
            config = (retailer, rpc_col, salsify_col, url_col)
            break
    if not config:
        return pd.DataFrame()

    retailer, rpc_col, salsify_col, url_col = config
    sku_col = first_existing_column_local(source, ["sku", "7 digit sku", "product sku", "salsify sku", "item sku"])
    brand_col = first_existing_column_local(source, ["brand", "brand_char", "brand characteristic"])
    salsify_source_col = first_existing_column_local(source, WIDE_SALSIFY_SALSIFY_URL_ALIASES.get(salsify_col, [salsify_col]))

    rows = []
    for _, row in source.iterrows():
        sku = normalize_space(row.get(sku_col, "")) if sku_col else ""
        brand = normalize_space(row.get(brand_col, "")) if brand_col else ""
        rpc = normalize_space(row.get(rpc_col, "")) if rpc_col in source.columns else ""
        salsify_url = normalize_space(row.get(salsify_source_col, "")) if salsify_source_col else ""
        retail_url = normalize_space(row.get(url_col, "")) if url_col in source.columns else ""

        # Only keep rows that have data for the selected retailer.
        if not (rpc or salsify_url or retail_url):
            continue

        rows.append({
            "retailer": retailer,
            "sku": sku,
            "brand": brand,
            "retailer_rpc": rpc,
            "salsify_url": clean_uploaded_url_value(salsify_url),
            "retail_url": normalize_uploaded_retail_url(retail_url),
            "rating": "",
            "review_count": "",
            "copy_source_code": "",
        })

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.sort_values(
        by=["retailer", "sku", "retailer_rpc", "retail_url"],
        key=lambda col: col.astype(str).str.lower(),
        kind="stable",
    ).reset_index(drop=True)
    return out
'''
if 'def build_selected_retailer_df_from_wide_source' not in text:
    marker = '''    required = ["sku", "salsify_url", "retail_url"]\n    missing = [c for c in required if c not in df.columns]\n    if missing:\n        raise ValueError(f"Missing required columns: {missing}")\n    return df\n'''
    if marker not in text:
        raise SystemExit('Could not find prepare_input_df return block to insert helper.')
    text = text.replace(marker, marker + helper + '\n', 1)

# 3. Preserve raw upload before preparing normalized all-retailer view.
text = text.replace(
    '''        master_df = read_uploaded_file_from_bytes(file_bytes, uploaded_file.name)\n        master_df = prepare_input_df(master_df)\n''',
    '''        source_master_df = read_uploaded_file_from_bytes(file_bytes, uploaded_file.name)\n        master_df = prepare_input_df(source_master_df)\n''',
    1,
)

# 4. Replace the queue build so wide matrix uses exactly the selected retailer.
old_queue = '''            retailer_df = strict_filter_rows_for_selected_retailer(\n                master_df,\n                selected_retailer,\n                dedupe_by_url=(selected_capture_mode == CAPTURE_MODE_USE_EXTENSION),\n            )\n'''
new_queue = '''            if is_wide_salsify_template_df(source_master_df):\n                # Wide SKU/RPC matrix rule: queue only the selected retailer.\n                retailer_df = build_selected_retailer_df_from_wide_source(source_master_df, selected_retailer)\n            else:\n                retailer_df = strict_filter_rows_for_selected_retailer(\n                    master_df,\n                    selected_retailer,\n                    dedupe_by_url=(selected_capture_mode == CAPTURE_MODE_USE_EXTENSION),\n                )\n'''
if old_queue in text:
    text = text.replace(old_queue, new_queue, 1)
else:
    print('Warning: main retailer queue block was not found. It may already be patched.')

# 5. Post-backfill Kroger isolation should not dedupe Kroger URLs.
text = text.replace(
    '''            if selected_retailer == "Kroger":\n                retailer_df = strict_filter_rows_for_selected_retailer(\n                    retailer_df,\n                    selected_retailer,\n                    dedupe_by_url=(selected_capture_mode == CAPTURE_MODE_USE_EXTENSION),\n                )\n''',
    '''            if selected_retailer == "Kroger":\n                retailer_df = strict_filter_rows_for_selected_retailer(\n                    retailer_df,\n                    selected_retailer,\n                    dedupe_by_url=False,\n                )\n''',
    1,
)

# 6. Non-fatal bridge, avoids component warnings becoming app killers.
text = text.replace(
    '    components.html(bridge_html, height=0, width=0)\n',
    '''    try:\n        components.html(bridge_html, height=0, width=0)\n    except Exception:\n        pass\n''',
    1,
)

# 7. Fix empty brand selectbox label warning.
text = text.replace(
    '''    st.selectbox(\n        "",\n        visual_brand_options,\n        key="selected_brand_visual",\n        label_visibility="collapsed",\n    )\n''',
    '''    st.selectbox(\n        "Select brand display filter",\n        visual_brand_options,\n        key="selected_brand_visual",\n        label_visibility="collapsed",\n    )\n''',
    1,
)

APP_PATH.write_text(text, encoding='utf-8')
print(f'Patched {APP_PATH}. Backup saved as {backup}.')
