#!/usr/bin/env python3
import os
import re
import csv
import time
import uuid
import threading
import traceback
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pymupdf
from dotenv import load_dotenv
import functools

# Désactive le buffer pour les logs
print = functools.partial(print, flush=True)
load_dotenv()

# Variables d'environnement
MAX_FILES = int(os.getenv("MAX_FILES", "10000"))  # Total à traiter
OFFSET = int(os.getenv("OFFSET", "0"))
NB_PAGES = int(os.getenv("NB_PAGES", "0"))  # 0 = toutes les pages
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))  # Taille des chunks
ROOT_DIR = "/starstock"
PATTERN = re.compile(r"ANR-(?:\d{2}-)?[A-Za-z0-9]{4,8}(?:-\d{1,4})?\b")
OUTPUT_DIR = "/output"
RUN_ID = str(uuid.uuid4())[:8]
DATE_NAME = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')

LOG_FILE = os.path.join(OUTPUT_DIR, f"{DATE_NAME}_batch_{OFFSET}_to_{OFFSET + MAX_FILES}.log")
STATE_FILE = os.path.join(OUTPUT_DIR, f"{DATE_NAME}_state.json")  # Fichier pour sauvegarder l'état
log_file = open(LOG_FILE, 'w', encoding='utf-8')

# Liste des mots-clés à exclure des noms de fichiers (insensible à la casse)
EXCLUDE_KEYWORDS = [
    'annexe', 'resume', 'résumé', 'abstract', 'errata', 'summary', 'erratum',
    'annexes', 'appendix', 'appendices', 'titre', 'couverture', 'couv',
    'cover', 'synthese', 'synthèse', 'glossaire', 'illustr', 'diff', 'image',
    'table', 'sommaire', 'remerciement', 'garde', 'planche', 'annnexe', 'annex_'
]

# Verrous pour l'écriture thread-safe
csv_writer_lock = threading.Lock()
log_writer_lock = threading.Lock()


def log(message, log_file=None):
    """Écrit un message dans les logs (console + fichier) avec verrou."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted_message = f"[{timestamp}] {message}"
    print(formatted_message)
    if log_file:
        with log_writer_lock:  # Verrou pour l'écriture
            log_file.write(formatted_message + "\n")
            log_file.flush()  # Force l'écriture

def save_state(last_processed, files_processed):
    """Sauvegarde l'état actuel dans un fichier JSON."""
    state = {
        "last_processed": last_processed,
        "files_processed": files_processed,
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)

def load_state():
    """Charge l'état depuis le fichier JSON si disponible."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def find_pdf_files_generator():
    """Génère les fichiers PDF un par un, en respectant l'ordre alphanumérique et en reprenant depuis l'état sauvegardé."""
    state = load_state()
    last_processed = state["last_processed"] if state else {"dir": None, "file": None}
    files_processed = state["files_processed"] if state else 0
    log(f"🔄 Reprise depuis l'état sauvegardé : {last_processed} ({files_processed} fichiers déjà traités)", log_file)

    # Tri alphanumérique des dossiers dans /starstock/
    for entry in sorted(os.scandir(ROOT_DIR), key=lambda e: e.name):
        if not entry.is_dir():
            continue
        log(f"📁 Exploration du répertoire : {entry.path}", log_file)
        subdir_start_time = time.time()

        # Tri alphanumérique des sous-dossiers THESE_*
        for these_dir in sorted(os.scandir(entry.path), key=lambda e: e.name):
            if not these_dir.is_dir() or not these_dir.name.startswith("THESE_"):
                continue

            these_path = os.path.join(these_dir.path, "document", "0", "0")
            if not os.path.exists(these_path):
                continue

            # Reprendre depuis le dernier dossier traité
            if last_processed["dir"] is not None and these_path < last_processed["dir"]:
                continue
            elif last_processed["dir"] == these_path:
                log(f"🔄 Reprise dans le dossier : {these_path}", log_file)

            # Tri alphanumérique des fichiers PDF
            try:
                files = sorted(os.listdir(these_path), key=lambda f: f.lower())
            except FileNotFoundError:
                continue

            # Reprendre depuis le dernier fichier traité dans ce dossier
            start_idx = 0
            if last_processed["dir"] == these_path and last_processed["file"] is not None:
                try:
                    start_idx = files.index(last_processed["file"]) + 1
                    log(f"🔄 Reprise depuis le fichier : {last_processed['file']} (index {start_idx})", log_file)
                except ValueError:
                    start_idx = 0

            for file in files[start_idx:]:
                if file.lower().endswith('.pdf'):
                    file_lower = file.lower()
                    if not any(keyword in file_lower for keyword in EXCLUDE_KEYWORDS):
                        full_path = os.path.join(these_path, file)
                        files_processed += 1
                        yield full_path, these_path, file, files_processed

                        # Sauvegarde l'état actuel
                        last_processed = {"dir": these_path, "file": file}
                        save_state(last_processed, files_processed)

                        # Arrêt si on a atteint MAX_FILES + OFFSET
                        if files_processed >= MAX_FILES:
                            return

def process_chunk(chunk, chunk_start_abs):
    """Traite un chunk de fichiers PDF avec logs détaillés."""
    CSV_FILE = os.path.join(OUTPUT_DIR, f"{DATE_NAME}_results_{chunk_start_abs}_to_{chunk_start_abs + len(chunk) - 1}.csv")
    csvfile = open(CSV_FILE, 'w', newline='', encoding='utf-8')
    total_matches = 0

    try:
        with csv_writer_lock:
            writer = csv.writer(csvfile)
            writer.writerow(["file", "match"])
            csvfile.flush()

        log(f"🔧 Début du chunk (n°{chunk_start_abs + 1} à n°{chunk_start_abs + len(chunk)}abs.)", log_file)
        chunk_start_time = datetime.now()

        with ThreadPoolExecutor(max_workers=int(os.getenv("MAX_WORKERS", "4"))) as executor:
            futures = []
            for file_count, (file_path, _, _, absolute_file_number) in enumerate(chunk, start=1):
                futures.append(executor.submit(process_file, file_path, file_count, absolute_file_number))

            for future in as_completed(futures):
                try:
                    file_path, matches, messages = future.result()
                    if matches:
                        with csv_writer_lock:
                            writer = csv.writer(csvfile)
                            for match in matches:
                                writer.writerow([file_path, match])
                            csvfile.flush()
                        total_matches += len(matches)
                except Exception as e:
                    log(f"⚠️ Erreur dans la récupération du résultat: {str(e)}", log_file)
                    continue

        chunk_duration = (datetime.now() - chunk_start_time).total_seconds()
        log(f"🎉 Chunk terminé en {chunk_duration:.2f}s | {total_matches} correspondances dans {CSV_FILE}", log_file)

    finally:
        csvfile.close()

def process_file(file_path, file_count, absolute_file_number):
    """Traite un fichier PDF avec logs détaillés."""
    start_time = time.time()
    file_path_log = f"n°{file_count} (n°{absolute_file_number}abs.) {file_path}"
    log(f"📖 Traitement de {file_path_log}", log_file)
    matches = []
    doc = None

    try:
        try:
            doc = pymupdf.open(file_path)
        except Exception as e:
            log(f"⚠️ Impossible d'ouvrir {file_path_log}: {str(e)}", log_file)
            return file_path, [], [f"Erreur: {str(e)}"]

        # Limite le nombre de pages à analyser si NB_PAGES > 0
        pages_nb_to_scan = min(len(doc), NB_PAGES) if NB_PAGES > 0 else len(doc)

        for page_num in range(pages_nb_to_scan):
            try:
                page = doc.load_page(page_num)
                text = page.get_text()
                found_matches = PATTERN.findall(text)
                if found_matches:
                    matches.extend(found_matches)
            except Exception as e:
                log(f"[DEBUG] Erreur sur la page {page_num} de {file_path_log}: {str(e)}", log_file)
                continue

        duration = time.time() - start_time
        log(f"⏱️ {file_path_log} traité en {duration:.2f}s ({pages_nb_to_scan}/{len(doc)} pages) - {len(matches)} correspondances", log_file)
        return file_path, matches, [f"Succès: {len(matches)} correspondances"]

    except Exception as e:
        error_msg = f"❌ ERREUR CRITIQUE sur {file_path_log}: {str(e)}\n{traceback.format_exc()}"
        log(error_msg, log_file)
        return file_path, [], [error_msg]

    finally:
        if doc:
            try:
                doc.close()
            except:
                pass

def main():
    script_start_time = datetime.now()
    log(f"🚀 Début du batch (ID: {RUN_ID})", log_file)

    if not os.path.exists(ROOT_DIR):
        log(f"[ERREUR] {ROOT_DIR} inaccessible.", log_file)
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    try:
        chunk = []
        chunk_start_abs = OFFSET
        state = load_state()
        files_processed = state["files_processed"] if state else 0

        for full_path, these_path, file, absolute_file_number in find_pdf_files_generator():
            chunk.append((full_path, these_path, file, absolute_file_number))

            if len(chunk) >= CHUNK_SIZE:
                process_chunk(chunk, chunk_start_abs)
                files_processed += len(chunk)
                chunk_start_abs += len(chunk)
                chunk = []

                # Arrêt si on a atteint MAX_FILES + OFFSET
                if files_processed >= MAX_FILES:
                    log(f"🎉 Traitement terminé : {files_processed} fichiers traités.", log_file)
                    break

        # Traiter le dernier chunk (même s'il est incomplet)
        if chunk:
            process_chunk(chunk, chunk_start_abs)
            files_processed += len(chunk)

        log(f"🎉 Traitement terminé : {files_processed} fichiers traités.", log_file)

    finally:
        log_file.close()

if __name__ == "__main__":
    main()
