# 09 — UX/UI (plano)
Referência visual: `ruscher/big-video-converter` (mesma família BigLinux; TTS deve ser ainda mais refinado).

- Componentes libadwaita onde fizer sentido: ToolbarView, NavigationSplitView, PreferencesPage/Group,
  ActionRow/ExpanderRow, StatusPage, ToastOverlay, Banner, Clamp.
- Tela principal enxuta: backend atual, voz atual, falar/testar, estado, velocidade, expressividade, volume.
- Avançado organizado por Perfil (Economia/Balanceado/Baixa latência/Qualidade), Áudio, Fala; mostrar só o
  que o backend suporta (usar `BackendCapabilities`, doc 04).
- **Pitch do Piper** → rótulo "Expressividade" (não é pitch). Não enganar o usuário.
- Estados visíveis sem modal invasivo; usar `Adw.Toast` para mensagens simples.
- Acessibilidade: navegação por teclado, focus order, labels acessíveis, contraste, dark/light.
- i18n: toda string nova via gettext; atualizar POT.
