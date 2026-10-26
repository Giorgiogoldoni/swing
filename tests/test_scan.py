"""Test dello scanner dei segnali e della logica della pagina. python -m pytest tests -q"""
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import scan_signals as sc  # noqa: E402
import swing_lib as sl  # noqa: E402
from test_swing import make_df, noisy_uptrend  # noqa: E402


def test_tradingview_url_prefixes():
    assert sc.tradingview_url("SWDA.MI").endswith("symbol=MIL%3ASWDA")
    assert sc.tradingview_url("EXS1.DE").endswith("symbol=XETR%3AEXS1")
    assert sc.tradingview_url("CW8.PA").endswith("symbol=EURONEXT%3ACW8")
    assert sc.tradingview_url("VUSA.L").endswith("symbol=LSE%3AVUSA")
    assert sc.tradingview_url("ABC").endswith("symbol=ABC")
    assert sc.tradingview_url("XYZ.MI").startswith("https://www.tradingview.com/chart/?symbol=")


def test_position_state_machine():
    s = np.zeros(10, dtype=bool); e = np.zeros(10, dtype=bool)
    assert sc.position_state(s, e) == ("FUORI", -1)
    s[9] = True
    assert sc.position_state(s, e) == ("INGRESSO", 9)                  # ingresso sull'ultima barra
    s2 = np.zeros(10, dtype=bool); s2[3] = True
    assert sc.position_state(s2, e) == ("IN POSIZIONE", 3)
    e2 = np.zeros(10, dtype=bool); e2[9] = True
    assert sc.position_state(s2, e2) == ("USCITA", -1)                  # uscita sull'ultima barra
    e3 = np.zeros(10, dtype=bool); e3[6] = True
    assert sc.position_state(s2, e3) == ("FUORI", -1)                   # uscita avvenuta prima
    e4 = np.zeros(10, dtype=bool); e4[2] = True                         # uscita senza posizione: ignorata
    assert sc.position_state(s2, e4) == ("IN POSIZIONE", 3)


def test_rows_levels_and_consistency():
    p = sl.Params()
    meta = {}
    prepared = {}
    for k in range(8):
        t = f"T{k}.MI"
        prepared[t] = sl.prepare_ticker(make_df(noisy_uptrend(k, 700), rng_pct=0.006), p)
        meta[t] = {"name": f"Test {k}", "isin": f"IT000{k}", "asset_class": "equity", "product_type": "ETF"}
    rows = sc.build_rows(prepared, meta, 55, 20)
    assert rows
    for r in rows:
        assert r["stato"] in ("INGRESSO", "USCITA", "IN POSIZIONE", "FUORI")
        assert r["tv"].startswith("https://www.tradingview.com/chart/?symbol=MIL%3AT")
        assert r["piazza"] == "MI"
        if r["stato"] == "INGRESSO":
            assert r["dist_rottura"] <= 0                                # chiusura sopra il livello appena superato
        if r["stato"] == "USCITA":
            assert r["dist_uscita"] <= 0                                 # chiusura sotto il livello appena rotto
        if r["stato"] == "IN POSIZIONE":
            assert r["dist_uscita"] >= 0 and r["giorni"] is not None and r["ingresso_prezzo"] > 0


def test_universe_excludes_crypto(tmp_path):
    uni = {"instruments": [
        {"ticker_yf": "AAA.MI", "name": "iShares Core MSCI World", "product_type": "ETF", "asset_class": "equity"},
        {"ticker_yf": "BTCE.DE", "name": "BITCOIN ETP", "product_type": "ETP", "asset_class": "equity"},
        {"ticker_yf": "XSDX.DE", "name": "XTRACKERS SHORTDAX DAILY SWAP", "product_type": "ETF", "asset_class": "equity"},
    ]}
    path = tmp_path / "u.json"; path.write_text(json.dumps(uni))
    assert [i["ticker_yf"] for i in sc.load_universe(str(path))] == ["AAA.MI"]


NODE = subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(not NODE, reason="node non disponibile")
def test_page_logic_in_node(tmp_path):
    import re
    html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    logic = re.search(r'<script id="logic">(.*?)</script>', html, re.S).group(1)
    ui = re.search(r'<script id="ui">(.*?)</script>', html, re.S).group(1)
    (tmp_path / "logic.js").write_text(logic)
    (tmp_path / "ui.js").write_text(ui)
    test_js = r"""
const L = require('./logic.js'); const assert = require('assert');
const rows = [
 {ticker:'A.MI', nome:'Alfa Bond', isin:'IT1', classe:'bond', piazza:'MI', stato:'INGRESSO', ranking:2, dist_rottura:-1, tv:'u1'},
 {ticker:'B.DE', nome:'Beta Equity', isin:'DE2', classe:'equity', piazza:'DE', stato:'INGRESSO', ranking:5, dist_rottura:-2, tv:'u2'},
 {ticker:'C.MI', nome:'Gamma', isin:'IT3', classe:'equity', piazza:'MI', stato:'FUORI', ranking:null, dist_rottura:3, tv:'u3'},
 {ticker:'D.MI', nome:'Delta', isin:'IT4', classe:'equity', piazza:'MI', stato:'USCITA', ranking:1, dist_rottura:2, tv:'u4'}];
assert.strictEqual(L.filterRows(rows,'INGRESSO','','','').length, 2);
assert.strictEqual(L.filterRows(rows,'TUTTI','','','').length, 4);
assert.strictEqual(L.filterRows(rows,'INGRESSO','','bond','').length, 1);
assert.strictEqual(L.filterRows(rows,'TUTTI','','','DE').length, 1);
assert.strictEqual(L.filterRows(rows,'TUTTI','gamma','','').length, 1);
assert.strictEqual(L.filterRows(rows,'TUTTI','it3','','').length, 1);
assert.deepStrictEqual(L.sortRows(rows,'ranking',-1).map(r=>r.ticker), ['B.DE','A.MI','D.MI','C.MI']);   // null in fondo
assert.deepStrictEqual(L.sortRows(rows,'ranking',1).map(r=>r.ticker), ['D.MI','A.MI','B.DE','C.MI']);
assert.deepStrictEqual(L.sortRows(rows,'ticker',1).map(r=>r.ticker), ['A.MI','B.DE','C.MI','D.MI']);
const ex = L.excelRows(rows.slice(0,1));
assert.strictEqual(ex[0]['Ticker'],'A.MI'); assert.strictEqual(ex[0]['TradingView'],'u1'); assert.strictEqual(ex[0]['Giorni'],'');
console.log('OK');
"""
    (tmp_path / "t.js").write_text(test_js)
    r = subprocess.run([NODE, "t.js"], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr + r.stdout
    for f in ("logic.js", "ui.js"):
        c = subprocess.run([NODE, "--check", str(tmp_path / f)], capture_output=True, text=True)
        assert c.returncode == 0, c.stderr
