# swing

Sistema di swing trading **daily**, **solo long**, su ETF, ETP ed ETC **non a leva**.
Segue il trend, entra su pullback con conferma, controlla il rischio. Genera segnali: la decisione resta discrezionale.

Stato: **fase 1 completata (specifica approvata)**. Prossima fase: backtest.

## Principi

- Le soglie qui sotto sono fissate **prima** di guardare qualsiasi risultato e **non si ottimizzano** sul backtest.
- La lista degli strumenti è letta da [`core`](https://github.com/Giorgiogoldoni/core) (`data/tickers_universe.json`).
  Prezzi, indicatori e segnali sono calcolati da `swing`: gli score di `core` non vengono usati.
- Prezzi giornalieri aggiustati (default yfinance). Il segnale si calcola sulla chiusura del giorno *t*.

## 1. Universo e filtri

| Regola | Definizione |
|---|---|
| Strumenti | ETF, ETP, ETC; `is_leveraged` falso; cripto, monetari e short esclusi. Obbligazionari inclusi |
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
- Confronti obbligatori: universo equamente pesato in acquisto e mantenimento; entrata casuale con gli stessi filtri di trend e le stesse uscite.
- Metriche: numero di trade, aspettativa in R, profit factor, drawdown massimo (simulazione con 15 slot), risultati per anno e per classe di attività.
- Criteri di accettazione: oltre 150 trade, aspettativa > +0,15R dopo i costi, profit factor > 1,3, drawdown massimo < 15%.
- Unica variante pre-dichiarata: stesso sistema **senza** il filtro R/R.

## Limiti noti

- **Survivorship bias**: l'universo è la lista di oggi; gli ETF chiusi o delistati mancano. I risultati sono un limite superiore.
- **Dati Open**: per ETF illiquidi (soprattutto Xetra) l'apertura di yfinance può coincidere con la chiusura precedente; se il problema è diffuso, si rivede il requisito del corpo rialzista.
- **Filtro R/R**: scarta i pullback poco profondi nei trend forti (ingresso vicino ai massimi a 60 barre).
- **Classificazione per parole chiave** (`core`): asset class e leva sono dedotti dal nome; per Xetra il riconoscimento è meno affidabile.

## Fasi

1. Specifica numerica (questo documento) ✅
2. Backtest sui prezzi storici, con i confronti del punto 6
3. Motore di portafoglio (slot, tetti, ordinamento)
4. Scanner giornaliero (GitHub Actions) e pagina HTML: sfondo bianco, italiano, Excel di scarico, bottone Regole PDF sempre aggiornato, grafico con componente scannerv2 (dati propri, con EMA20/50 e segnale di pullback)

## Da decidere

- Dove tenere lo storico dei prezzi senza gonfiare la storia git
