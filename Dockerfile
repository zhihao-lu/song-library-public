FROM python:3.12-slim

# Fonts for redrawing chords (Liberation Sans is metric-compatible with Arial),
# WenQuanYi Micro Hei for Chinese lyrics in uniform layout,
# OCR support for scanned song sheets, and LibreOffice to turn Word files into PDFs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-liberation fonts-dejavu-core fonts-wqy-microhei ocrmypdf \
      libreoffice-writer-nogui \
 && rm -rf /var/lib/apt/lists/*

# The team's own time, so a set becomes a past set at midnight in Singapore, not 8 am (UTC).
ENV TZ=Asia/Singapore

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PORT=8080
EXPOSE 8080
# One worker process: uploaded PDFs are tracked in memory for "original layout" mode.
CMD gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT app:app
