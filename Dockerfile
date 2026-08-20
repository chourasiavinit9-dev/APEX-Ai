FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for Docling + sentence-transformers
RUN apt-get update && apt-get install -y \
    gcc g++ libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directories
RUN mkdir -p data/product_catalog_db data/sample_docs results

EXPOSE 8501

ENV PYTHONUNBUFFERED=1

CMD ["streamlit", "run", "ui/app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
