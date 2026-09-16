# 10 — Plano de Testes

## Implementados nesta fase
- `tests/test_param_mappings.py` — rate/pitch/volume; **volume 0 = mudo**.
- `tests/test_history.py` — regressão de colisão/perda de dados (5 saves no mesmo segundo → 5 arquivos).
- `tests/test_engine_native.py` — Piper/espeak não abrem áudio na síntese; **mudo real** a volume 0.
- Rust `espeak::tests` — fonemização não-vazia; mudo; audível.
Execução: `python3 -m pytest tests/ -q` e `cargo test --release` (com `ORT_LIB_LOCATION=/usr/lib`).

## A implementar (regressão crítica)
- Piper primeira execução: nunca reproduzir conteúdo antes do texto [pendente hardware / GTK loop].
- Alt+V 100× rápido: sem crash/áudio duplicado/órfão/deadlock/freeze.
- Troca de backend: nenhum processo anterior continua tocando.
- Encerrar app: nenhum processo órfão.
- Histórico 1000+: não congelar (após rework doc 07).
- Testes de falha: modelo removido, config corrompida, sem internet, download interrompido, disco cheio,
  speech-dispatcher parado, PipeWire reiniciado.
- Integração por backend (falar/parar/trocar/Unicode/emoji/URL/Markdown) [pendente hardware].
