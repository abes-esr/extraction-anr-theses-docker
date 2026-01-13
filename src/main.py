#!/usr/bin/env python3
import os
import re
import csv
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import fitz  # PyMuPDF
from dotenv import load_dotenv
import functools
import sys

# Désactive le buffer pour les logs
print = functools.partial(print, flush=True)
load_dotenv()

# Variables d'environnement
MAX_FILES = int(os.getenv("MAX_FILES", "1000"))
OFFSET = int(os.getenv("OFFSET", "0"))
ROOT_DIR = "/starstock"
PATTERN = re.compile(r"ANR-(?:\d{2}-)?[A-Za-z0-9]{4,8}(?:-\d{1,4})?\b")
OUTPUT_DIR = "/output"
CSV_FILE = os.path.join(OUTPUT_DIR, f"results_{OFFSET}_to_{OFFSET + MAX_FILES}.csv")

def log(message, log_file=None):
    """Écrit un message dans les logs (console + fichier spécifique)."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted_message = f"[{timestamp}] {message}"
    print(formatted_message)  # Affiche toujours dans la console
    if log_file:
        log_file.write(formatted_message + "\n")  # Écrit dans le fichier de log du batch

def find_pdf_files_in_batches(batch_size):
    """Génère des lots de fichiers PDF depuis /starstock/*/THESE_*/document/0/0/"""
    pdf_files = []
    processed_files = 0

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
                            full_path = os.path.join(root, file)
                            if processed_files >= OFFSET:
                                pdf_files.append(full_path)
                                if len(pdf_files) >= batch_size:
                                    yield pdf_files
                                    pdf_files = []
                            processed_files += 1

    if pdf_files:  # Dernier lot
        yield pdf_files

def process_file(file_path):
    start_time = time.time()
    try:
        message = f"📖 Traitement de {file_path}"
        print(message)

        doc = fitz.open(file_path)
        matches = []

        for page in doc:
            text = page.get_text()
            found_matches = PATTERN.findall(text)
            if found_matches:
                matches.extend(found_matches)

        doc.close()

        duration = time.time() - start_time
        message = f"⏱️ {os.path.basename(file_path)} traité en {duration:.2f}s ({len(doc)} pages) - {len(matches)} matches"
        print(message)
        return file_path, matches, [message]
    except Exception as e:
        message = f"⚠️ Erreur sur {file_path}: {e}"
        print(message)
        return file_path, [], [message]

def main():
    script_start_time = datetime.now()
    print(f"[{script_start_time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 Début du script")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    total_matches = 0
    batch_count = 0

    with open(CSV_FILE, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["file", "match"])

        for batch in find_pdf_files_in_batches(MAX_FILES):
            batch_count += 1
            LOG_FILE = os.path.join(OUTPUT_DIR, f"logs_batch_{batch_count}_offset_{OFFSET}.txt")
            batch_start_time = datetime.now()

            with open(LOG_FILE, 'w', encoding='utf-8') as log_file:
                log(f"📦 Lot {batch_count} ({len(batch)} fichiers) - Début à {batch_start_time.strftime('%H:%M:%S')}", log_file=log_file)

                futures = []
                with ThreadPoolExecutor(max_workers=8) as executor:
                    for file in batch:
                        futures.append(executor.submit(process_file, file))

                    for future in as_completed(futures):
                        file_path, matches, messages = future.result()
                        for msg in messages:
                            log(msg, log_file=log_file)
                        if matches:
                            total_matches += len(matches)
                            for match in matches:
                                writer.writerow([file_path, match])

                batch_end_time = datetime.now()
                batch_duration = (batch_end_time - batch_start_time).total_seconds()
                log(f"⏱️ Lot {batch_count} terminé à {batch_end_time.strftime('%H:%M:%S')} (durée: {batch_duration:.2f} secondes)", log_file=log_file)

    script_end_time = datetime.now()
    script_duration = (script_end_time - script_start_time).total_seconds()
    print(f"[{script_end_time.strftime('%Y-%m-%d %H:%M:%S')}] 🎉 Script terminé")
    print(f"[{script_end_time.strftime('%Y-%m-%d %H:%M:%S')}] ⏳ Durée totale: {script_duration:.2f} secondes | {total_matches} correspondances trouvées dans {CSV_FILE}")

    if len(batch) == MAX_FILES:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔄 Relancez avec OFFSET={OFFSET + MAX_FILES} pour continuer.")

if __name__ == "__main__":
    main()
