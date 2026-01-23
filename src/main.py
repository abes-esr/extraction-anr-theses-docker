#!/usr/bin/env python3
import os
import re
import csv
import time
import uuid
import threading
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pymupdf
from dotenv import load_dotenv
import functools

# Désactive le buffer pour les logs
print = functools.partial(print, flush=True)
load_dotenv()

# Variables d'environnement
MAX_FILES = int(os.getenv("MAX_FILES", "1000"))  # Total à traiter
OFFSET = int(os.getenv("OFFSET", "0"))
NB_PAGES = int(os.getenv("NB_PAGES", "0"))  # 0 = toutes les pages
CHUNK_SIZE = 1000
ROOT_DIR = "/starstock"
PATTERN = re.compile(r"ANR-(?:\d{2}-)?[A-Za-z0-9]{4,8}(?:-\d{1,4})?\b")
OUTPUT_DIR = "/output"
RUN_ID = str(uuid.uuid4())[:8]
DATE_NAME = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')

LOG_FILE = os.path.join(OUTPUT_DIR, f"{DATE_NAME}_batch_{OFFSET}_to_{OFFSET + MAX_FILES}.log")
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


def find_pdf_files():
    """Génère des chunks de fichiers PDF triés par sous-répertoire, puis par nom de fichier, en s'arrêtant à OFFSET + MAX_FILES."""
    all_eligible_files = []
    log(f"🔍 Début du scan des fichiers PDF (offset={OFFSET}, max={MAX_FILES})", log_file)
    max_needed = OFFSET + MAX_FILES  # Nombre maximal de fichiers nécessaires à l'élaboration de la liste à traiter

    start_time = time.time()
    for i, entry in enumerate(os.scandir(ROOT_DIR)):
        if not entry.is_dir():
            continue
        log(f"📁 [{i+1}] Exploration du répertoire : {entry.path}", log_file)
        subdir_start_time = time.time()

        for root, dirs, files in os.walk(entry.path, followlinks=False):
            rel_path = os.path.relpath(root, entry.path)
            parts = rel_path.split(os.sep)

            if len(parts) >= 4 and\
                    parts[0].startswith("THESE_") and\
                    parts[1] == "document" and\
                    parts[2] == "0" and\
                    parts[3] == "0":
                log(f"📂 Trouvé structure valide : {os.path.join(entry.path, rel_path)}", log_file)
                pdf_count = 0
                for file in sorted(files):  # Tri alphabétique local
                    if file.lower().endswith('.pdf'):
                        file_lower = file.lower()
                        if not any(keyword in file_lower for keyword in EXCLUDE_KEYWORDS):
                            full_path = os.path.join(root, file)
                            all_eligible_files.append(full_path)
                            pdf_count += 1

                            # Arrêt si on a assez de fichiers
                            if len(all_eligible_files) >= max_needed:
                                break  # Sort de la boucle de fichiers

                if pdf_count > 0:
                    log(f"📄 {pdf_count} fichiers PDF éligibles trouvés dans {os.path.join(entry.path, rel_path)}", log_file)

                if len(all_eligible_files) >= max_needed:
                    break  # Sort de la boucle os.walk

        subdir_duration = time.time() - subdir_start_time
        log(f"⏱️ Répertoire {entry.path} scanné en {subdir_duration:.2f}s", log_file)

        if len(all_eligible_files) >= max_needed:
            break  # Sort de la boucle des répertoires

    total_eligible = len(all_eligible_files)
    log(f"📊 {total_eligible} fichiers PDF éligibles trouvés (après offset)", log_file)

    # Découpe en chunks de CHUNK_SIZE
    for chunk_start in range(0, min(MAX_FILES, total_eligible - OFFSET), CHUNK_SIZE):
        chunk_end = chunk_start + CHUNK_SIZE
        chunk = all_eligible_files[OFFSET + chunk_start: OFFSET + chunk_end]
        if chunk:
            log(f"📦 Chunk préparé : n°{OFFSET + chunk_start + 1} à n°{OFFSET + chunk_end}abs. ({len(chunk)} fichiers)", log_file)
            yield chunk

    total_duration = time.time() - start_time
    log(f"⏳ Scan terminé en {total_duration:.2f}s | {len(all_eligible_files)} fichiers trouvés", log_file)


def process_file(file_path, file_count, chunk_start):
    """Traite un fichier PDF avec logs détaillés."""
    start_time = time.time()
    absolute_file_number = chunk_start + file_count
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
                pass  # Ignore les erreurs de fermeture

def main():
    CSV_FILE = os.path.join(OUTPUT_DIR, f"{DATE_NAME}_results_{OFFSET}_to_{OFFSET + MAX_FILES}.csv")
    csvfile = open(CSV_FILE, 'w', newline='', encoding='utf-8')
    total_matches = 0
    script_start_time = datetime.now()
    log(f"🚀 Début du batch (ID: {RUN_ID})", log_file)

    if not os.path.exists(ROOT_DIR):
        log(f"[ERREUR] {ROOT_DIR} inaccessible.", log_file)
        return

    if not os.listdir(ROOT_DIR):
        log(f"[AVERTISSEMENT] {ROOT_DIR} est vide.", log_file)
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    try:
        with csv_writer_lock:
            writer = csv.writer(csvfile)
            writer.writerow(["file", "match"])
            csvfile.flush()

        chunk_index = 0
        for chunk in find_pdf_files():
            chunk_index += 1
            chunk_start_abs = OFFSET + (chunk_index - 1) * CHUNK_SIZE
            log(f"🔧 Début du chunk {chunk_index} (n°{chunk_start_abs + 1} à n°{chunk_start_abs + len(chunk)}abs.)", log_file)
            chunk_start_time = datetime.now()

            with ThreadPoolExecutor(max_workers=int(os.getenv("MAX_WORKERS", "4"))) as executor:
                futures = []
                for file_count, file in enumerate(chunk, start=1):
                    futures.append(executor.submit(process_file, file, file_count, chunk_start_abs))

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
            log(f"🎉 Chunk {chunk_index} terminé en {chunk_duration:.2f}s | {total_matches} correspondances totales", log_file)

    finally:
        csvfile.close()
        log_file.close()

if __name__ == "__main__":
    main()
