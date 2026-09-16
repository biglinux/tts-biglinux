# 12 — Migrações

## Config
- `config.py` já tem migração legado (`~/.config/tts-biglinux` → novo) e defaults tolerantes.
- Recomendado: adicionar `config_version` (schema versionado) e validação por-campo, para não perder todas
  as preferências por uma chave inválida. "Restaurar padrões" (global e por-backend).

## Histórico → SQLite ✅ (feito)
- `services/history_db.py`: SQLite (WAL) com `entries(id PK, ts, backend, voice_id, text, text_preview,
  has_audio, created_at)`, índices por `created_at` e `ts`. Inserts O(1), busca indexada, paginação.
- Migração automática de `history.json` → SQLite (renomeia para `.json.migrated`, roda uma vez). Sem perda
  de dados; arquivos `.txt/.wav` continuam no disco referenciados por `ts_backend`.
- Retenção configurável: `max_entries` e `max_age_days` (0 = ilimitado), aplicada no save; nunca exclui fora
  da política. Config em `HistoryConfig`.
- Testes: `tests/test_history_db.py` (7) + `tests/test_history.py` (migração/retenção).
