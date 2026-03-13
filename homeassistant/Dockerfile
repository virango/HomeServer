FROM python:3.11-slim

# Stellt sicher, dass Python-Output sofort zu Docker-Logs geht
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Kopiere requirements.txt und installiere Abhängigkeiten
COPY reqs.txt .
RUN pip install --no-cache-dir -r reqs.txt

# Kopiere das Python-Skript
COPY mars.py .

# Starte das Skript
CMD ["python", "mars.py"]
