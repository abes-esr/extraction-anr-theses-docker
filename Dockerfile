# Utilise une image Python légère
FROM python:3.9-slim

# Définis le répertoire de travail
WORKDIR /app

# Copie les dépendances
COPY requirements.txt .

# Installe les dépendances
RUN pip install --no-cache-dir -r requirements.txt

# Copie le code source
COPY src/ .

# Commande par défaut (optionnel)
CMD ["python", "main.py"]
