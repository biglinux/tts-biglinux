# 07 — Histórico (plano de rework)

## Problemas confirmados (audit)
1. **Duas árvores completas** (ListBox de rows + FlowBox de cards) construídas *simultaneamente* a cada
   reload, mesmo com só uma visível.
2. **Sem virtualização**: N rows + N cards realizados de uma vez.
3. **~2000 pipelines GStreamer** (`playbin`) criados *eagerly* (2 por entrada com áudio).
4. Milhares de `is_file()` por reload.
5. `history.json` **reescrito inteiro** a cada save (O(N)).
6. **IDs por timestamp de segundos** → colisão/sobrescrita. ✅ **Corrigido** nesta fase (id uuid + µs).
7. Busca sem debounce, itera todos os widgets por tecla.

## Correções já aplicadas
- ✅ ID único (`uuid4`) + timestamp com microssegundos → sem perda de dados. Teste: `tests/test_history.py`.
- ✅ Parser de timestamp tolerante (formato novo e legado) na UI.

## Plano (próxima fase)
- **Uma** view virtualizada: `Gtk.ListView`/`GridView` + `Gio.ListModel` + factory/reciclagem. Nunca manter
  lista e grade completas ao mesmo tempo.
- **Players sob demanda**: criar `AudioPlayerWidget`/pipeline só quando a linha entra em viewport / ao dar play.
- **SQLite** (doc 12) com WAL, índices, paginação; migração automática de `history.json` sem perder dados.
- **Busca** com debounce (150–250 ms) e, se justificar, FTS5.
- **Retenção** configurável (nº/idade/espaço), nunca excluindo silenciosamente fora da política.
- Benchmark obrigatório: abrir com 0/10/100/500/1000/5000 entradas (tempo até 1ª tela, RAM, scroll, busca).
