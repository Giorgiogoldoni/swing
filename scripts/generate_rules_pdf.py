#!/usr/bin/env python3
"""generate_rules_pdf.py

Genera regole_donchian.pdf. Il testo è costruito dai parametri reali del codice (swing_lib.Params e
DONCHIAN_PAIRS) e, se presente, dai risultati di results/explore_donchian.json: se cambiano le soglie,
il PDF cambia con loro (nessun testo da aggiornare a mano).
"""

import datetime
import json
import os
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from swing_lib import Params, DONCHIAN_PAIRS  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE_DIR, "regole_donchian.pdf")
RESULTS_PATH = os.path.join(BASE_DIR, "results", "explore_donchian.json")
SIGNALS_PATH = os.path.join(BASE_DIR, "data", "signals.json")


def pct(x, nd=1):
    return f"{x * 100:.{nd}f}%".replace(".", ",")


def eur(x):
    return f"{x:,.0f}".replace(",", ".")


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build_story(p):
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=18, spaceAfter=6, alignment=0)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12.5, spaceBefore=12, spaceAfter=4,
                        textColor=colors.HexColor("#1f3a5f"), keepWithNext=1)
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=9.5, leading=13.5, spaceAfter=3)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.HexColor("#555555"))
    bullet = ParagraphStyle("bullet", parent=body, leftIndent=12, bulletIndent=2)

    def b(text):
        return Paragraph(text, bullet, bulletText="\u2022")

    (n1, m1), (n2, m2) = DONCHIAN_PAIRS[0], DONCHIAN_PAIRS[1]
    story = [
        Paragraph("Regole del sistema Donchian", h1),
        Paragraph(f"Aggiornato al {datetime.date.today().strftime('%d/%m/%Y')} - generato dal codice, "
                  "i valori riportati sono quelli realmente in uso.", small),
        Paragraph("<b>Stato: sistema in osservazione, non validato.</b> Sui dati 2016-2022 non ha mostrato un "
                  "vantaggio rispetto all'acquisto e mantenimento di un paniere equamente pesato (vedi sezione 6). "
                  "Quanto riportato non è consulenza finanziaria.", body),

        Paragraph("1. Che cosa fa", h2),
        Paragraph("Sistema di trend following sul timeframe giornaliero, solo acquisto (long). Compra le rotture "
                  "al rialzo del canale Donchian e vende quando il prezzo rompe il minimo recente. "
                  "Nessuno stop separato, nessun filtro di segnale: i soli filtri riguardano la qualità dei dati "
                  "e la scelta dell'universo.", body),

        Paragraph("2. Segnali (sulla chiusura dell'ultima barra)", h2),
        Paragraph(f"Due coppie di parametri classici, non ottimizzate: <b>{n1}/{m1}</b> (principale) e "
                  f"<b>{n2}/{m2}</b> (alternativa più rapida).", body),
        b(f"<b>INGRESSO</b>: la chiusura supera il massimo delle {n1} barre precedenti (la barra corrente è esclusa; "
          f"per la coppia {n2}/{m2}: {n2} barre). Si compra all'apertura del giorno dopo."),
        b(f"<b>USCITA</b>: con posizione aperta, la chiusura rompe il minimo delle {m1} barre precedenti "
          f"(per la coppia {n2}/{m2}: {m2} barre). Si vende all'apertura del giorno dopo."),
        b("<b>IN POSIZIONE</b>: la rottura è avvenuta in precedenza e da allora non c'è stata nessuna uscita."),
        b("<b>FUORI</b>: nessuna posizione; sono elencati solo gli strumenti eleggibili."),
        Paragraph("Lo stato mostrato è quello teorico di ogni strumento, senza i vincoli di portafoglio "
                  "(sezione 4). I livelli indicati nella pagina valgono per la seduta successiva.", body),

        Paragraph("3. Universo e filtri sui dati", h2),
        b("ETF, ETP ed ETC non a leva, tratti dall'universo di core (Borsa Italiana e Xetra). Obbligazionari inclusi."),
        b("Esclusi: prodotti cripto, ETP su Xetra, prodotti inversi o a leva e monetari. La classificazione di core "
          "non li distingue sempre: l'esclusione avviene anche per nome."),
        b(f"Storia minima: {p.min_bars} barre. Volatilità minima: ATR a {p.atr_period} giorni almeno "
          f"{pct(p.min_atr_pct, 2)} del prezzo."),
        b(f"Liquidità: mediana a 20 giorni di prezzo per volume di almeno {eur(p.min_turnover)} euro, con volume "
          f"maggiore di zero in almeno {pct(p.min_vol_days_frac, 0)} dei giorni."),
        b(f"Dati anomali: scartato lo strumento se in {p.anomaly_window} barre c'è un salto di prezzo oltre "
          f"{str(p.anomaly_hi).replace('.', ',')} volte o sotto {str(p.anomaly_lo).replace('.', ',')} volte; "
          "barre con prezzi non validi eliminate."),
        b("Prezzi aggiustati per dividendi e frazionamenti (Yahoo Finance). Per gli ETF poco scambiati, "
          "soprattutto su Xetra, le aperture possono essere inaffidabili."),

        Paragraph("4. Portafoglio", h2),
        b(f"Massimo {p.max_positions} posizioni contemporanee, ciascuna pari a 1/{p.max_positions} del capitale "
          "(pesi uguali, nessuna leva)."),
        b(f"Quando gli ingressi superano i posti liberi si ordinano per rendimento a {p.rank_lookback} giorni "
          "diviso ATR% (colonna Ranking): si prendono i primi."),
        b("Nessuno stop di prezzo: l'uscita è la rottura del minimo. Il rischio fino al giorno successivo "
          "alla rottura è a carico dell'operatore."),

        Paragraph("5. Come leggere la pagina", h2),
        b("<b>Livello rottura</b>: massimo delle ultime barre del canale; una chiusura sopra di esso è un ingresso. "
          "Per gli ingressi del giorno è il livello appena superato."),
        b("<b>Livello uscita</b>: minimo delle ultime barre del canale; una chiusura sotto è un'uscita. "
          "Per le uscite del giorno è il livello appena rotto."),
        b("<b>Dist. rottura / Dist. uscita</b>: distanza percentuale della chiusura dai due livelli."),
        b("<b>Rend. da ingresso</b>: variazione dalla apertura del giorno successivo al segnale di ingresso."),
        b("Le righe con ultimo dato più vecchio della data dei dati sono in grigio: il segnale riguarda una "
          "barra non recente."),
        b("Ogni ticker è un collegamento a TradingView con il prefisso di borsa corretto."),
    ]

    res = load_json(RESULTS_PATH)
    story.append(Paragraph("6. Evidenza sul passato (2016-2022) e limiti", h2))
    if res and "55/20" in res:
        blk = res["55/20"]["senza_costi"]
        bh = res.get("buy_hold_equamente_pesato", {})
        rows = [["", "Operazioni", "Rendimento totale", "Annuo", "Drawdown massimo"],
                [f"Donchian {n1}/{m1}", str(blk.get("n_trade")), pct(blk["rendimento_totale"]), pct(blk["cagr"]),
                 pct(blk["drawdown_massimo"])]]
        if bh:
            rows.append(["Buy e hold equamente pesato", "-", pct(bh["rendimento_totale"]), pct(bh["cagr"]),
                         pct(bh["drawdown_massimo"])])
        t = Table(rows, colWidths=[5.2 * cm, 2.6 * cm, 3.6 * cm, 2.2 * cm, 3.4 * cm])
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")), ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ]))
        story += [Paragraph("Risultati lordi (senza costi di negoziazione), periodo " + " - ".join(res.get("periodo", [])) + ".", body), t, Spacer(1, 4)]
        rc = res["55/20"].get("entrata_casuale_stesso_contesto_senza_costi", {})
        if rc:
            story.append(Paragraph(
                f"Entrate casuali nello stesso contesto (sopra la media a 50 giorni): rendimento totale medio "
                f"{pct(rc['rendimento_totale_medio'])}. Il segnale non ha dimostrato di valere più di una entrata casuale.", body))
    else:
        story.append(Paragraph("Risultati del backtest non disponibili in questa esecuzione.", body))
    story += [
        b("Il risultato dipende in larga parte da un solo anno (2020); negli altri la resa per operazione è vicina a zero."),
        b("Survivorship bias: l'universo è quello quotato oggi, gli ETF chiusi mancano. I risultati sono un limite superiore."),
        b("Il periodo 2023-2026 non è stato ancora usato come test finale."),
    ]
    sig = load_json(SIGNALS_PATH)
    if sig:
        story.append(Paragraph(f"Dati della pagina: ultima seduta {sig.get('data_dati', '-')}.", small))
    return story


def main():
    p = Params()
    doc = SimpleDocTemplate(OUT_PATH, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=1.8 * cm, bottomMargin=1.8 * cm, title="Regole del sistema Donchian",
                            author="swing")
    doc.build(build_story(p))
    print(f"Scritto {OUT_PATH} ({os.path.getsize(OUT_PATH) / 1e3:.0f} kB)")


if __name__ == "__main__":
    main()
