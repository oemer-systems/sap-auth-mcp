# Rezept für das Docker-Image des SAP-Berechtigungsassistenten.
# Das Sprachmodell läuft nicht im Image, sondern weiter über Ollama auf dem eigenen Rechner.

# Grundlage: schlankes offizielles Python-Image
FROM python:3.13-slim

# Python legt keine Zwischendateien an, gibt Ausgaben sofort aus und arbeitet immer mit UTF-8.
# pip speichert keinen Zwischenspeicher im Image und fragt nicht nach neuen Versionen.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Zeitzone: Ohne diese Angabe rechnet ein Container in UTC, in Berlin also eine Stunde falsch.
# tzdata enthält die Zeitzonen, TZ wählt eine aus. Beim Start mit TZ=... änderbar.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
ENV TZ=Europe/Berlin

# Arbeitsordner im Image
WORKDIR /app

# Zuerst nur die Paketliste. Docker merkt sich diese Schicht und baut sie nur neu,
# wenn sich requirements.txt ändert. Codeänderungen gehen dadurch in Sekunden.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Dann der Code. Was in .dockerignore steht, wird nicht kopiert.
COPY . .

# Eigener Benutzer ohne Administratorrechte. Schreiben darf er nur in die Laufzeitordner
# und in data, damit der Import auch im Container funktioniert.
RUN useradd --create-home --uid 1000 assistent \
    && mkdir -p logs berichte zustand \
    && chown -R assistent:assistent logs berichte zustand data
USER assistent

# Ollama läuft auf dem Rechner, nicht im Container. Diese Adresse führt aus dem Container dorthin.
ENV OLLAMA_HOST=http://host.docker.internal:11434

# Die Chat-Oberfläche
EXPOSE 8501

# Docker prüft alle 30 Sekunden, ob die Oberfläche antwortet
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

# Startbefehl: Chat im Browser. 0.0.0.0 heißt hier nur "innerhalb des Containers erreichbar",
# nach außen wird der Port beim Start auf 127.0.0.1 begrenzt.
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
