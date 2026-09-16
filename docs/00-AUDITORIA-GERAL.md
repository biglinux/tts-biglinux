# 00 — Auditoria Geral do BigLinux TTS

> Data: 2026-09-15 · Branch: `tts-hardening-phase-b` · Commit base: `c374397`
> Método: leitura integral do código local + **instrumentação empírica** (ctypes/FFI,
> probes de file-descriptors, benchmarks reais). Nada aqui é suposição não verificada;
> onde algo não pôde ser confirmado no ambiente headless, está marcado como **[pendente hardware]**.

## 1. Arquitetura atual (resumo)

```
Alt+V (KDE/GNOME/XFCE/Cinnamon shortcut)
  → /usr/bin/biglinux-tts-speak → main.py --speak
  → GApplication (instância única) do_command_line
  → _on_tray_speak: captura seleção (thread) → GLib.idle_add → TTSService.speak()
  → backend: speech-dispatcher | rhvoice | espeak-ng | piper | kokoro
  → áudio: aplay / play(sox) / espeak interno / rodio(nativo) / koko
```

- **Python/GTK4/libadwaita**: app em `usr/share/biglinux/tts-biglinux/` (~9k linhas).
- **Rust/PyO3/ONNX**: engine nativo em `tts-engine/` (~0.7k linhas), exposto como `tts_engine.so`.
- Camadas: `application.py` (ciclo de vida), `services/` (tts, voice_manager, history, clipboard, tray, desktop_integration, text_processor), `ui/` (main_view, history_view, voice_manager_dialog, audio_player), `utils/`.

## 2. Achados críticos (confirmados por instrumentação)

| # | Severidade | Achado | Evidência | Status |
|---|-----------|--------|-----------|--------|
| C1 | Alta | **espeak inicializado em `AUDIO_OUTPUT_PLAYBACK`** e compartilhado como fonemizador do Piper — permitia (por construção) que o espeak fosse dono do alto-falante. | `espeak.rs` init; probe de fd | **Corrigido** (ver §4) |
| C2 | Alta | **Engine nativo quebrado**: `tts_engine.so` linkado contra onnxruntime 1.24.3; sistema tem 1.29.0 → `ImportError` → *todo* TTS neural caía em subprocess silenciosamente. | `import tts_engine` → `VERS_1.24.3 not found` | **Corrigido** (rebuild) + causa raiz de empacotamento documentada |
| C3 | Alta | **`volume=0` não é mudo**: mapeamentos Python usavam pisos (`0.2`, `10`) que nunca passavam 0 ao motor — que já silencia corretamente em `0.0`. | teste de amplitude do WAV | **Corrigido** |
| C4 | Alta | **`pkill -f espeak-ng/piper-tts/RHVoice-test`** em `_kill_backends()` mata processos de *outros* apps do usuário. | `tts_service.py:1001` | **Corrigido** (removido) |
| C5 | Alta | **`time.sleep(0.15)` no GTK main thread** dentro de `speak()` (chamado via `idle_add`), além de `process.wait(timeout=2)` e joins de thread — congelam a UI. | `tts_service.py:148` | **Parcial**: sleep removido, wait reduzido; controlador assíncrono completo proposto (05) |
| C6 | Alta | **Histórico**: IDs por timestamp de segundos → colisão → arquivos `.txt/.wav` **sobrescritos** (perda de dados). | audit + teste de regressão | **Corrigido** (id único + timestamp µs) |
| C7 | Alta | **Histórico**: renderiza 2 árvores completas (lista+grade) e ~2000 pipelines GStreamer *eagerly*, sem virtualização → congela com 1000+ entradas. | audit `history_view.py` | **Pendente** (rework proposto em 07) |

## 3. Achados médios / dívidas

- **M1 — `history.json` reescrito inteiro a cada save** (O(N)); migrar para SQLite (doc 07/12).
- **M2 — Preview de voz cai em texto inglês** quando `language` é vazio/`multi`/`unknown`; espeak preview sem `-v`. (doc 08)
- **M3 — Downloads Kokoro sem atomicidade**: reescrita in-place de `voices.bin` pode corromper todas as vozes; sem `.part`, retry, checksum, verificação de espaço. (doc 08)
- **M4 — Sem barra de progresso real** em downloads (apenas spinner); tamanho “~512 KB” falso.
- **M5 — Pitch do Piper** mapeia para `noise_scale` (não é pitch real) — enganoso; renomear para “expressividade” na UI. (doc 04)
- **M6 — ort fixado em `2.0.0-rc.9`** (lock resolveu rc.10) e PKGBUILD sem `--locked`; RC + build sem trava = risco de reprodutibilidade. Foi a causa raiz de C2. (doc 11)
- **M7 — Dupla provisão Kokoro**: `optdepends` pacman *e* `pip install --break-system-packages` em `main_view.py` — conflito de gerenciadores.
- **M8 — espeak-ng não é thread-safe** e era chamado sem lock global (piper phonemize em bg thread + espeak speak). **Corrigido**: `ESPEAK_LOCK` serializa toda FFI.
- **M9 — Rate/pitch/volume** interpretados de forma diferente por backend sem normalização central. **Parcial**: funções puras normalizadas extraídas (`tts_service.py`).

## 4. A correção do bug carro-chefe (C1) — raiz e prova

**Hipóteses testadas e refutadas por medição:**
- `espeak_Initialize(AUDIO_OUTPUT_PLAYBACK)` + `espeak_TextToPhonemes`: **0 fds de áudio abertos**, nenhum som (probe `/proc/self/fd`). Fonemas de “Bom dia” corretos: `bˈoŋ dʒˈiæ`.
- `piper-tts` subprocess: 0.68 s de áudio pt-BR limpo, sem inglês (stderr + WAV inspecionados).
- `synthesize_piper` nativo: **0 fds de áudio** durante síntese.

**Correção estrutural aplicada** (`tts-engine/src/backends/espeak.rs`): espeak passa a inicializar em
`AUDIO_OUTPUT_SYNCHRONOUS` com **synth callback** — nunca abre o dispositivo de áudio nem toca
sozinho. Piper usa `espeak::phonemize()` (só `espeak_TextToPhonemes`), **incapaz de emitir áudio por construção**.
O áudio do *backend* espeak agora é renderizado para PCM e reproduzido pelo motor rodio centralizado
(`crate::audio`), com cancelamento e volume unificados. Toda FFI espeak é serializada por `ESPEAK_LOCK`
(espeak-ng não é thread-safe). Testes de regressão em Rust e Python cobrem: fonemização não-vazia,
síntese audível a volume 100, e **PCM silencioso a volume 0**.

> **[pendente hardware]** A reprodução final do sintoma “inglês antes do texto” exige o desktop ao vivo do
> usuário (PipeWire ativo + engine nativo carregando). As três fontes de áudio plausíveis foram eliminadas
> por medição e o espeak foi tornado incapaz de tocar sozinho — a classe do bug está fechada por construção.

## 5. Busca por padrões problemáticos

- `pkill -f` genérico → **removido** (C4).
- `except Exception: pass` (silencioso): presente em várias camadas (spd close, dbus, downloads). Mantidos os defensivos legítimos; recomendado logging em nível DEBUG.
- I/O síncrono no main thread: `speak()`/`stop()` (C5), `history load/save` (C7/M1), preview subprocess. Endereçado parcialmente; plano completo em 05/06.
- Arquivos temporários `/tmp/*.wav`: limpos em sucesso/stop; caminho de mute agora também limpa. Streaming direto (sem temp) proposto em 05.
- `subprocess.run(timeout=...)` no main thread (spd-say -C, which sox): reduzir/mover; `which sox` já trocado por `shutil.which`.

## 6. Dependências

- **Rust**: pyo3 0.25.1, ort 2.0.0-rc.10, rodio 0.20.1, hound 3.5.1, serde 1, thiserror 2. Ver riscos em doc 11.
- **Sistema**: gtk4, libadwaita, speech-dispatcher, espeak-ng, onnxruntime(-cpu), aplay, sox(opt), piper-tts(opt), koko(opt), RHVoice(opt), kokoro/pytorch(opt).

## 7. Testes adicionados nesta fase

- `tests/test_param_mappings.py` — contrato de rate/pitch/volume (mudo=0).
- `tests/test_history.py` — regressão de colisão/perda de dados.
- `tests/test_engine_native.py` — Piper/espeak não abrem áudio na síntese; mudo real.
- Rust `#[cfg(test)]` em `espeak.rs` — fonemização + mudo + audível.
- `scripts/benchmark_tts.py` — baseline estruturado (JSON/tabela).

Resultado: **8 testes Python + 3 testes Rust — todos passam.** App compila limpo (`compileall`).
