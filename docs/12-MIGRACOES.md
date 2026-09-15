# 12 — Migrações

## Config
- `config.py` já tem migração legado (`~/.config/tts-biglinux` → novo) e defaults tolerantes.
- Recomendado: adicionar `config_version` (schema versionado) e validação por-campo, para não perder todas
  as preferências por uma chave inválida. "Restaurar padrões" (global e por-backend).

## Histórico → SQLite (plano)
- Criar `history.db` (SQLite, WAL) com tabela `entries(id TEXT PK, ts TEXT, backend, voice_id, text,
  text_preview, audio_path, duration, size, params JSON, favorite, tags)`.
- Migração automática idempotente de `history.json` (e arquivos `.txt/.wav`) → linhas, sem perder dados.
- Índices por `ts`, `backend`, `voice_id`; paginação; (opcional) FTS5 para busca.

## Estado desta fase
- ✅ Entradas novas já têm `id` único (uuid) — pré-requisito da migração SQLite.
