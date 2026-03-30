# Loging Inbox

Project untuk mencatat semua pesan masuk dari Whatsapp Engine, termasuk text dan gambar.

## Requirements already decided

- Source pesan: Whatsapp Engine saja
- Storage utama: SQLite
- Gambar: disimpan sebagai file lokal
- Gambar diproses oleh agent untuk mendapatkan konteks/deskripsi
- Konteks gambar disimpan ke SQLite
- Viewer log: wajib ada

## Planned components

- webhook receiver untuk menerima event dari Whatsapp Engine
- SQLite storage untuk metadata pesan dan konteks gambar
- media storage lokal untuk file gambar asli
- agent integration untuk membaca konteks gambar
- viewer/API untuk melihat log masuk

## Project structure

- `src/`
- `docs/`
- `data/`
- `scripts/`
- `systemd/`

## Current status

- webhook receiver exists
- SQLite schema exists
- log viewer exists
- real event normalization from Whatsapp Engine is supported
- image context is still placeholder until final phase

## Next

- verify real inbound WhatsApp events arrive through Whatsapp Engine fanout
- improve viewer filters and search
- add payload validation
- keep real agent image analysis for the final phase
