.PHONY: install ui test demo clean

install:
	pip install -r requirements.txt

ui:
	streamlit run ui/app.py

demo:
	python -m core.pipeline --input data/sample_docs/ --output results/

test:
	python -m pytest tests/ -v

clean:
	rm -rf results/ __pycache__ core/__pycache__ ui/__pycache__
	find . -name "*.pyc" -delete

# Index sample products into ChromaDB for enrichment demo
seed-catalog:
	python -c "
from core.ingest import ingest_file
from core.extractor import extract, build_client
from core.enricher import index_product
from pathlib import Path
import anthropic

client = build_client()
for f in Path('data/sample_docs').glob('*.txt'):
    print(f'Indexing {f.name}...')
    doc = ingest_file(f)
    pt = 'bearing' if 'bearing' in f.name else 'valve' if 'valve' in f.name else 'sensor'
    product = extract(doc, pt, client=client)
    index_product(product)
    print(f'  Done.')
print('Catalog seeded.')
"
