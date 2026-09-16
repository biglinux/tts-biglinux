# 04 — Matriz de Capacidades dos Backends

Objetivo: a UI deve mostrar apenas controles que o backend **realmente** suporta. Esta matriz é a fonte
para um futuro `BackendCapabilities` (ver doc 05).

## Matriz

| Recurso | speech-dispatcher | RHVoice | espeak-ng | Piper | Kokoro |
|---|:---:|:---:|:---:|:---:|:---:|
| Velocidade (rate) | ✅ | ✅ | ✅ | ✅ (length_scale) | ✅ (speed) |
| Pitch **real** | ✅ | ✅ | ✅ | ❌ (ver nota) | ❌ |
| Volume | ✅ | ✅ | ✅ | ✅ (ganho) | ✅ (ganho) |
| Volume 0 = mudo | ✅¹ | ✅¹ | ✅ | ✅ | ✅ |
| Streaming | parcial | ❌ | n/a | ⚠️ (por sentença, a implementar) | ✅ (por chunk, já usado) |
| Cancelamento | ✅ | ✅ | ✅ | ✅ | ✅ |
| Preview | via CLI | ✅ | ✅² | ❌ (a adicionar) | ✅ |
| Multilíngue | ✅ | limitado | ✅ (amplo) | 1 idioma/modelo | ✅ |
| GPU | n/a | ❌ | ❌ | ⚠️ (EP ONNX) | ⚠️ |
| CPU-only | ✅ | ✅ | ✅ | ✅ | ✅ |
| Voice cloning | ❌ | ❌ | ❌ | ❌ | ⚠️ (blend) |
| Cache de modelo | n/a | n/a | n/a | ✅ (sessão ONNX) | ✅ (pipeline) |
| Entrada de fonemas | ❌ | ❌ | ✅ (IPA) | ✅ (via espeak) | ❌ |
| pt-BR excelente | depende módulo | ✅ | razoável | ✅ | ✅ |
| Offline | ✅ | ✅ | ✅ | ✅ | ✅ |

¹ Nesta fase, volume 0 é tratado como **mudo real** (curto-circuito, sem áudio) — ver `tts_service.py`.
² Preview espeak hoje sem `-v <lang>` (fala com voz padrão); corrigir em doc 08.

## Notas de semântica (padronização)

- **Rate**: convenção única — esquerda (`-100`) = mais lento, direita (`+100`) = mais rápido, em TODOS os
  backends. A adaptação para o parâmetro nativo fica na função do backend (`espeak_wpm`, `piper_length_scale`,
  `speed`). Já implementado como funções puras testadas.
- **Pitch do Piper**: **não é pitch**. Hoje mapeia para `noise_scale` (expressividade/variação da voz).
  Ação recomendada (doc 09): renomear o controle para **"Expressividade"** quando o backend for Piper, ou
  desativar "Pitch" para Piper explicando o motivo. Nunca rotular como pitch.
- **Volume**: `0 == mudo` garantido por backend. Ganhos: espeak `0..200`, Piper/Kokoro fator `0.0..2.0`.

## `BackendCapabilities` proposto (doc 05)

```python
@dataclass(frozen=True)
class BackendCapabilities:
    rate: bool = True
    pitch: bool = False          # pitch REAL
    expressiveness: bool = False # ex.: noise_scale do Piper
    volume: bool = True
    streaming: bool = False
    cancel: bool = True
    preview: bool = True
    multilingual: bool = False
    phoneme_input: bool = False
    voice_cloning: bool = False
```

A UI liga/desliga controles a partir desse objeto → nunca oferece um controle que engana o usuário.
