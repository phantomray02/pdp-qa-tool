APP RETAILER ROUTING PATCH
==========================

Goal
----
Use retailer-specific routing:
- CVS: skip the extension and go straight to batch/extract.
- Walgreens: skip the extension and go straight to batch/extract.
- Kroger: use the extension bridge.
- Sam's Club: use the extension bridge.

How to apply
------------
In your current bridge-enabled app file, replace or add the blocks below.

1) ADD THESE HELPER FUNCTIONS
-----------------------------
Paste these near your other bridge helper functions.

```python
def uses_extension_bridge_for_retailer(retailer_name):
    retailer_key = str(retailer_name or "").strip().lower()
    return retailer_key in {"kroger", "sam's club", "sams club", "samsclub"}


def should_skip_extension_for_retailer(retailer_name):
    retailer_key = str(retailer_name or "").strip().lower()
    return retailer_key in {"cvs", "walgreens"}
```

2) REPLACE build_bridge_rows_from_retailer_df
---------------------------------------------
Only build bridge rows for Kroger and Sam's Club.

```python
def build_bridge_rows_from_retailer_df(retailer_df, retailer_name=""):
    rows = []
    seen = set()
    if retailer_df is None or getattr(retailer_df, "empty", True):
        return rows

    if not uses_extension_bridge_for_retailer(retailer_name):
        return rows

    for _, row in retailer_df.iterrows():
        sku = str(row.get("sku", "") or "").strip()
        url = str(row.get("retail_url", "") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append({"url": url, "label": sku})
    return rows
```

3) UPDATE THE CALL SITE THAT BUILDS bridge_rows
-----------------------------------------------
Replace this:

```python
bridge_rows = build_bridge_rows_from_retailer_df(retailer_df)
```

With this:

```python
bridge_rows = build_bridge_rows_from_retailer_df(retailer_df, selected_retailer)
```

4) REPLACE get_html(url)
------------------------
This makes CVS and Walgreens go direct, while Kroger and Sam's use the bridge.

```python
def get_html(url):
    global html_cache

    if "html_cache" not in globals() or not isinstance(globals().get("html_cache"), dict):
        html_cache = {}

    url = str(url or "").strip()
    if not url:
        return ""

    if is_salsify_url(url):
        return get_salsify_html_direct(url)

    bridged_map = get_session_bridged_html_map()
    if url in bridged_map:
        return bridged_map.get(url, "")

    cached = html_cache.get(url)
    if cached:
        return cached

    # Retailer-specific routing.
    retailer_key = str(CURRENT_FETCH_RETAILER or "").strip().lower()
    use_bridge = uses_extension_bridge_for_retailer(retailer_key)

    if use_bridge and BRIDGE_EXTENSION_MODE:
        return ""

    try:
        session = get_session()
        r = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code == 200 and r.text:
            html_cache[url] = r.text
            while len(html_cache) > HTML_CACHE_MAX:
                html_cache.pop(next(iter(html_cache)))
            return r.text
    except Exception:
        pass

    return ""
```

5) REPLACE get_walgreens_html(url)
----------------------------------
Keep Walgreens direct and batch/extract without the extension gate.

```python
def get_walgreens_html(url):
    global html_cache
    if "html_cache" not in globals() or not isinstance(globals().get("html_cache"), dict):
        html_cache = {}

    url = str(url or "").strip()
    if not url:
        return ""

    cache_key = f"walgreens::{url}"
    if cache_key in html_cache:
        html_cache[cache_key] = html_cache.pop(cache_key)
        return html_cache[cache_key]

    html_text = fetch_html_with_timeout(url, WALGREENS_REQUEST_TIMEOUT)
    if html_text:
        html_cache[cache_key] = html_text
        while len(html_cache) > HTML_CACHE_MAX:
            html_cache.pop(next(iter(html_cache)))
    return html_text
```

6) WRAP THE BRIDGE UI BLOCK SO IT ONLY RUNS FOR KROGER / SAM'S
--------------------------------------------------------------
Only show the extension bridge UI when the selected retailer uses the bridge.

Change your bridge UI section from this pattern:

```python
render_bridge_config_beacon(current_batch_key, selected_retailer, bridge_rows)

bridge_payload = extension_bridge(
    rows=bridge_rows,
    retailer=selected_retailer,
    batch_id=current_batch_key,
    auto_show_status=True,
    key=f"bridge_{current_batch_key}",
)

# ...chunk handling...

bridged_map = get_session_bridged_html_map()
bridged_ready_count = len([url for url in bridge_required_urls if url in bridged_map])
st.caption(...)
```

To this:

```python
if uses_extension_bridge_for_retailer(selected_retailer):
    render_bridge_config_beacon(current_batch_key, selected_retailer, bridge_rows)

    bridge_payload = extension_bridge(
        rows=bridge_rows,
        retailer=selected_retailer,
        batch_id=current_batch_key,
        auto_show_status=True,
        key=f"bridge_{current_batch_key}",
    )

    if bridge_payload and isinstance(bridge_payload, dict):
        event_type = str(bridge_payload.get("event_type", "") or "").strip().lower()
        if event_type == "chunk":
            chunk_id = str(bridge_payload.get("chunk_id", "") or "").strip()
            processed_chunks = get_processed_bridge_chunks()
            if chunk_id and chunk_id not in processed_chunks:
                bridged_map = get_session_bridged_html_map()
                for item in bridge_payload.get("results", []) or []:
                    url = str(item.get("url", "") or "").strip()
                    if not url:
                        continue
                    bridged_map[url] = str(item.get("html", "") or "")
                st.session_state.bridged_html_by_url = bridged_map
                processed_chunks.add(chunk_id)
                st.session_state.processed_bridge_chunks = processed_chunks
        elif event_type in ["complete", "cancelled"]:
            payload_signature = hashlib.md5(json.dumps(bridge_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
            if st.session_state.bridge_last_result_signature != payload_signature:
                st.session_state.bridge_last_result_signature = payload_signature
                if str(bridge_payload.get("batch_id", "") or "") == current_batch_key:
                    st.session_state.bridge_ready_batch_key = current_batch_key
        elif event_type == "error":
            st.warning(f"Bridge error: {bridge_payload.get('bridge_error', 'Unknown error')}")

    bridged_map = get_session_bridged_html_map()
    bridged_ready_count = len([url for url in bridge_required_urls if url in bridged_map])
    st.caption(
        f"Bridge retailer HTML ready for this batch: {bridged_ready_count}/{len(bridge_required_urls)} URLs. "
        f"After choosing the retailer, click the Edge extension once to fetch the retailer batch."
    )
```

7) WRAP THE STEP 2 WAIT GATE SO CVS / WALGREENS GO STRAIGHT TO BATCH
--------------------------------------------------------------------
Replace this pattern:

```python
if BRIDGE_EXTENSION_MODE:
    bridged_map = get_session_bridged_html_map()
    missing_bridge_urls = [url for url in bridge_required_urls if url not in bridged_map]
    bridge_batch_done = st.session_state.bridge_ready_batch_key == current_batch_key
    if missing_bridge_urls and not bridge_batch_done:
        st.info(...)
        st.caption(...)
        st.stop()
```

With this:

```python
if BRIDGE_EXTENSION_MODE and uses_extension_bridge_for_retailer(selected_retailer):
    bridged_map = get_session_bridged_html_map()
    missing_bridge_urls = [url for url in bridge_required_urls if url not in bridged_map]
    bridge_batch_done = st.session_state.bridge_ready_batch_key == current_batch_key

    if missing_bridge_urls and not bridge_batch_done:
        st.info(
            "Step 2: Click the Raw HTML Fetcher Edge extension once while this Streamlit tab is open. "
            "The app will start processing after the extension returns this retailer batch."
        )
        st.caption(f"Still waiting on {len(missing_bridge_urls)} retailer URL(s) from the extension bridge.")
        st.stop()
    elif missing_bridge_urls and bridge_batch_done:
        st.warning(
            f"Extension finished, but {len(missing_bridge_urls)} retailer URL(s) were not returned. "
            "Continuing with the pages that were captured so export/report can still finish."
        )
```

Notes
-----
- CVS and Walgreens will no longer be blocked by the extension Step 2 wait path.
- Kroger and Sam's Club will still use the extension bridge.
- If you want, the next step is for me to merge this directly into your full app file and hand back one finished TXT.
