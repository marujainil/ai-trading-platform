"""Front-end integrity: every button must have a handler, every element JS touches
must exist, and no half-deleted sections. A missing function is invisible to Python
tests but breaks the whole page in the browser ("X is not defined")."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
JS = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "dashboard.html").read_text(encoding="utf-8")

DEFINED = (set(re.findall(r"function\s+([A-Za-z_$][\w$]*)", JS))
           | set(re.findall(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", JS)))


def test_every_inline_handler_exists():
    used = set(re.findall(r'on(?:click|change)="([A-Za-z_$][\w$]*)\(', HTML))
    assert used, "no handlers found — parser broken"
    assert not (used - DEFINED), f"buttons call undefined functions: {sorted(used - DEFINED)}"


def test_core_runtime_functions_present():
    """These are called from code paths Python tests never touch."""
    for fn in ("analyze", "renderVerdict", "renderCharts", "startLive", "stopLive",
               "pollLive", "paintPrice", "growLastBar", "binancePair", "nseOpen",
               "loadUniverse", "loadScanResults", "renderScan", "loadTrackRecord",
               "saveGrowwToken", "backtest", "loadWatch", "loadPrecision"):
        assert fn in DEFINED, f"{fn}() is missing — the page will break at runtime"


def test_no_orphan_element_ids():
    ids_js = set(re.findall(r'\$\("([A-Za-z0-9_]+)"\)', JS))
    ids_html = set(re.findall(r'id="([A-Za-z0-9_]+)"', HTML))
    assert not (ids_js - ids_html), f"JS touches non-existent elements: {sorted(ids_js - ids_html)}"


def test_js_has_balanced_braces():
    """Cheap structural check: a truncated file fails here."""
    for open_c, close_c in (("{", "}"), ("(", ")"), ("[", "]")):
        assert JS.count(open_c) == JS.count(close_c), f"unbalanced {open_c}{close_c} in app.js"


def test_timeframe_handler_is_scoped():
    """The .tf class is reused for styling elsewhere; an unscoped listener once set
    chartTF to undefined and broke every analysis with 'tf must be one of…'."""
    assert 'querySelectorAll("#tfRow .tf")' in JS, "timeframe listener must be scoped to #tfRow"
    assert 'querySelectorAll(".tf")' not in JS, "unscoped .tf listener would hijack other buttons"
    assert "if (!b.dataset.tf) return;" in JS, "missing guard against non-timeframe buttons"


def test_scanner_filter_buttons_have_own_class():
    filters = re.search(r'id="scanFilters".*?</div>', HTML, re.S)
    assert filters and "fbtn" in filters.group(0), "scan filters need their own class"


def test_chart_timeframes_match_backend():
    """Every button in the chart row must be a timeframe the API accepts."""
    from app.api.routes import TIMEFRAMES
    row = re.search(r'id="tfRow".*?</div>', HTML, re.S).group(0)
    for tf in re.findall(r'data-tf="([^"]+)"', row):
        assert tf in TIMEFRAMES, f"chart button '{tf}' is not a valid backend timeframe"
