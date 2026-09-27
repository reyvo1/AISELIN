.PHONY: dev test worker demo

dev:
	PYTHONPATH=. uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	PYTHONPATH=. python -m app.worker

test:
	PYTHONPATH=. pytest -q

demo:
	PYTHONPATH=. python scripts/bootstrap_demo.py
