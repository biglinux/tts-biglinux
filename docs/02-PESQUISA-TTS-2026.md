# 02 — Pesquisa TTS 2026 (framework + itens a confirmar online)

> **Integridade**: este documento NÃO inventa números de benchmark/licenças "atuais". Os pontos que exigem
> dados vivos (releases, RTF, licenças de pesos hoje) estão marcados **[confirmar online]** com as consultas
> a executar. Preencher com WebSearch/WebFetch antes de decidir adoção de qualquer backend novo.

## Critério de adoção (0–10 por dimensão)
qualidade · TTFA · RTF · CPU · RAM · tamanho · pt-BR · multilíngue · estabilidade · manutenção · **licença
do código** · **licença dos pesos** · distribuição · complexidade de integração.

> Regra: **não** adotar por ser moderno. Backend pesado/redundante → **opcional**, nunca no core.
> **Não confundir** "código open source" com "modelo redistribuível". Analisar pesos e datasets separadamente.

## Candidatos (com o que verificar)

| Projeto | Papel provável | [confirmar online] |
|---|---|---|
| OHF-Voice **piper1-gpl** / libpiper | já é o core neural | versão atual, C/C++/Python API, streaming/chunks, licença GPL, phonemização; comparar com nossa impl (TTFA/RTF/thread-safety/cancelamento) |
| **Kokoro** | já integrado (opcional) | licença dos pesos/voices.bin, tamanho, pt-BR, streaming |
| **Pocket TTS / Kyutai** | candidato baixa latência CPU | CPU-only? streaming? pt-BR? tamanho? evitar arrastar PyTorch de GBs |
| **KittenTTS** | ONNX pequeno | licença **atual** do código E dos modelos; pt-BR; maturidade |
| **Chatterbox / Resemble** | qualidade alta (pesado?) | pt-BR, CPU/GPU, tamanho, licença; provavelmente **opcional** |
| **F5-TTS** | comparação | **licença dos pesos** (possível non-commercial) vs código |
| **sherpa-onnx** | **camada de inferência comum** | suporta famílias TTS via ONNX; avaliar POC: reduz código/deps/subprocessos? |
| **Supertonic / Fish Speech / outros** | pesquisa | licença atual; não chamar de livre só por ver o código |
| **RHVoice / espeak-ng** | fallback/acessibilidade | manter; revisar integração (feito parcialmente) |

## sherpa-onnx — POC recomendada
Comparar: (atual) Python + Rust/PyO3 + ONNX custom + espeak; vs (proposta) GTK/Python → binding → sherpa-onnx
→ modelos. Só migrar com **POC + benchmark** mostrando ganho claro (menos código/deps/subprocessos, melhor
phonemização/cancelamento/streaming). Caso contrário, manter e melhorar a impl atual (que já mede RTF ~0.06).

## Licenças — checklist separado (por backend)
código · pesos · vozes · datasets (quando afetam redistribuição) · **pode o BigLinux redistribuir?**
Documentar cada um. Non-commercial nos pesos ⇒ **não** distribuir por padrão.

## Consultas sugeridas (executar)
- "piper1-gpl release libpiper C API streaming 2025/2026"
- "kokoro-82M license weights redistribution"
- "kittentts model license 2026"
- "kyutai pocket tts cpu streaming portuguese"
- "sherpa-onnx tts models list vits kokoro matcha"
- "f5-tts weights license non-commercial"
