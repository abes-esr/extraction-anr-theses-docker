#!/usr/bin/env python3
import os
import re
import csv
import time
import uuid
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pymupdf
from dotenv import load_dotenv
import functools
import sys

# Désactive le buffer pour les logs
print = functools.partial(print, flush=True)
load_dotenv()

# Variables d'environnement
MAX_FILES = int(os.getenv("MAX_FILES", "1000"))
OFFSET = int(os.getenv("OFFSET", "0"))
NB_PAGES = int(os.getenv("NB_PAGES", "0"))  # 0 = toutes les pages
ROOT_DIR = "/starstock"
PATTERN = re.compile(r"ANR-(?:\d{2}-)?[A-Za-z0-9]{4,8}(?:-\d{1,4})?\b")
OUTPUT_DIR = "/output"
RUN_ID = str(uuid.uuid4())[:8]
DATE_NAME = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
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


def find_pdf_files(batch_size):
    """Génère des lots de fichiers PDF depuis /starstock/*/THESE_*/document/0/0/"""
    pdf_files = []
    all_eligible_files = []

    for entry in os.scandir(ROOT_DIR):
        if entry.is_dir():
            for root, dirs, files in os.walk(entry.path, followlinks=False):
                rel_path = os.path.relpath(root, entry.path)
                parts = rel_path.split(os.sep)
                if (len(parts) >= 4 and
                        parts[0].startswith("THESE_") and
                        parts[1] == "document" and
                        parts[2] == "0" and
                        parts[3] == "0"):

                    for file in files:
                        if file.lower().endswith('.pdf'):
                            file_lower = file.lower()
                            if not any(keyword in file_lower for keyword in EXCLUDE_KEYWORDS):
                                all_eligible_files.append(os.path.join(root, file))

                    all_eligible_files.sort()

                    pdf_files = []
                    for full_path in all_eligible_files[OFFSET:OFFSET + batch_size]:
                        pdf_files.append(full_path)
                        if len(pdf_files) >= batch_size:
                            yield pdf_files
                            pdf_files = []

    if pdf_files:
        yield pdf_files


def process_file(file_path, file_count):
    start_time = time.time()
    try:
        message = f"📖 Traitement de {file_path}"
        print(message)

        try:
            doc = pymupdf.open(file_path)
        except Exception as e:
            message = f"⚠️ Impossible d'ouvrir {file_path}: {str(e)}"
            print(message)
            return file_path, [], [message]

        matches = []
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
                print(f"[DEBUG] Erreur sur la page {page_num} de {file_path}: {str(e)}")
                continue

        duration = time.time() - start_time
        absolute_file_number = file_count + OFFSET
        message = f"⏱️  n°{file_count} (n°{absolute_file_number}abs.) {file_path} traité en {duration:.2f}s ({pages_nb_to_scan}/{len(doc)} pages) - {len(matches)} matches"
        print(message)
        return file_path, matches, [message]
    except Exception as e:
        message = f"⚠️  Erreur sur {file_path}: {e}"
        print(message)
        return file_path, [], [message]
    finally:
        doc.close()


def main():
    if not os.path.exists("/starstock"):
        print(f"[ERREUR] Le répertoire /starstock n'est pas monté ou inaccessible.")
        return

    if not os.listdir("/starstock"):
        print(f"[AVERTISSEMENT] /starstock est vide ou ne contient pas de sous-répertoires.")

    script_start_time = datetime.now()
    print(f"[{script_start_time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 Début du batch (ID: {RUN_ID})")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    CSV_FILE = os.path.join(OUTPUT_DIR, f"{DATE_NAME}_results_{OFFSET}_to_{OFFSET + MAX_FILES}.csv")
    LOG_FILE = os.path.join(OUTPUT_DIR, f"{DATE_NAME}_batch_{OFFSET}_to_{OFFSET + MAX_FILES}.log")
    total_matches = 0

    # Ouvre les fichiers une fois pour toute la durée du script
    csvfile = open(CSV_FILE, 'w', newline='', encoding='utf-8')
    log_file = open(LOG_FILE, 'w', encoding='utf-8')

    try:
        # Écrit l'en-tête du CSV avec verrou
        with csv_writer_lock:
            writer = csv.writer(csvfile)
            writer.writerow(["file", "match"])
            csvfile.flush()  # Force l'écriture

        log(f"📦 Début du batch (ID: {RUN_ID})", log_file=log_file)

        # Récupère le batch unique
        batch = next(find_pdf_files(MAX_FILES), [])
        if not batch:
            log(f"[INFO] Aucun fichier à traiter.", log_file=log_file)
            return

        batch_start_time = datetime.now()
        log(f"📦 Début à {datetime.now().strftime('%H:%M:%S')}", log_file=log_file)

        futures = []
        with ThreadPoolExecutor(max_workers=int(os.getenv("MAX_WORKERS", "4"))) as executor:
            file_count = 0
            for file in batch:
                file_count += 1
                futures.append(executor.submit(process_file, file, file_count))

            for future in as_completed(futures):
                file_path, matches, messages = future.result()
                for msg in messages:
                    log(msg, log_file=log_file)

                if matches:
                    with csv_writer_lock:
                        writer = csv.writer(csvfile)
                        for match in matches:
                            writer.writerow([file_path, match])
                        csvfile.flush()  # Force l'écriture
                    total_matches += len(matches)

        batch_end_time = datetime.now()
        batch_duration = (batch_end_time - batch_start_time).total_seconds()
        log(f"[{batch_end_time.strftime('%Y-%m-%d %H:%M:%S')}] 🎉 Lot terminé", log_file=log_file)
        log(f"⏳ Durée totale: {batch_duration:.2f} secondes | {total_matches} correspondances trouvées dans {CSV_FILE}",
            log_file=log_file)
    finally:
        csvfile.close()
        log_file.close()


if __name__ == "__main__":
    main()
