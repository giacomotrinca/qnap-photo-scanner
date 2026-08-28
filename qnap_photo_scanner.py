#!/usr/bin/env python3
"""
qnap_photo_scanner
==================

Organizza una libreria di foto in una struttura `Anno/Mese`, rilevando e
mettendo in quarantena i **duplicati** (hash MD5) e le **foto sfocate**
(varianza del Laplaciano via Pillow+numpy, senza OpenCV).

Questo progetto nasce dall'esperienza reale di riorganizzare i file di un NAS
QNAP con una glibc molto vecchia (2.21) dove OpenCV e Miniconda non sono
installabili: il rilevamento della sfocatura usa quindi una convoluzione numpy
del kernel Laplaciano 3x3, compatibile con qualsiasi ambiente con Pillow+numpy.

Uso:
    python qnap_photo_scanner.py --dry-run
    python qnap_photo_scanner.py --source /path/to/foto --target /path/to/output
    python qnap_photo_scanner.py --cartella "NOME" --dry-run
"""

import os
import sys
import shutil
import hashlib
import csv
import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import logging

# ---------------------------------------------------------------------------
# Costanti di default (sovrascrivibili da CLI e config)
# ---------------------------------------------------------------------------
DEFAULT_SOURCE = Path("/share/Multimedia/pictures")
DEFAULT_BLUR_THRESHOLD = 100
IMAGE_FORMATS = {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp', '.gif'}
VIDEO_FORMATS = {'.mp4', '.mov', '.avi', '.mkv', '.wmv'}
SPECIAL_DIRS = {'quarantena', '.venv', '.opencode'}

# ---------------------------------------------------------------------------
# Stato globale modificato da CLI
# ---------------------------------------------------------------------------
DRY_RUN = False
BLUR_THRESHOLD = DEFAULT_BLUR_THRESHOLD
SOURCE_DIR: Path = DEFAULT_SOURCE


def build_last_dirs(source: Path) -> Tuple[Path, Path, Path, Path]:
    """Ritorna le cartelle chiave derivate dalla cartella sorgente."""
    quarantena = source / "quarantena"
    return (
        quarantena,
        quarantena / "duplicati",
        quarantena / "sfocate",
    )


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )


def calcola_hash(file_path: Path) -> str:
    """Calcola hash MD5 del file, leggendo a blocchi."""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
    except Exception:
        return ""
    return hash_md5.hexdigest()


def leggi_exif_data(file_path: Path) -> Optional[datetime]:
    """Estrae la data di scatto (EXIF) con Pillow."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        with Image.open(file_path) as img:
            try:
                exif_data = img._getexif()
            except Exception:
                return None
            if exif_data:
                for tag_id, value in exif_data.items():
                    tag = TAGS.get(tag_id, tag_id)
                    if tag in ('DateTimeOriginal', 'DateTime'):
                        try:
                            return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
                        except Exception:
                            pass
    except Exception:
        pass
    return None


def ottieni_data_file(file_path: Path) -> datetime:
    """Data del file: EXIF se presente, altrimenti data di modifica."""
    data_exif = leggi_exif_data(file_path)
    if data_exif:
        return data_exif
    try:
        return datetime.fromtimestamp(os.path.getmtime(file_path))
    except Exception:
        return datetime.fromtimestamp(0)


def controlla_sfocatura(file_path: Path) -> bool:
    """True se l'immagine è sfocata: varianza Laplaciano sotto soglia.
    Implementazione Pillow+numpy (nessuna dipendenza da OpenCV)."""
    try:
        import numpy as np
        from PIL import Image
        img = Image.open(file_path).convert("L")
        # Ridimensiona per velocizzare su immagini grandi
        if max(img.size) > 1000:
            img.thumbnail((1000, 1000))
        arr = np.asarray(img, dtype=np.float64)
        if arr.shape[0] < 4 or arr.shape[1] < 4:
            return False
        # Convoluzione Laplaciana (kernel 3x3) via numpy
        lap = (arr[1:-1, 1:-1] * 4 - arr[:-2, 1:-1] - arr[2:, 1:-1]
               - arr[1:-1, :-2] - arr[1:-1, 2:])
        return lap.var() < BLUR_THRESHOLD
    except Exception:
        return False


def is_immagine(file_path: Path) -> bool:
    return file_path.suffix.lower() in IMAGE_FORMATS


def sposta_file(file_path: Path, destinazione: Path) -> bool:
    try:
        if DRY_RUN:
            logging.getLogger(__name__).info(
                f"[DRY-RUN] Sposterei: {file_path} -> {destinazione}")
            return True
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        if destinazione.exists():
            stem = destinazione.stem
            suffix = destinazione.suffix
            counter = 1
            while destinazione.exists():
                destinazione = destinazione.parent / f"{stem}_{counter}{suffix}"
                counter += 1
        shutil.move(str(file_path), str(destinazione))
        return True
    except Exception as e:
        logging.getLogger(__name__).error(f"Errore spostamento {file_path}: {e}")
        return False


def stato_path() -> Path:
    return SOURCE_DIR / "stato_organizzazione.json"


def report_path() -> Path:
    return SOURCE_DIR / "report_organizzazione.csv"


def carica_stato() -> Dict:
    if stato_path().exists():
        try:
            with open(stato_path(), 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"processate": [], "da_elaborare": []}


def salva_stato(stato: Dict) -> None:
    try:
        with open(stato_path(), 'w', encoding='utf-8') as f:
            json.dump(stato, f, indent=2)
    except Exception as e:
        logging.getLogger(__name__).error(f"Errore salvataggio stato: {e}")


def crea_struttura_cartelle() -> None:
    _, dup_dir, sfo_dir = build_last_dirs(SOURCE_DIR)
    dup_dir.mkdir(parents=True, exist_ok=True)
    sfo_dir.mkdir(parents=True, exist_ok=True)


def processa_lista_file(file_list: List[Path], contenitore: Path) -> None:
    """Rileva duplicati, poi sfocate, poi riorganizza il resto in Anno/Mese."""
    logger = logging.getLogger(__name__)
    if not file_list:
        return

    # 1. Rileva duplicati (MD5)
    files_per_hash: Dict[str, List[Path]] = {}
    for file_path in file_list:
        try:
            hash_val = calcola_hash(file_path)
            if hash_val:
                files_per_hash.setdefault(hash_val, []).append(file_path)
        except Exception:
            pass

    quarantena_dir = build_last_dirs(SOURCE_DIR)[0]

    # 1. Rileva duplicati (MD5): ogni hash con piu' di un file -> copie in quarantena
    moved: Set[Path] = set()
    for hash_val, paths in files_per_hash.items():
        if len(paths) > 1:
            for dup in paths[1:]:
                anno = ottieni_data_file(dup).year
                mese = ottieni_data_file(dup).month
                dest = (quarantena_dir / "duplicati"
                        / f"{anno:04d}" / f"{mese:02d}" / dup.name)
                if sposta_file(dup, dest):
                    moved.add(dup)
                    logger.info(f"  [DUPLICATO] {dup.name} -> quarantena/duplicati")

    # Residui = file non ancora spostati e ancora presenti sul disco
    residui: List[Path] = [f for f in file_list
                           if f not in moved and f.exists()]

    # 2. Foto sfocate (solo immagini e solo sulle copie rimaste)
    for file_path in residui:
        if not is_immagine(file_path):
            continue
        if controlla_sfocatura(file_path):
            anno = ottieni_data_file(file_path).year
            mese = ottieni_data_file(file_path).month
            dest = (quarantena_dir / "sfocate"
                    / f"{anno:04d}" / f"{mese:02d}" / file_path.name)
            if sposta_file(file_path, dest):
                moved.add(file_path)
                logger.info(f"  [SFOCATA] {file_path.name} -> quarantena/sfocate")

    # 3. Riorganizza i rimanenti in Anno/Mese
    for file_path in residui:
        if file_path in moved:
            continue
        data = ottieni_data_file(file_path)
        anno, mese = data.year, data.month
        dest_dir = SOURCE_DIR / f"{anno:04d}" / f"{mese:02d}"
        if file_path.parent == dest_dir:
            continue  # già nel posto giusto
        dest = dest_dir / file_path.name
        if sposta_file(file_path, dest):
            logger.info(f"  [SPOSTATO] {file_path.name} -> {anno}/{mese:02d}/")


def elabora_cartella(cartella: Path) -> None:
    logger = logging.getLogger(__name__)
    file_list: List[Path] = []
    dirs_ricorsive: List[Path] = []

    try:
        for entry in cartella.iterdir():
            if entry.is_file():
                if entry.suffix.lower() in (IMAGE_FORMATS | VIDEO_FORMATS):
                    file_list.append(entry)
            elif entry.is_dir() and not entry.name.startswith('.'):
                dirs_ricorsive.append(entry)
    except PermissionError:
        logger.error(f"Permesso negato: {cartella}")
        return
    except Exception as e:
        logger.error(f"Errore accesso {cartella}: {e}")
        return

    logger.info(f"  Trovati {len(file_list)} file e {len(dirs_ricorsive)} sottocartelle")

    processa_lista_file(file_list, cartella)

    for subdir in dirs_ricorsive:
        logger.info(f"  Sottocartella: {subdir.name}")
        subfile_list: List[Path] = []
        try:
            for entry in subdir.rglob('*'):
                if entry.is_file() and entry.suffix.lower() in (IMAGE_FORMATS | VIDEO_FORMATS):
                    subfile_list.append(entry)
        except Exception as e:
            logger.error(f"  Errore in sottocartella {subdir.name}: {e}")
            continue
        processa_lista_file(subfile_list, subdir)


def processa_cartelle_incrementalmente(cartella_specifica: Optional[str] = None) -> None:
    logger = logging.getLogger(__name__)

    if cartella_specifica:
        cartella_path = SOURCE_DIR / cartella_specifica
        if not cartella_path.is_dir():
            logger.error(f"Cartella '{cartella_specifica}' non trovata")
            return
        cartelle = [cartella_path]
    else:
        cartelle: List[Path] = []
        try:
            for entry in SOURCE_DIR.iterdir():
                if entry.is_dir() and not entry.name.startswith('.'):
                    if entry.name not in SPECIAL_DIRS:
                        cartelle.append(entry)
        except Exception as e:
            logger.error(f"Errore elenco cartelle: {e}")
            return

    stato = carica_stato()
    processate = set(stato.get("processate", []))
    cartelle_da_processare = [c for c in cartelle if c.name not in processate]

    logger.info(f"Cartelle totali: {len(cartelle)}")
    logger.info(f"Cartelle già processate: {len(processate)}")
    logger.info(f"Cartelle da processare: {len(cartelle_da_processare)}")

    for counter, cartella in enumerate(cartelle_da_processare, 1):
        logger.info("=" * 60)
        logger.info(f"[{counter}/{len(cartelle_da_processare)}] Processo cartella: {cartella.name}")
        logger.info("=" * 60)
        try:
            elabora_cartella(cartella)
            if not DRY_RUN:
                processate.add(cartella.name)
                stato["processate"] = list(processate)
                salva_stato(stato)
        except KeyboardInterrupt:
            logger.info(f"Interrotto durante la cartella {cartella.name}")
            if not DRY_RUN:
                salva_stato(stato)
            sys.exit(1)
        except Exception as e:
            logger.error(f"Errore nella cartella {cartella.name}: {e}")
            continue

    logger.info("=" * 60)
    logger.info("ELABORAZIONE COMPLETATA")
    logger.info(f"Cartelle processate: {len(processate)}")
    logger.info("=" * 60)


def aggiorna_report() -> None:
    logger = logging.getLogger(__name__)
    report = []
    for line in _iter_log_lines():
        if '[DUPLICATO]' in line:
            report.append({'azione': 'duplicato', 'dettaglio': line.strip()})
        elif '[SFOCATA]' in line:
            report.append({'azione': 'sfocata', 'dettaglio': line.strip()})
        elif '[SPOSTATO]' in line:
            report.append({'azione': 'riorganizzato', 'dettaglio': line.strip()})

    try:
        out = report_path()
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=['azione', 'dettaglio'])
            writer.writeheader()
            writer.writerows(report)
        logger.info(f"Report salvato in: {out}")
    except Exception as e:
        logger.error(f"Errore report: {e}")


def _iter_log_lines(requested_log: Optional[Path] = None):
    log_file = requested_log or (SOURCE_DIR / "organizzazione.log")
    if not log_file.exists():
        return
    with open(log_file, 'r', encoding='utf-8') as f:
        for line in f:
            yield line


def main(argv: Optional[List[str]] = None) -> int:
    global DRY_RUN, BLUR_THRESHOLD, SOURCE_DIR

    parser = argparse.ArgumentParser(
        description='Organizza foto in struttura Anno/Mese (QNAP-friendly)')
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE,
                        help=f'Cartella radice da elaborare (default: {DEFAULT_SOURCE})')
    parser.add_argument('--dry-run', action='store_true',
                        help='Mostra cosa verrebbe fatto senza modificare nulla')
    parser.add_argument('--soglia-sfocatura', type=int, default=DEFAULT_BLUR_THRESHOLD,
                        help=f'Soglia per foto sfocate (default: {DEFAULT_BLUR_THRESHOLD})')
    parser.add_argument('--cartella', type=str, default=None,
                        help='Processa solo una cartella specifica')
    parser.add_argument('--reset-stato', action='store_true',
                        help='Ripristina lo stato e riprocessa tutte le cartelle')
    args = parser.parse_args(argv)

    DRY_RUN = args.dry_run
    BLUR_THRESHOLD = args.soglia_sfocatura
    SOURCE_DIR = args.source

    setup_logging(SOURCE_DIR / "organizzazione.log")

    if args.reset_stato:
        if stato_path().exists():
            stato_path().unlink()
            logging.getLogger(__name__).info("Stato resettato")

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("ORGANIZZAZIONE FOTO")
    logger.info(f"Sorgente: {SOURCE_DIR}")
    if DRY_RUN:
        logger.info("MODALITÀ DRY-RUN: Nessuna modifica verrà effettuata")
    else:
        logger.info("MODALITÀ REALE: I file verranno spostati")
    logger.info(f"Soglia sfocatura: {BLUR_THRESHOLD}")
    logger.info("=" * 60)

    crea_struttura_cartelle()
    processa_cartelle_incrementalmente(args.cartella)
    aggiorna_report()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Operazione interrotta dall'utente")
        sys.exit(1)
    except Exception as e:
        logging.getLogger(__name__).error(f"Errore fatale: {e}")
        sys.exit(1)
