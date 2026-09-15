# 03 — Baseline de Benchmark (medições reais)

> Ferramenta: `scripts/benchmark_tts.py` · Engine nativo v4.0.0 (rebuild onnxruntime 1.29)
> Hardware: máquina de desenvolvimento do usuário (x86-64, xanmod). Modelo Piper: `pt_BR-faber-medium`.
> RTF = tempo_de_síntese / duração_do_áudio (menor é melhor; <1.0 = mais rápido que tempo real).

## Síntese (TTFA de bytes, sem reprodução)

| Backend | chars | cold | synth (s) | áudio (s) | RTF |
|---------|------:|:----:|----------:|----------:|----:|
| espeak-native | 10 | sim | 0.0020 | 0.50 | 0.0041 |
| espeak-native | 50 | não | 0.0022 | 2.68 | 0.0008 |
| espeak-native | 200 | não | 0.0067 | 7.83 | 0.0009 |
| espeak-native | 1000 | não | 0.0486 | 65.2 | 0.0007 |
| piper-native | 10 | **sim** | **1.0042** | 0.57 | 1.77 |
| piper-native | 50 | não | 0.1573 | 2.74 | 0.057 |
| piper-native | 200 | não | 0.4367 | 7.71 | 0.057 |
| piper-native | 1000 | não | 3.9653 | 50.3 | 0.079 |

### Leitura dos números

1. **espeak** é praticamente instantâneo (RTF ~0.001) — ótimo para fallback/acessibilidade.
2. **Piper cold start = 1.0 s** para a *primeira* frase — quase todo esse tempo é **carga da sessão ONNX**
   (o modelo é lido do disco e a sessão inicializada). A síntese em si de 10 chars é ~0.06 s.
3. **Piper warm** tem RTF ~0.057–0.079 — muito rápido; a síntese não é o gargalo do TTFA.

### Implicação direta para o TTFA (Alt+V)

O maior componente do "tempo até a fala" no primeiro Alt+V com Piper é a **carga do modelo (~1 s)**, não a
síntese. Portanto:

- **Prewarm do modelo** (carregar a sessão ONNX em idle, depois da UI pronta, **sem produzir áudio**) reduz
  o TTFA da primeira fala de ~1.0 s para ~0.06 s. Meta: implementar `synthesize_piper` "a seco" com texto
  mínimo *ou* uma API de load-only. **Prewarm nunca pode produzir áudio** (garantido: síntese não abre o
  dispositivo — ver doc 00 §4).
- **Streaming por sentença** (sintetizar/soltar a 1ª sentença enquanto as próximas são geradas) reduz o TTFA
  percebido em textos longos, onde hoje espera-se o WAV inteiro.

## Cancelamento / stop

- `crate::audio::stop_playback()` sinaliza um `AtomicBool` verificado a cada 50 ms no laço do rodio →
  latência de stop de até ~50 ms no motor nativo. **[a medir no hardware]** com a nova UI.
- No lado Python, `stop()` foi ajustado para reap curto (`wait(timeout=0.5)`), evitando estol longo no
  main thread.

## Memória / processos (a completar com hardware)

- **[pendente hardware]** Perfil de RSS por backend, teste "trocar voz 100×" (estabilização de memória),
  verificação de processos órfãos após encerrar — requer o desktop ao vivo e serão preenchidos em
  `docs/90-BENCHMARK-FINAL.md`.

## Como reproduzir

```bash
ORT_LIB_LOCATION=/usr/lib python3 scripts/benchmark_tts.py            # tabela
ORT_LIB_LOCATION=/usr/lib python3 scripts/benchmark_tts.py --json     # JSON estruturado
```
