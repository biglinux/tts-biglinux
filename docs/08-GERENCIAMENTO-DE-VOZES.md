# 08 — Gerenciamento de Vozes e Downloads (plano)

## Problemas confirmados (audit)
- **Kokoro download**: sem `.part`/atomicidade; **reescrita in-place de `voices.bin`** pode corromper TODAS
  as vozes extras; sem retry, checksum, verificação de espaço; slurp único em memória.
- **Sem progresso real**: apenas spinner; tamanho "~512 KB" falso.
- **Preview cai em inglês** quando `language` é vazio/`multi`/`unknown`; espeak preview sem `-v <lang>`;
  Piper instalado sem botão de preview.
- **Duas enumerações divergentes** de vozes (catálogo vs diálogo/pacman); dois `_guess_gender` conflitantes.
- Piper/RHVoice/espeak instalam via `pkexec pacman` (seguro); Kokoro é o caminho frágil.

## Plano
- Download atômico: baixar para `*.part` → validar tamanho/hash → `os.replace`. Nunca deixar `.onnx`/voz
  parcial parecendo válida.
- Escrita **não-destrutiva** de `voices.bin` (temp + rename); nunca truncar o arquivo do usuário in-place.
- Progresso real (bytes/total/velocidade/cancelar/retry) com callback; UI com `Gtk.ProgressBar`.
- Preview: usar a voz/idioma corretos; para não-reconhecidos, usar locale do sistema (nunca inglês por engano).
- UX (doc 09): dropdown de **Backend** no topo; vozes do backend atual em destaque; ação principal clara +
  menu (Instalar/Desinstalar/Testar/Informações/Licença); priorizar idioma do sistema (pt-BR).
- Segurança: sanitizar `voice_id` (sem `../`, sem barras) antes de compor URL/nome de arquivo.
