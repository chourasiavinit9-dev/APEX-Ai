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

# Create data directories with correct permissions
RUN mkdir -p data/product_catalog_db data/sample_docs results data/unihack data/chroma_db \
    && chmod -R 777 data results

EXPOSE 8080

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

CMD ["python3", "run_ui.py", "--port=8080", "--no-browser"]
