# qnap-photo-scanner

Organizza una libreria di foto in una struttura `Anno/Mese`, rilevando e mettendo in **quarantena** i **duplicati** (hash MD5) e le **foto sfocate** (varianza del Laplaciano via Pillow+numpy, **senza OpenCV**).

Nasce dall'esperienza reale di riorganizzare i file di un NAS QNAP (modello TS-X53B, QTS 5.2.10) con una **glibc molto vecchia (2.21)**, dove Miniconda e OpenCV non sono installabili. Lo script usa quindi solo `Pillow` e `numpy`, adatti anche ad ambienti EntWare/opkg su QNAP.

---

## Funzionalità

- **Riorganizzazione** dei file in `Anno/Mese/` usando la data di scatto **EXIF** (`DateTimeOriginal` / `DateTime`), con fallback sulla data di modifica.
- **Rilevamento duplicati**: confronto tramite **hash MD5** (lettura a blocchi). I duplicati (tutti tranne il primo per ogni hash) vengono spostati in `quarantena/duplicati/`.
- **Rilevamento sfocatura**: **varianza del Laplaciano** calcolata con una convoluzione numpy (kernel 3x3). Le immagini sfocate finiscono in `quarantena/sfocate/`.
- **Incrementale e ripronevole**: tieni traccia delle cartelle già elaborate in `stato_organizzazione.json`, così un'interruzione non fa riprocessare tutto.
- **Dry-run**: modalità di prova che mostra cosa verrebbe fatto senza toccare nulla.
- **Report CSV** riassuntivo delle operazioni (`report_organizzazione.csv`).

> ⚠️ Nulla viene mai **eliminato**: tutto ciò che non è collocabile con certezza viene **spostato** in quarantena, lasciando la decisione finale a un controllo umano.

---

## Struttura output

Data una cartella sorgente, lo script crea/stila:

```
<source>/
├── 2009/10/               # es. foto scattate a ottobre 2009
├── 2021/03/
├── ...
├── quarantena/
│   ├── duplicati/2021/03/...
│   └── sfocate/2021/05/...
├── organizzazione.log      # log dettagliato
├── report_organizzazione.csv
└── stato_organizzazione.json
```

---

## Requisiti

- **Python 3.8+**
- **Pillow**
- **numpy**

### Installazione dipendenze (locale)

```bash
pip install -r requirements.txt
```

> **Su QNAP (via EntWare/opkg)** la glibc vecchia blocca le wheel moderne: installa i pacchetti EntWare
> `python3`, `python3-pillow`, `python3-numpy` e assicurati che la `TMPDIR` punti a uno spazio dati
> (es. `/share/CACHEDEV1_DATA/...`) perché `/tmp` sul QNAP è una tmpfs piccola.

---

## Uso

```bash
# Eseguire su una cartella sorgente
python qnap_photo_scanner.py --source /percorso/alle/foto

# Prova senza modificare nulla
python qnap_photo_scanner.py --source /percorso/alle/foto --dry-run

# Processa solo una sottocartella ("NOME")
python qnap_photo_scanner.py --source /percorso/alle/foto --cartella NOME --dry-run

# Regola la soglia di sfocatura
python qnap_photo_scanner.py --source /percorso/alle/foto --soglia-sfocatura 150

# Ripristina lo stato e riprocessa tutto
python qnap_photo_scanner.py --source /percorso/alle/foto --reset-stato
```

### Opzioni CLI

| Opzione | Default | Descrizione |
|---|---|---|
| `--source PATH` | `/share/Multimedia/pictures` | Cartella radice da elaborare |
| `--dry-run` | off | Simula senza spostare nulla |
| `--soglia-sfocatura N` | `100` | Soglia varianza Laplaciano (più bassa = meno "sfocate") |
| `--cartella NOME` | — | Elabora una sola sottocartella |
| `--reset-stato` | off | Cancella lo stato e riprocessa tutto |

> In ambiente QNAP, prima di lanciare set `TMPDIR` su spazio dati:
> ```bash
> export TMPDIR=/share/CACHEDEV1_DATA/entware/tmp
> ```

---

## Esempio di esecuzione

```
$ python qnap_photo_scanner.py --source /share/Multimedia/pictures --dry-run
============================================================
ORGANIZZAZIONE FOTO
Sorgente: /share/Multimedia/pictures
MODALITÀ DRY-RUN: Nessuna modifica verrà effettuata
Soglia sfocatura: 100
============================================================
Cartelle totali: 334
Cartelle già processate: 1
Cartelle da processare: 333
============================================================
[1/333] Processo cartella: Catalogo Foto
============================================================
  Trovati 0 file e 1 sottocartelle
  Sottocartella: Photos Library.photoslibrary
  [DUPLICATO] img_001.jpg -> quarantena/duplicati
  [SFOCATA] img_002.jpg -> quarantena/sfocate
  [SPOSTATO] img_003.jpg -> 2021/03/
```

---

## Come funziona il rilevamento sfocatura

La **varianza del Laplaciano** è una metrica classica di nitidezza: un'immagine nitida ha bordi netti che producono una varianza alta, una sfocata una varianza bassa. In `controlla_sfocatura()`:

1. L'immagine è convertita in scala di grigi e ridimensionata a max 1000px (per velocità).
2. Si applica un **kernel Laplaciano 3x3** via `numpy` (nessuna dipendenza OpenCV).
3. Se la varianza risultante è **sotto soglia** (`--soglia-sfocatura`, default `100`), l'immagine è considerata sfocata.

---

## Limitazioni note

- Elaborazione **volutamente lenta** per non sovraccaricare il NAS (accettato nel caso d'uso originale).
- La ricorsione processa i file presenti in sottocartelle di ogni cartella di primo livello (utile per librerie come `Photos Library.photoslibrary`), ma le cartelle di **secondo livello** non vengono enumerate come "cartelle da processare".
- Il calcolo hash MD5 di librerie molto grandi (es. decine di migliaia di file) può richiedere tempo.

---

## Struttura del repo

```
qnap_photo_scanner/
├── qnap_photo_scanner.py   # script principale (CLI)
├── requirements.txt        # Pillow + numpy
├── pyproject.toml          # metadati + entry point
├── LICENSE                 # MIT
└── README.md
```

---

## Contribuire

Issue e pull request sono benvenuti. Prima di intervenire su grandi modifiche, apri una issue per discutere l'approccio.

---

## Note di progetto / changelog

### v1.0.1 — Fix riorganizzazione doppia + TMPDIR (agosto 2026)

Questa versione risolve un piccolo bug ereditato dallo script originale usato sul NAS QNAP e ripristina un'impostazione di ambiente fondamentale per quel dispositivo.

**Bug corretto — elaborazione doppia dei file:**

Nella versione originale, in `processa_lista_file()` le tre fasi (duplicati → sfocate → riorganizzazione) iteravano ognuna sull'intera `file_list`. Una foto **sfocata**, una volta spostata in quarantena nella fase 2, **non veniva esclusa** dalla fase 3 di riorganizzazione: la fase 3 ritentava quindi `shutil.move` su un file che non esisteva più sul disco.

In **dry-run** questo generava log duplicati e voci `[SPOSTATO]` spure; in **modalità reale** era innocuo ai fini dei dati (il file non veniva perso né danneggiato, solo loggato un `Errore spostamento`), ma l'output risultava sporco e poco affidabile come report.

Fix implementato: ogni fase opera ora solo sui **residui** (file non ancora spostati e ancora presenti sul disco), tenendo traccia dei file già mossi (`moved`). Il risultato è un report pulito e senza voci spurie.

**Ripristino `TMPDIR`:**

Durante il refactoring era andata persa l'impostazione
```python
os.environ.setdefault("TMPDIR", "/share/CACHEDEV1_DATA/entware/tmp")
```
sul NAS QNAP `/tmp` è una **tmpfs da ~64MB, spesso piena**: senza questo fallback i file temporanei (e gli here-doc) possono fallire. È stato reinserito come `setdefault` (l'ambiente dell'utente può comunque sovrascriverlo).

---

## Licenza

MIT © 2026 Giacomo Trinca Cintioli — vedi [LICENSE](LICENSE).
