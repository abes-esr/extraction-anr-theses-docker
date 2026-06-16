FROM python:3.9-slim
WORKDIR /app
# Copie des dépendances
COPY requirements.txt .
# Instalation les dépendances
RUN pip install --no-cache-dir -r requirements.txt
# Copie du code source
COPY src/ .
# Récupérer l'heure du serveur
ENV TZ=Europe/Paris
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
