from pathlib import Path
import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).resolve().parent / "streamlit_bridge_component"
_bridge_component = components.declare_component("pdp_extension_bridge", path=str(_COMPONENT_DIR))


def extension_bridge(rows, retailer="", batch_id="", auto_show_status=False, key="pdp_extension_bridge"):
    """
    rows: list of dicts like {"url": "...", "label": "optional"}
    Returns the batch payload posted back by the extension content script.
    """
    return _bridge_component(
        rows=rows or [],
        retailer=retailer or "",
        batch_id=batch_id or "",
        auto_show_status=bool(auto_show_status),
        default=None,
        key=key,
    )
