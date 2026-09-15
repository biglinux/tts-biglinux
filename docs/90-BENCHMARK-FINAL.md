# 90 — Benchmark Final (a completar)

Reservado para os números **depois** das próximas fases (prewarm, streaming, histórico virtualizado),
comparados ao baseline em `docs/03-BASELINE-BENCHMARK.md`.

Métricas obrigatórias (antes/depois, com %):
- TTFA 1ª fala Piper (esperado: ~1.0 s → ~0.06 s com prewarm).
- TTFA de texto longo (streaming por sentença).
- RAM por backend e após "trocar voz 100×" (estabilização).
- Tempo até 1ª tela do Histórico com 1000/5000 entradas.
- Latência de stop percebida.
- Processos órfãos após encerrar (esperado: 0).

[pendente hardware — requer o desktop ao vivo do usuário]
