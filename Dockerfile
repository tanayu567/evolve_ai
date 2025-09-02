FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy scraper
COPY sve_scrape.py ./

# Drop privileges
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# Default entrypoint; pass CLI args to override options
ENTRYPOINT ["python", "-u", "/app/sve_scrape.py"]
# No default args; caller can pass e.g. --limit 10 or --out /data/cards.tsv
CMD []

