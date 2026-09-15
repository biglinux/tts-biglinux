# 01 — Pendências dos Documentos Antigos (rastreabilidade)

## Situação encontrada

A pasta `docs/` **não existia** no diretório local no início desta auditoria (verificado: `ls docs/` →
inexistente; `git ls-files` sem `.md` de planejamento além do `README.md`). Portanto **não há documentos
`.md` de planejamento anteriores** a confrontar. A única fonte de "intenção planejada" é o `README.md`
(26 KB) e os docstrings/comentários do código.

Esta pasta `docs/` foi criada agora e passa a ser o registro de rastreabilidade daqui para frente.

## Matriz de rastreabilidade (requisito → estado real)

Derivada do `README.md`, dos comentários no código e do briefing da missão.

| Requisito planejado | Origem | Estado atual | Arquivo/função | Ação | Teste | Status final |
|---|---|---|---|---|---|---|
| Alt+V fala só o texto selecionado (sem intro em inglês) | Missão/README | Corrigido por construção | `espeak.rs` (sync mode), `piper.rs` | espeak nunca toca sozinho | Rust+Py regressão | ✅ (repro final [pendente hardware]) |
| Engine nativo Rust/ONNX funcional | README | Estava **quebrado** (onnxruntime) | `tts-engine`, PKGBUILD | rebuild 1.29 + doc de empacotamento | `test_engine_native.py` | ✅ local / ⚠️ pkg |
| Volume 0 = mudo | Missão | Não era mudo | `tts_service.py` map | curto-circuito + fator 0.0 | `test_param_mappings.py` | ✅ |
| Não usar `pkill -f` | Missão | Usava | `tts_service.py` | removido | revisão | ✅ |
| UI nunca congela por TTS | README/Missão | Bloqueios no main thread | `tts_service.py`, controller | sleep removido; controller assíncrono | — | ⚠️ parcial (05) |
| Histórico grande sem travar | Missão | Trava (2 árvores, ~2000 players) | `history_view.py` | virtualização | — | ❌ (plano 07) |
| Histórico sem perda de dados | Missão | Colisão de IDs | `history_service.py` | id único + µs | `test_history.py` | ✅ |
| Normalização rate/pitch/volume entre backends | Missão | Divergente | `tts_service.py` | funções puras | `test_param_mappings.py` | ✅ parcial |
| Pitch do Piper não enganar | Missão | Rotulado "Pitch" (é noise_scale) | UI | renomear "Expressividade" | — | ❌ (plano 04/09) |
| Downloads robustos (.part/retry/checksum) | Missão | Ausentes (Kokoro) | `kokoro_voice_service.py` | rework | — | ❌ (plano 08) |
| Progresso real de download | Missão | Spinner falso | `voice_manager_dialog.py` | barra real | — | ❌ (plano 08) |
| Preview nunca fala inglês por engano | Missão | Cai em inglês | `voice_manager_dialog.py` | usar voz correta | — | ❌ (plano 08) |
| SQLite p/ histórico + migração | Missão | `history.json` O(N) | `history_service.py` | migração | — | ❌ (plano 07/12) |
| Prewarm sem áudio | Missão | Ausente | engine | load-only em idle | — | ❌ (plano 05) |
| Streaming por sentença (Piper) | Missão | Ausente | engine/tts_service | chunk+stream | — | ❌ (plano 05) |
| Diagnóstico/Logging/Config versionada | Missão | Parcial | vários | implementar | — | ❌ (plano 09/12) |
| Empacotamento: ort travado, `--locked` | Missão | RC solto | PKGBUILD/Cargo | pin + locked | — | ⚠️ (plano 11) |

Legenda: ✅ feito e testado · ⚠️ parcial · ❌ planejado (não iniciado nesta fase).

> Princípio seguido: **nada planejado foi ignorado** — o que não foi implementado nesta fase está listado
> com plano e arquivo-alvo, priorizando "uma implementação impecável" dos itens críticos sobre muitos itens
> pela metade.
