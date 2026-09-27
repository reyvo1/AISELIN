# AIOC Connector SDK

Semua SDK mengikuti kontrak yang sama: `/health`, `/capabilities`, `/actions/execute`, `/actions/verify`, `/actions/rollback`, plus helper HMAC untuk mengirim event ke AIOC.

Folder:
- `python/aioc_connector.py`
- `php/AiocConnector.php`
- `node/aioc-connector.mjs`

SDK sengaja kecil dan framework-neutral. Aplikasi dapat membungkus runtime ini dengan FastAPI/Flask, PHP route/controller, Express/Fastify, atau framework yang sudah dipakai aplikasi produksi.
