# 05 — Arquitetura Proposta (concorrência, controller, áudio)

Documento de design para as próximas fases. Parte já foi implementada nesta fase (marcado ✅).

## 1. TTSController único com estado e request-id

Problema: hoje `speak()`/`stop()` rodam no GTK main thread, com sleeps/joins que podem congelar a UI, e não
há proteção contra callbacks antigos mudarem o estado da requisição atual (race no Alt+V rápido).

Proposta:

```
Estados: IDLE → PREPARING → LOADING_MODEL → SYNTHESIZING → PLAYING → STOPPING → (IDLE|ERROR)
```

- Toda solicitação recebe um **request-id monotônico**. Um worker (thread único ou executor) processa a
  fila. Callbacks/áudio de um id != id-atual são **descartados** (evita "áudio antigo começa depois do novo").
- `stop()` apenas sinaliza cancelamento + incrementa o id — **não bloqueia** o main thread. A limpeza de
  processos/threads roda no worker.
- Atualizações de UI vêm do worker via `GLib.idle_add` (já é o padrão em partes do código).

Benefício: fecha as races do "Alt+V 100×" e remove I/O do main thread por construção.

## 2. AudioOutput centralizado

Hoje há caminhos múltiplos: `aplay`, `play`(sox), espeak interno, rodio(nativo), koko.

- ✅ **Parcial**: o backend espeak agora roteia áudio pelo `crate::audio` (rodio) em vez do playback interno
  do espeak.
- Próximo: uma abstração `AudioOutput` (Rust) com `play/stream/stop/volume/drain`, preferindo **PipeWire**
  quando disponível, com fallback ALSA. Evitar subprocess `aplay`/`play` quando a API nativa trouxer ganho
  real (menos processos, menos temporários, cancelamento fino).
- **Streaming**: `play_stream(rx: Receiver<Vec<i16>>)` para tocar chunks à medida que são sintetizados.

## 3. Prewarm de modelo (sem áudio)

Baseline (doc 03): Piper cold = ~1.0 s, quase todo em carga da sessão ONNX.

- Adicionar `tts_engine.load_piper(model_path)` (load-only, sem inferência) OU um prewarm que sintetiza um
  token e **descarta** o áudio (garantido: síntese não abre o dispositivo — doc 00 §4).
- Disparar em **idle**, depois da UI pronta, só para o backend/voz **selecionado**. Nunca no boot crítico.
- Meta: TTFA da 1ª fala Piper de ~1.0 s → ~0.06 s.

## 4. Cache LRU de modelos

Hoje `MODEL_CACHE` guarda **1** sessão (troca de modelo recarrega). Propor LRU configurável:

- Opções UI: Desativado · 1 voz · 2 vozes · 4 vozes · Automático.
- Limite por nº de modelos e/ou RAM; descarregar sob pressão de memória; nunca vários modelos gigantes
  residentes sem controle.

## 5. Chunking de textos grandes

- Não enviar 100k chars como bloco único. Dividir por sentença/parágrafo/pontuação com tamanho máximo,
  preservando semântica.
- Prefetch: sintetizar o próximo chunk enquanto o atual toca (streaming) → TTFA baixo + reprodução contínua.

## 6. Fila e políticas

- `interromper e falar` (default) · `somente parar` · `enfileirar` — já existe esboço em `application.py`
  (`_speech_queue`), a mover para o controller thread-safe.

## Itens já entregues nesta fase (base para o controller)

- ✅ espeak sem playback próprio (`AUDIO_OUTPUT_SYNCHRONOUS` + callback) — pré-requisito do AudioOutput.
- ✅ `ESPEAK_LOCK` serializando FFI espeak (thread-safety).
- ✅ Funções puras de mapeamento rate/pitch/volume (normalização entre backends).
- ✅ Remoção de bloqueio de main thread (sleep) e de `pkill -f`.
- ✅ `synthesize_espeak` (WAV, sem playback) — base para histórico/streaming/prewarm.
