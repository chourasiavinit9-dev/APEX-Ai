FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc g++ libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Suppress pip root warning — expected in Docker containers
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt

COPY . .

# Create data directories
RUN mkdir -p data/product_catalog_db data/sample_docs results

# Run as non-root user for security
RUN useradd -m -u 1000 apex && chown -R apex:apex /app
USER apex

EXPOSE 8080

ENV PYTHONUNBUFFERED=1

CMD ["python3", "run_ui.py", "--port=8080", "--no-browser"]
