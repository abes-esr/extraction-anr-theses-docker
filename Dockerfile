FROM python:3.9-slim
WORKDIR /app
# Copie des dépendances
COPY requirements.txt .
# Instalation les dépendances
RUN pip install --no-cache-dir -r requirements.txt
# Copie du code source
COPY src/ .
# Commande par défaut
CMD ["python", "main.py"]
