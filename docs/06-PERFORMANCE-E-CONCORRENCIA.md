# 06 — Performance e Concorrência

## Regras de ouro (metas)
- Nenhuma tarefa síncrona > 50 ms no GTK main thread quando evitável.
- Stop percebido como imediato.
- Scroll do Histórico fluido mesmo com milhares de registros.

## Bloqueios do main thread encontrados (e status)
- ✅ `time.sleep(0.15)` em `speak()` — **removido** (rodava via `GLib.idle_add` no main thread).
- ✅ `process.wait(timeout=2)` no stop — **reduzido para 0.5 s** (aplay/spd morrem no SIGTERM).
- ⚠️ joins de bg-thread (`timeout=2`) em `stop()` — bounded, mas o correto é o **TTSController** (doc 05)
  com request-id, que move toda espera para o worker.
- ⚠️ `history load/save` e construção de widgets no main thread (doc 07).

## Concorrência
- ✅ `ESPEAK_LOCK` (Rust) serializa a FFI espeak (não thread-safe) — evita corromper estado do tradutor
  quando o phonemizer do Piper roda em bg-thread enquanto o backend espeak fala.
- ⚠️ `STOP_FLAG` global do rodio: por-processo, sem geração/id. Com o controller, associar a request-id
  para evitar que um stop de A pare B.

## Próximos passos mensuráveis
- Prewarm → TTFA 1ª fala Piper 1.0 s → ~0.06 s.
- Streaming por sentença → TTFA de textos longos.
- Perfil de RAM "trocar voz 100×" e verificação de órfãos [pendente hardware].
