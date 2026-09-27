# Connector Specification v1

AIOC connector adalah service boundary di aplikasi produksi. AI tidak perlu mengetahui tabel internal aplikasi.

## Required endpoints

### `GET /health`
Return object JSON; healthy endpoint harus dapat dibaca oleh AIOC.

### `GET /capabilities`
Return capability discovery metadata. Manifest yang tersimpan AIOC tetap merupakan authority untuk capability yang boleh dipanggil.

### `POST /actions/execute`

```json
{"capability":"router.reconnect","parameters":{"router_id":"R1"},"dry_run":false}
```

Return object JSON. Untuk financial/destructive command, implementasi aplikasi tetap harus melakukan validation/business invariants sendiri.

### `POST /actions/verify`
Menerima capability, parameters, action result, verification spec dan mengembalikan `{"ok":true}` atau failure detail.

### `POST /actions/rollback`
Melakukan compensating action bila verification gagal. Rollback bukan pengganti transaction safety di aplikasi.

## Events -> AIOC

Signed webhook: `POST /api/v1/events/webhook/{app_id}`.

Headers:
- `X-AIOC-Timestamp`: Unix seconds
- `X-AIOC-Signature`: `sha256=<HMAC_SHA256(secret, timestamp + "." + raw_body)>`

Signature hanya dapat dipakai sekali.

## SDK

Framework-neutral runtime/helper tersedia di `sdk/python`, `sdk/php`, `sdk/node`.


## Standard transport catalog

Core distribution includes `simulator`, `rest`, `graphql`, `mapped_http`, `docker`, `kubernetes`, `mqtt`, `ssh`, and `snmp`. Application operations remain capability-declared. Additional transport plugins register under the `aioc.connectors` Python entry-point group. Production network targets must match `AIOC_ALLOWED_CONNECTOR_HOSTS`; wildcard targets are rejected in production.
