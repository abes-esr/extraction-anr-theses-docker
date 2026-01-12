import os
import re
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import pdfplumber
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.local")  # Charge le fichier .env.local

# Récupère les variables d'environnement
MAX_FILES = int(os.getenv("MAX_FILES", "0"))  # 0 = pas de limite
OFFSET = int(os.getenv("OFFSET", "0"))

# Chemin racine dans le conteneur (monté depuis /applis/portail/theses/STARSTOCK)
ROOT_DIR = "/starstock"

def find_pdf_files(root_dir):
    """Trouve tous les fichiers PDF dans la structure :
    /starstock/*/THESE_*/document/0/0/*.pdf
    """
    pdf_files = []
    # On utilise os.walk pour parcourir l'arborescence
    for root, dirs, files in os.walk(root_dir):
        # On vérifie que le chemin relatif correspond au motif
        rel_path = os.path.relpath(root, root_dir)
        parts = rel_path.split(os.sep)
        # On veut : [*, "THESE_*", "document", "0", "0"]
        if (len(parts) >= 4 and
                parts[-3].startswith("THESE_") and
                parts[-2] == "document" and
                parts[-1] == "0" and
                os.path.basename(root) == "0"):
            for file in files:
                if file.lower().endswith('.pdf'):
                    pdf_files.append(os.path.join(root, file))
    return pdf_files

def process_file(file_path):
    matches = []
    try:
        with open(file_path, "rb") as f:
            with pdfplumber.load(f) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        found = PATTERN.findall(text)
                        matches.extend(found)
    except Exception as e:
        print(f"⚠️ Erreur sur {file_path}: {e}")
    return file_path, matches

def main():
    PATTERN = re.compile(r"ANR-[A-Za-z0-9]{4}")
    OUTPUT_DIR = "/output"
    CSV_FILE = os.path.join(OUTPUT_DIR, "results.csv")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf_files = find_pdf_files(ROOT_DIR)

    print(f"🔍 Recherche dans : {ROOT_DIR}")
    print(f"📄 Nombre de fichiers PDF trouvés : {len(pdf_files)}")

    with open(CSV_FILE, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["file", "match"])

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(process_file, file): file for file in pdf_files}
            for future in as_completed(futures):
                file_path, matches = future.result()
                if matches:
                    print(f"=== Match dans : {file_path} ===")
                    for match in matches:
                        print(match)
                        writer.writerow([file_path, match])

    print(f"✅ Terminé. Résultats dans {CSV_FILE}")

if __name__ == "__main__":
    main()
