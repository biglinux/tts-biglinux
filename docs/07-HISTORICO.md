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
- ✅ **Pipeline GStreamer preguiçoso**: `AudioPlayerWidget` não cria mais `playbin` no `__init__` — só na
  primeira reprodução (`_ensure_pipeline`). Elimina a causa dominante do congelamento (~2000 pipelines eager).
  Teste headless: `tests/test_history_view_lazy.py`.
- ✅ **Uma única view materializada** (lista OU grade, nunca as duas): `_rebuild_active()` reconstrói só a
  view visível; troca de view reconstrói preguiçosamente.
- ✅ **Construção em lotes** (40 widgets por ciclo `idle`) → 1ª tela aparece rápido mesmo com milhares.
- ✅ **Carga do `history.json` fora do main thread** (parse em thread → `idle_add`).
- ✅ **Busca com debounce** (200 ms) e filtrando só os itens da view ativa (sem `zip` das duas árvores).

## Plano (próxima fase)
- **Virtualização real** com `Gtk.ListView`/`GridView` + `Gio.ListModel` + reciclagem de factory (os itens já
  não têm pipeline eager; o próximo passo é não realizar todos os widgets de uma vez).
- **SQLite** (doc 12) com WAL, índices, paginação; migração automática de `history.json` sem perder dados.
- **Retenção** configurável (nº/idade/espaço), nunca excluindo silenciosamente fora da política.
- Benchmark obrigatório com hardware: abrir com 0/10/100/500/1000/5000 entradas (tempo até 1ª tela, RAM,
  scroll, busca). **[pendente hardware]**
