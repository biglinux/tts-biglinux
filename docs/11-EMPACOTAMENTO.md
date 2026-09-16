# 11 — Empacotamento (Arch/BigLinux)

## Causa raiz do engine quebrado (C2)
- `Cargo.toml`: `ort = "2.0.0-rc.9"` (lock resolveu **rc.10**) — dependência **RC**.
- PKGBUILD roda `cargo build --release` **sem `--locked`/`--frozen`** → cargo pode re-resolver e o `ort`
  pode linkar contra um onnxruntime diferente do instalado. Em dev, o build pegou onnxruntime **1.24.x**
  (do pip em site-packages) enquanto o sistema tinha **1.29.0** → `.so` não carrega.

## Recomendações
- Pinar `ort` a uma versão exata e usar `cargo build --locked` no PKGBUILD.
- Garantir `ORT_LIB_LOCATION=/usr/lib ORT_PREFER_DYNAMIC_LINK=1` também fora do PKGBUILD (ex.: `.cargo/config.toml`)
  para builds de dev baterem com o pacote.
- `makedepends`: pinar `rust>=1.85` (edition 2024).
- Classificação (já correta): backends pesados (Piper models, Kokoro/PyTorch) em **optdepends**; core não os força.
- Remover a dupla provisão Kokoro: NÃO usar `pip install --break-system-packages` em `main_view.py`; usar os
  `optdepends` pacman (`python-kokoro`, `python-soundfile`).
- `.so` corretamente **não versionado** no git (built no pacote) — manter assim.

## Arquitetura de pacotes sugerida
- `tts-biglinux` (base: espeak-ng, speech-dispatcher, gtk4/libadwaita, engine nativo).
- Complementos opcionais: Piper (+vozes), Kokoro, RHVoice. Usuário de RHVoice/espeak não baixa GBs.
