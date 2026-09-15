# 99 — Relatório Final (Fase A + início da Fase B)

> Branch: `tts-hardening-phase-b` · Base: `c374397` · Sem commit destrutivo; alterações no working tree.

## Resumo
Auditoria completa por **instrumentação empírica** (não só leitura) e correção dos bugs críticos de maior
risco, com testes automatizados (Python + Rust) e um benchmark reproduzível. Priorizou-se "uma implementação
impecável" dos itens críticos sobre muitos itens pela metade.

## Bugs encontrados → causa → solução

1. **"Fala em inglês antes do texto" (carro-chefe)**
   - Causa: espeak-ng inicializado em `AUDIO_OUTPUT_PLAYBACK` e **compartilhado** como fonemizador do Piper —
     estruturalmente permitia que o espeak fosse dono do alto-falante. (As 3 fontes de áudio plausíveis —
     init do espeak, subprocess piper-tts, síntese nativa — foram **medidas e descartadas**: nenhuma abre o
     dispositivo de áudio.)
   - Solução: espeak reescrito para `AUDIO_OUTPUT_SYNCHRONOUS` + synth callback (nunca abre áudio, nunca toca
     sozinho); Piper usa `espeak::phonemize()` puro; áudio do backend espeak roteado pelo motor rodio central;
     `ESPEAK_LOCK` serializa a FFI. Bug fechado **por construção**. Repro final [pendente hardware].

2. **Engine nativo quebrado** — `.so` linkado a onnxruntime 1.24.3 vs sistema 1.29.0 → `ImportError` → todo
   TTS neural caía em subprocess. Solução: rebuild contra 1.29; causa raiz de empacotamento documentada (RC do
   `ort` + build sem `--locked`).

3. **volume=0 não era mudo** — pisos `0.2`/`10` nunca passavam 0 ao motor (que já silencia em 0.0). Solução:
   funções puras `volume_factor`/`espeak_volume` com **0 = mudo real** + curto-circuito em todos os backends.

4. **`pkill -f`** matava processos de outros apps. Solução: removido; `stop()` já finaliza apenas os processos
   rastreados por nós.

5. **Bloqueio do GTK main thread** — `time.sleep(0.15)` em `speak()` (via idle_add). Solução: removido;
   `process.wait` reduzido a 0.5 s. Controlador assíncrono completo especificado (doc 05).

6. **Histórico com perda de dados** — IDs por timestamp de segundos colidiam e sobrescreviam arquivos.
   Solução: `id` uuid + timestamp em microssegundos; parser de UI tolerante a formato legado.

## Arquitetura: antes → depois (parcial)
- Antes: espeak dono do áudio (playback) + duplicado como phonemizer; áudio fragmentado (aplay/sox/espeak/rodio);
  mapeamentos de parâmetro espalhados e divergentes; FFI espeak sem lock.
- Depois: espeak = phonemizer audio-free + backend que rende PCM ao motor central; funções de mapeamento puras
  e testadas; FFI espeak serializada. Base pronta para o `TTSController`/`AudioOutput` (doc 05).

## Performance (baseline medido — doc 03)
- espeak nativo: RTF ~0.001 (instantâneo).
- Piper nativo: **cold 1.0 s** (dominado por carga do modelo), **warm ~0.06 s** (RTF ~0.057). ⇒ Prewarm sem
  áudio derruba o TTFA da 1ª fala para ~0.06 s (próxima fase).

## Dependências
- Nenhuma adicionada/removida no runtime. `Cargo.lock`: apenas versão do crate `0.1.0→4.0.0` (sincroniza com o
  manifest; sem bump de dependências). `.so` continua não-versionado (built no pacote).

## Licenças
- Auditoria de licenças de backends novos: framework em doc 02, **a completar com dados online** antes de
  qualquer adoção. Não integrar pesos non-commercial por padrão.

## Testes (comandos e resultado)
```
python3 -m pytest tests/ -q                     # 8 passed
cd tts-engine && ORT_LIB_LOCATION=/usr/lib ORT_PREFER_DYNAMIC_LINK=1 cargo test --release   # 3 passed
python3 -m compileall usr/share/biglinux/tts-biglinux/                                       # limpo
ORT_LIB_LOCATION=/usr/lib python3 scripts/benchmark_tts.py                                   # baseline
```

## Arquivos
- Código: `tts-engine/src/backends/espeak.rs` (reescrito), `piper.rs`, `lib.rs` (novo `synthesize_espeak`),
  `services/tts_service.py`, `services/history_service.py`, `ui/history_view.py`.
- Novos: `scripts/benchmark_tts.py`, `tests/` (3 arquivos + conftest), `docs/` (14 arquivos).

## Fase B (continuação) — entregue e testado

7. **Processamento de texto pt-BR** — normalização de números/moeda/percentual (novo, `text_processor.py`):
   `R$ 1.250,90` → "mil duzentos e cinquenta reais e noventa centavos"; `50%` → "cinquenta por cento";
   `2026` → "dois mil e vinte e seis". Motor `num_to_words_pt` (0..10^15) com regras de "e" corretas.
   Toggle `normalize_numbers` (config + UI). 7 testes.
8. **Prewarm (TTFA)** — `load_piper()` no engine (load-only, **sem áudio**) + prewarm em idle no startup e
   ao trocar backend/voz. Medido: 1ª síntese Piper **1.0 s → 0.098 s**. Teste de regressão: prewarm não abre
   dispositivo de áudio.
9. **Guarda de corrida do Alt+V** — contador de geração monotônico: síntese em background descartada se um
   `speak()`/`stop()` mais novo a supersede (evita "áudio antigo começa depois do novo"). Corrigido no review
   para preservar o modo **simultâneo**. 2 testes.
10. **Downloads Kokoro robustos** — `voices.bin` agora **atômico** (temp + `os.replace` + fsync), evitando
    perder todas as vozes num crash; retry (3×) com backoff; sanitização de `voice_id` (anti path-traversal).
    3 testes (inclui sobrevivência a crash mid-write).
11. **Config resiliente + versionada** — `config_version`; coerção por-campo tolerante (uma chave inválida
    não descarta todas as preferências); seções não-dict não quebram. 3 testes.
12. **Chunking de textos grandes** — `chunk_text()` por sentença/parágrafo/palavra (corte duro só em último
    caso), base para streaming. 6 testes.
13. **Empacotamento** — PKGBUILD com `cargo build --locked`, `rust>=1.85`; `ort` pinado a `=2.0.0-rc.10`;
    removido `pip install --break-system-packages` (Kokoro agora via `python-kokoro`/`python-soundfile` pacman).
14. **Pitch do Piper → "Expressividade"** na UI (não é pitch; é noise_scale) — relabel dinâmico por backend.

15. **Histórico — congelamento com muitas entradas (PRIORIDADE ALTÍSSIMA)** — atacada a causa dominante:
    `AudioPlayerWidget` cria o pipeline GStreamer **só ao tocar** (não mais ~2000 pipelines eager); apenas
    **uma view** (lista OU grade) é materializada; construção **em lotes** por `idle`; carga do JSON **fora
    do main thread**; busca com **debounce** (200 ms). Teste headless do pipeline preguiçoso.
16. **Streaming por sentença (Piper)** — textos > 600 chars são divididos e sintetizados/tocados em chunks
    com **prefetch** (sintetiza o próximo enquanto o atual toca): TTFA passa de "texto inteiro" (~segundos)
    para ~o primeiro chunk (~0.25 s). Honra a guarda de corrida; limpa temporários; histórico texto-only.
    Testes headless (ordenação/limpeza/abort): `tests/test_piper_streaming.py`.

Testes totais: **33 Python + 3 Rust — todos passam.** `ruff` limpo nos arquivos que editei.

## Problemas restantes (declarados abertamente)
- Histórico: causa dominante do freeze resolvida (pipelines preguiçosos + view única + lotes); falta a
  **virtualização real** (`Gtk.ListView`) para não realizar todos os widgets — melhora incremental (doc 07).
- Controlador assíncrono completo (state machine formal) — a guarda de corrida por geração já cobre o pior
  caso do Alt+V rápido; a máquina de estados completa continua especificada (doc 05/06).
- Download: progresso ainda é spinner (não barra com bytes/velocidade); atomicidade/retry/sanitização já
  feitos (doc 08).
- Preview de voz ainda pode cair em inglês para idiomas não reconhecidos (doc 08).
- **SQLite** para histórico (doc 12) — ainda `history.json` (agora com ids únicos e sem perda de dados).
- Reprodução final do bug carro-chefe, stress Alt+V, Wayland/X11, perfil de RAM/órfãos, teste de scroll do
  Histórico com 5000 entradas — **[pendente hardware]** (exigem o desktop ao vivo).

## Como validar no seu desktop
1. `cd tts-engine && ORT_LIB_LOCATION=/usr/lib ORT_PREFER_DYNAMIC_LINK=1 cargo build --release`
2. Rodar o app (dev): `usr/bin/biglinux-tts`
3. Selecionar texto pt-BR, backend Piper, Alt+V → só o texto; volume 0 → silêncio; parar → imediato.
