# swing

Sistema di swing trading **daily**, **solo long**, su ETF, ETP ed ETC **non a leva**.
Segue il trend, entra su pullback con conferma, controlla il rischio. Genera segnali: la decisione resta discrezionale.

Stato: **fase 1 completata (specifica approvata)**. **Fase 2: codice del backtest pronto**, in attesa del primo download dei prezzi.

## Principi

- Le soglie qui sotto sono fissate **prima** di guardare qualsiasi risultato e **non si ottimizzano** sul backtest.
- La lista degli strumenti è letta da [`core`](https://github.com/Giorgiogoldoni/core) (`data/tickers_universe.json`).
  Prezzi, indicatori e segnali sono calcolati da `swing`: gli score di `core` non vengono usati.
- Prezzi giornalieri aggiustati (default yfinance). Il segnale si calcola sulla chiusura del giorno *t*.

## 1. Universo e filtri

| Regola | Definizione |
|---|---|
| Strumenti | ETF, ETP, ETC; `is_leveraged` falso; cripto, monetari e short esclusi. Obbligazionari inclusi. Esclusi anche per nome: prodotti cripto, ETP su Xetra, prodotti inversi o a leva che `core` classifica come azionari (circa 70 strumenti) |
| Storia minima | 150 barre di vita (riscaldamento di EMA50 e KAMA) |
| Volatilità minima | ATR14 / prezzo ≥ 0,30% |
| Liquidità | mediana a 20 giorni di (prezzo × volume) ≥ 50.000 €; volume > 0 in almeno l'80% dei giorni |
| Dati anomali | scarto se in 250 barre c'è un rapporto giorno-su-giorno > 2,5 o < 0,4 |

## 2. Trend (tutte le condizioni vere)

- EMA20 > EMA50
- EMA20(t) > EMA20(t−5) e EMA50(t) > EMA50(t−10)
- Chiusura > EMA50
- KAMA(10, 2, 30) non piatta: KAMA(t) − KAMA(t−10) ≥ 1,0 × ATR14

## 3. Pullback e conferma (candela *t*)

- Tocco: nelle ultime 3 barre almeno un minimo ≤ EMA20
- Nessuna chiusura efficace sotto EMA50: nessuna chiusura < EMA50 − 0,5 × ATR14 nelle stesse 3 barre
- Chiusura(t) > EMA20(t); candela rialzista con corpo ≥ 50% del range e ombra superiore ≤ 25% del range
- AO(t) > AO(t−1), con AO = SMA5 − SMA34 su (max+min)/2
- Un solo segnale per strumento ogni 10 barre

## 4. Esecuzione, stop, uscita

- Segnale alla chiusura di *t*; ingresso all'apertura di *t+1*. Trade saltato se l'apertura è oltre 1 ATR sopra la chiusura di *t*.
- **Stop unico**: minimo delle ultime 3 barre − 0,25 × ATR14. Distanza tra 1 e 3 ATR: se più corta, stop a 1 ATR; se più lunga, trade saltato.
- **Filtro R/R**: ingresso solo se (massimo a 60 barre − ingresso) ≥ 2 × distanza dello stop.
- **Uscite**: stop colpito; dopo +1R trailing con il SAR; invalidazione (chiusura < EMA50 − 0,5 × ATR14 oppure EMA20 < EMA50) con uscita all'apertura successiva.

## 5. Portafoglio

- Rischio per trade: **0,5%** del capitale. Con 15 posizioni il rischio aperto iniziale massimo è 7,5%; tetto a 8%.
- Massimo **15 posizioni** contemporanee; massimo 3 nuove posizioni al giorno.
- Tetto per posizione: 10% del capitale. Esposizione totale ≤ 100% (nessuna leva).
- Tetti per classe: azionari 8, obbligazionari 5, materie prime 3.
- Massimo 2 posizioni per gruppo di strumenti con correlazione > 0,85 sui rendimenti a 60 giorni.
- Ordine quando i segnali superano gli slot liberi: rendimento a 60 giorni / ATR%.

## 6. Protocollo di test

- Prezzi: storico massimo disponibile (fino a ~10 anni), per includere crollo 2020 e ribasso 2022.
- Periodo di calibrazione solo descrittivo (distribuzioni di ATR% e liquidità per fissare le soglie), poi un test unico sul periodo recente mai guardato.
- Costi: 0,20% per giro completo (caso base), 0,40% (stress).
- Confronti obbligatori: universo equamente pesato in acquisto e mantenimento (ignorando rendimenti giornalieri oltre il 25%, che sono prezzi sporchi); entrata casuale con gli stessi filtri di trend e le stesse uscite.
- Metriche: numero di trade, aspettativa in R, profit factor, drawdown massimo (simulazione con 15 slot), risultati per anno e per classe di attività.
- Criteri di accettazione: oltre 150 trade, aspettativa > +0,15R dopo i costi, profit factor > 1,3, drawdown massimo < 15%.
- Unica variante pre-dichiarata: stesso sistema **senza** il filtro R/R.

## Esplorazione sui dati prima del 2023 (nessun test finale ancora eseguito)

Periodo 2016-10 → 2022-12, strumenti eleggibili. Varianti provate finora: **5** (specifica completa, senza R/R, semplice, Donchian 55/20 e 20/10). Più varianti si provano sugli stessi dati, più cresce il rischio di adattarsi al passato. Profit factor calcolato in euro per tutte le righe (per le varianti 1 e 2 in R era 0,58 e 0,96).

| Variante | Operazioni | Per operazione | Profit factor | Totale | Annuo | Drawdown max | Volatilità |
|---|---|---|---|---|---|---|---|
| 1. Specifica completa (con R/R), costi 0,20% | 39 | −0,21% (−0,37R) | 0,76 | −0,8% | −0,1% | −1,5% | 0,7% |
| 2. Come la 1 senza R/R, costi 0,20% | 241 | +0,21% (−0,02R) | 1,14 | +3,9% | 0,6% | −4,6% | 3,2% |
| 3. Semplice (taglio EMA20, uscita SAR), senza costi | 1.813 | +0,19% | 1,17 | +22,5% | 3,3% | −11,2% | 8,4% |
| 3. costi 0,20% | 1.813 | −0,01% | 0,97 | −3,7% | −0,6% | −15,3% | 8,4% |
| 3. costi 0,40% | 1.813 | −0,21% | 0,81 | −24,2% | −4,4% | −24,8% | 8,4% |
| 3. entrata casuale, stesso contesto, senza costi | ~2.100 | +0,18% | 1,18 | +26,4% | 3,8% | −13,4% | 9,6% |
| 4a. Donchian 55/20, senza costi | 486 | +1,07% | 1,35 | +31,3% | 4,5% | −24,9% | 12,7% |
| 4a. costi 0,20% | 486 | +0,86% | 1,26 | +23,1% | 3,4% | −25,4% | 12,7% |
| 4a. costi 0,40% | 486 | +0,66% | 1,17 | +15,5% | 2,4% | −25,9% | 12,7% |
| 4a. entrata casuale, stesso contesto, senza costi | ~590 | +0,74% | 1,38 | +28,4% | 4,1% | −19,1% | 11,8% |
| 4a. ordine casuale dei candidati, senza costi | ~500 | +0,66% | 1,26 | +21,3% | 3,2% | −23,0% | 11,4% |
| 4b. Donchian 20/10, senza costi | 933 | +0,38% | 1,20 | +21,9% | 3,3% | −23,6% | 11,8% |
| 4b. costi 0,20% | 933 | +0,18% | 1,07 | +7,8% | 1,2% | −24,8% | 11,8% |
| 4b. costi 0,40% | 933 | −0,02% | 0,96 | −4,6% | −0,8% | −26,1% | 11,8% |
| Buy & hold equamente pesato | – | – | – | +29,7% | 4,3% | −28,5% | 11,9% |

**Variante 3 (semplice):** ingresso quando il prezzo taglia la EMA20 al rialzo, chiusura sopra EMA50 e SAR rialzista; uscita al primo pallino SAR sopra il prezzo, eseguita all'apertura successiva. Nessuno stop, nessun filtro di segnale.

**Variante 4 (Donchian):** ingresso se la chiusura supera il massimo delle N barre precedenti (la barra corrente è esclusa), uscita se la chiusura rompe il minimo delle M barre precedenti; esecuzione all'apertura successiva.

**Varianti 3 e 4:** ogni posizione vale 1/15 dell'equity, ordine dei candidati per rendimento a 60 giorni / ATR% (nelle righe «ordine casuale» è casuale), restano i filtri di igiene sull'universo.

Lettura: in tutte le varianti l'entrata casuale nello stesso contesto (sopra EMA50) fa quanto il segnale, quindi nessun trigger dimostra un valore proprio. La Donchian 55/20 rende quanto il buy & hold con volatilità simile e un drawdown un po' più basso; il suo risultato dipende dal 2020 (+6,5% per operazione, contro valori vicini a zero in 4 anni su 6).

La valorizzazione ignora i picchi di prezzo di un solo giorno (71 barre in tutto l'universo), che altrimenti falsavano drawdown e volatilità.

## Limiti noti

- **Survivorship bias**: l'universo è la lista di oggi; gli ETF chiusi o delistati mancano. I risultati sono un limite superiore.
- **Dati Open**: per ETF illiquidi (soprattutto Xetra) l'apertura di yfinance può coincidere con la chiusura precedente; se il problema è diffuso, si rivede il requisito del corpo rialzista.
- **Filtro R/R**: scarta i pullback poco profondi nei trend forti (ingresso vicino ai massimi a 60 barre).
- **Tetto 10% per posizione, 15 posizioni ed esposizione ≤ 100% non sono compatibili.** Con rischio 0,5% e stop di 1-3 ATR, la maggior parte delle posizioni raggiunge il tetto del 10%: a pieno tetto l'esposizione del 100% si esaurisce con circa 10 posizioni. Il backtest riporta esposizione media, posizioni massime e ingressi ridimensionati: da rivedere dopo i primi risultati (per esempio tetto al 6,7%).
- **Classificazione per parole chiave** (`core`): asset class e leva sono dedotti dal nome; per Xetra il riconoscimento è meno affidabile.

## Fasi

1. Specifica numerica (questo documento) ✅
2. Backtest sui prezzi storici, con i confronti del punto 6 (codice pronto, vedi sotto)
3. Motore di portafoglio (slot, tetti, ordinamento)
4. Scanner giornaliero (GitHub Actions) e pagina HTML: sfondo bianco, italiano, Excel di scarico, bottone Regole PDF sempre aggiornato, grafico con componente scannerv2 (dati propri, con EMA20/50 e segnale di pullback)

## Struttura

```
scripts/swing_lib.py       parametri (specifica), indicatori, filtri e segnali
scripts/fetch_prices.py    scarica lo storico dei prezzi aggiustati (snapshot unico)
scripts/backtest.py        simulazione di portafoglio, baseline e metriche
tests/test_swing.py        test su dati sintetici (python -m pytest tests -q)
.github/workflows/         fetch-prices.yml, backtest.yml (avvio manuale)
data/                      metadati (prices_meta.json, universe_swing.json)
results/                   risultati del backtest
```

Lo storico dei prezzi (`prices_snapshot.parquet`) **non è in git**: vive nella Release `prices` del repo.
In git restano solo metadati e risultati.

## Come si usa

1. **Una tantum**: Settings → Actions → General → Workflow permissions → *Read and write permissions*.
2. Actions → **Scarica prezzi** → Run workflow. Prima prova con `limit_tickers` = 50 (non pubblica nulla). Poi senza limite: circa 1-1,5 ore.
3. Actions → **Backtest** → `calibrate`: statistiche descrittive, nessun P&L. Verifica che le soglie abbiano senso.
4. Actions → **Backtest** → `explore`: simulazione sul periodo precedente al test, per controllare la pipeline. Non si ottimizza nulla.
5. Actions → **Backtest** → `test`: **una sola volta**. Esegue tutte le varianti pre-dichiarate sul periodo dal 2023-01-01 e scrive `results/test_result.json`; un secondo lancio viene rifiutato.

Il periodo di test inizia il 2023-01-01 (`--oos-start`): va confermato prima di guardare qualsiasi risultato.

## Decisioni

- Storico prezzi: snapshot completo rigenerato ogni volta (i prezzi aggiustati cambiano retroattivamente a ogni dividendo), pubblicato nella Release `prices`.
