# BigLinux TTS

<p align="center">
  <img src="usr/share/icons/hicolor/scalable/apps/biglinux-tts.svg" alt="BigLinux TTS" width="128">
</p>

<p align="center">
  <strong>Solução completa de texto-para-fala com interface gráfica nativa para BigLinux</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/licença-GPL--3.0-blue.svg" alt="Licença"></a>
  <img src="https://img.shields.io/badge/GTK-4-green.svg" alt="GTK4">
  <img src="https://img.shields.io/badge/libadwaita-1.x-purple.svg" alt="libadwaita">
  <img src="https://img.shields.io/badge/Python-3.10+-yellow.svg" alt="Python">
  <img src="https://img.shields.io/badge/motores-3-orange.svg" alt="3 motores TTS">
  <img src="https://img.shields.io/badge/idiomas-29-lightgrey.svg" alt="29 idiomas">
</p>

---

## Índice

- [Introdução](#introdução)
- [Funcionalidades](#funcionalidades)
- [Motores TTS](#motores-tts)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Uso](#uso)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Arquitetura](#arquitetura)
- [Configuração](#configuração)
- [Internacionalização](#internacionalização)
- [Detalhes Técnicos](#detalhes-técnicos)
- [Empacotamento](#empacotamento)
- [Licença](#licença)

---

## Introdução

O **BigLinux TTS** (Text-to-Speech) é um aplicativo nativo para desktop Linux que transforma texto em fala. Desenvolvido com GTK4 e libadwaita, é a ferramenta de leitura em voz alta do [BigLinux](https://www.biglinux.com.br/) — uma distribuição Linux brasileira baseada em Manjaro/Arch Linux.

O aplicativo resolve um problema prático: permitir que qualquer usuário ouça em voz alta textos selecionados na tela, sem configuração complicada. Basta selecionar um texto em qualquer janela, pressionar **Alt+V**, e o texto é lido automaticamente. Pressionando novamente, a leitura para (toggle).

### Para que serve

- **Acessibilidade**: pessoas com deficiência visual ou dificuldade de leitura podem ouvir conteúdos de tela
- **Multitarefa**: ouvir artigos, documentos ou e-mails enquanto realiza outras atividades
- **Aprendizado de idiomas**: ouvir a pronúncia correta de textos em diferentes idiomas
- **Revisão de texto**: detectar erros de escrita ao ouvir o que foi digitado
- **Produtividade**: transformar leitura passiva em escuta ativa

### Diferenciais

1. **Três motores TTS** — speech-dispatcher (RHVoice, espeak), espeak-ng direto e Piper Neural TTS, cobrindo desde vozes básicas até síntese neural de alta qualidade
2. **Descoberta automática de vozes** — escaneia automaticamente todos os motores e vozes instalados no sistema
3. **Processamento inteligente de texto** — expande abreviações (tb→também, vc→você), pronuncia caracteres especiais (#→cerquilha, @→arroba) e remove formatação HTML/Markdown
4. **Integração com KDE Plasma** — atalho global, ícone na bandeja do sistema (system tray), fixação no launcher
5. **Interface moderna** — design Adwaita com interface limpa, responsiva e acessível
6. **Multilíngue** — traduzido para 29 idiomas com sistema i18n baseado em gettext `.po`

---

## Funcionalidades

### Leitura de Texto

- **Atalho global Alt+V** — Selecione qualquer texto em qualquer aplicativo e pressione Alt+V para ouvir. Pressione novamente para parar (comportamento toggle)
- **Botão na bandeja do sistema** — Clique esquerdo no ícone do tray para ler o texto selecionado; clique direito para acessar configurações ou sair
- **Teste de voz integrado** — Campo de texto na interface para digitar e ouvir com a voz e configurações atuais
- **Fixação no launcher** — Opção para fixar o botão de falar na barra de tarefas do KDE Plasma

### Controle de Voz

- **Velocidade** — Escala de -100 (lento) a +100 (rápido), com marcações "Lento", "Normal" e "Rápido"
- **Tom** — Escala de -100 (grave) a +100 (agudo)
- **Volume** — Escala de 0 (mudo) a 100 (máximo), com ajuste fino via sox para Piper
- **Seleção de voz** — Lista dinâmica filtrada por motor, mostrando "Nome — Idioma [Qualidade]"

### Processamento de Texto

| Recurso | Descrição | Exemplo |
|---|---|---|
| **Expandir abreviações** | Converte gírias e abreviações do idioma | `tb` → "também", `vc` → "você" |
| **Caracteres especiais** | Pronuncia símbolos por extenso | `#` → "cerquilha", `@` → "arroba" |
| **Remover formatação** | Strip HTML tags, Markdown bold/italic/code | `**negrito**` → "negrito" |
| **Ler URLs** | Opção de incluir ou ignorar links | `https://...` → lê ou ignora |
| **Limite de caracteres** | Trunca textos muito longos | Ilimitado, 1K, 5K, 10K, 50K, 100K |

### Atalhos de Teclado

| Atalho | Ação |
|---|---|
| **Alt+V** (padrão) | Ler/parar texto selecionado (toggle) |
| **Ctrl+Q** | Fechar o aplicativo |
| Configurável | O atalho pode ser alterado na interface com captura visual de teclas |

### Bandeja do Sistema (System Tray)

- Ícone na área de notificação usando PySide6 `QSystemTrayIcon` em subprocesso isolado
- **Clique esquerdo**: ler texto selecionado (toggle falar/parar)
- **Clique direito**: menu com "Configurações" e "Sair"
- Executa em processo separado para evitar conflitos GTK/Qt
- Comunicação via protocolo JSON lines sobre stdin/stdout

---

## Motores TTS

### 1. speech-dispatcher (Padrão)

Motor principal que roteia a fala através do daemon speech-dispatcher. Suporta múltiplos módulos de saída:

| Módulo | Descrição | Qualidade |
|---|---|---|
| **RHVoice** | Vozes de alta qualidade, com suporte forte para pt-BR (Letícia F123) e inglês | ★★★★ |
| **espeak-ng** | Leve, suporta 100+ idiomas, qualidade básica | ★★ |
| **pico** | SVOX Pico TTS | ★★★ |
| **festival** | Sistema Festival da Universidade de Edinburgh | ★★ |

**Implementação técnica**:
- Comunicação via API Python `speechd.SSIPClient` (SSIP — Speech Synthesis Interface Protocol)
- Cria conexão: `SSIPClient("biglinux-tts")`
- Define módulo de saída e voz selecionada
- Envia texto com callback de conclusão para detectar fim da fala
- **Fallback robusto**: se a API Python falhar, auto-reinicia o daemon (`systemctl --user restart speech-dispatcher`) e tenta novamente; se isso também falhar, cai para execução CLI via `spd-say --wait`

**Mapeamento de parâmetros**:
- Rate: -100 a 100 (passado diretamente ao SSIP)
- Pitch: -100 a 100 (passado diretamente)
- Volume: 0-100 → -100 a 100 (fórmula: `(volume × 2) - 100`)

### 2. espeak-ng (Direto)

Ignora o speech-dispatcher e chama o espeak-ng diretamente via subprocesso. Útil para menor latência e configuração mais simples.

**Comando**: `espeak-ng -v {voz} -s {wpm} -p {pitch} -a {volume} {texto}`

**Mapeamento de parâmetros**:

| Parâmetro App | Fórmula | Faixa Final |
|---|---|---|
| Rate (-100 a 100) | `175 + (rate × 1.5)` | 80-450 WPM |
| Pitch (-100 a 100) | `50 + (pitch × 0.5)` | 0-99 |
| Volume (0-100) | `volume × 2` | 10-200 (mínimo 10 para ser audível) |

### 3. Piper (Neural TTS) ★★★★★

Motor de síntese neural offline usando modelos ONNX. Produz fala de qualidade próxima à humana.

- **Binário**: `piper-tts` (pacote `piper-tts-bin`)
- **Modelos**: arquivos `.onnx` em `/usr/share/piper-voices/{idioma}/{região}/{locutor}/{qualidade}/`
- **Configuração**: arquivo `.onnx.json` ao lado de cada modelo com metadados

**Pipeline de reprodução**:
1. **Fase de síntese** (thread em background):
   - `piper-tts --model {caminho} --output_file {temp.wav} --length_scale {ls} --noise_scale {ns}`
   - Recebe texto via stdin, gera arquivo WAV temporário
2. **Fase de reprodução**:
   - Com sox: `aplay` → `sox vol {fator}` para controle de volume
   - Sem sox: `aplay -r 22050 -f S16_LE -t raw -q` (reprodução direta)
3. **Limpeza**: arquivo WAV temporário removido após reprodução

**Mapeamento de parâmetros**:

| Parâmetro App | Mapeamento Piper | Efeito |
|---|---|---|
| Rate -100 | `length_scale = 0.3` | Muito rápido |
| Rate 0 | `length_scale = 1.0` | Normal |
| Rate +100 | `length_scale = 2.5` | Muito lento |
| Pitch (-100 a 100) | `noise_scale = 0.667 ± 0.333` | Variação de entonação |
| Volume (0-100) | `sox vol factor = volume/50` | 0.2x a 2.0x |

### Descoberta Automática de Vozes

O sistema descobre vozes de todos os motores simultaneamente:

1. **RHVoice**: `spd-say -o rhvoice -L` → parsing dos nomes SSIP, com mapa de metadados hardcoded (idioma, gênero). Fallback para scan de diretório `/usr/share/RHVoice/voices/` e pacotes pacman `rhvoice-voice-*`
2. **espeak-ng**: `espeak-ng --voices` → parsing do formato tabular com código de idioma e gênero
3. **Piper**: scan de diretórios `/usr/share/piper-voices/`, `~/.local/share/piper-voices/` → detecção de arquivos `.onnx` com `.onnx.json` padrão

Resultado: `VoiceCatalog` com todas as vozes disponíveis, filtráveis por idioma, motor e qualidade.

---

## Requisitos

### Dependências Obrigatórias

| Pacote | Descrição |
|---|---|
| `python` (3.10+) | Interpretador Python |
| `python-gobject` | Bindings GTK para Python (PyGObject) |
| `gtk4` | Toolkit gráfico GTK versão 4 |
| `libadwaita` | Biblioteca de widgets Adwaita (GNOME HIG) |
| `speech-dispatcher` | Daemon de síntese de fala |
| `espeak-ng` | Motor TTS de código aberto |
| `xsel` | Acesso ao clipboard X11 (seleção primária) |
| `wl-clipboard-rs` | Acesso ao clipboard Wayland (wl-paste) |
| `alsa-utils` | Utilitários de áudio ALSA (aplay) |

### Dependências Opcionais

| Pacote | Descrição |
|---|---|
| `pyside6` | Ícone na bandeja do sistema (QSystemTrayIcon via subprocesso) |
| `rhvoice` | Motor TTS multilíngue de alta qualidade |
| `rhvoice-voice-leticia-f123` | Voz feminina em português brasileiro |
| `rhvoice-voice-evgeniy-eng` | Voz masculina em inglês |
| `rhvoice-brazilian-portuguese-complementary-dict-biglinux` | Dicionário complementar pt-BR |
| `piper-tts-bin` | Motor TTS neural offline |
| `piper-voices-pt-BR` | Vozes neurais em português brasileiro |
| `sox` | Controle de volume para áudio Piper |

---

## Instalação

### BigLinux / Manjaro / Arch Linux

```bash
# Instalar do repositório BigLinux
sudo pacman -S tts-biglinux

# Opcional: voz RHVoice em português
sudo pacman -S rhvoice rhvoice-voice-leticia-f123

# Opcional: TTS neural Piper
sudo pacman -S piper-tts-bin piper-voices-pt-BR

# Opcional: bandeja do sistema
sudo pacman -S pyside6
```

### Compilar o Pacote (makepkg)

```bash
git clone https://github.com/biglinux/tts-biglinux.git
cd tts-biglinux/pkgbuild
makepkg -si
```

### Executar sem Instalar (Desenvolvimento)

```bash
git clone https://github.com/biglinux/tts-biglinux.git
cd tts-biglinux/usr/share/biglinux/tts-biglinux
python main.py --debug
```

---

## Uso

### Interface Gráfica

```bash
biglinux-tts            # Abre a janela de configurações
biglinux-tts --debug    # Modo debug com log detalhado
biglinux-tts --version  # Exibe versão
```

### Atalho do Teclado (Modo CLI)

```bash
biglinux-tts-speak      # Lê o texto selecionado (chamado pelo Alt+V)
```

O script `biglinux-tts-speak` funciona como toggle:
1. Se já está falando → para imediatamente (mata o processo via PID em `/tmp/`)
2. Se tem texto selecionado → lê em voz alta
3. Se não tem texto → sai silenciosamente

### Fluxo Típico

1. **Primeiro uso**: o app mostra um diálogo de boas-vindas explicando os recursos
2. **Configuração**: selecione o motor TTS, voz e ajuste velocidade/tom/volume
3. **Teste**: digite um texto no campo de teste e clique "Testar Voz"
4. **No dia a dia**: selecione texto em qualquer janela → Alt+V → ouça

### Configurando a Voz

1. Abra o aplicativo (`biglinux-tts`)
2. Selecione um **Motor TTS** (speech-dispatcher, espeak-ng ou Piper)
3. Escolha uma **Voz** na lista de vozes descobertas
4. Ajuste **Velocidade**, **Tom** e **Volume** com os sliders
5. Digite um texto de teste e clique **Testar voz** para visualizar
6. Todas as alterações são salvas automaticamente

### Alterando o Atalho de Teclado

1. Abra **Opções avançadas → Atalho de teclado**
2. Clique em **Alterar**
3. Pressione a combinação de teclas desejada (ex: Ctrl+Shift+S)
4. O novo atalho é salvo e aplicado aos atalhos globais do KDE imediatamente

### Instalando Piper Neural TTS

1. Selecione **Piper (Neural TTS)** como motor
2. Se o Piper não estiver instalado, um diálogo de instalação aparece
3. Clique **Instalar** para baixar automaticamente via `pacman`:
   - `piper-tts-bin` — O binário Piper
   - `piper-voices-<idioma>` — Modelos de voz para seu idioma
4. Após instalação, as vozes são descobertas automaticamente

---

## Estrutura do Projeto

```
tts-biglinux/
├── locale/                              # Arquivos de tradução fonte (.po, .pot)
│   ├── tts-biglinux.pot                 # Template de tradução (29 idiomas)
│   ├── pt-BR.po                         # Português brasileiro
│   ├── en.po                            # Inglês
│   └── ...                              # bg, cs, da, de, el, es, et, fi, fr, he,
│                                        # hr, hu, is, it, ja, ko, nl, no, pl, pt,
│                                        # ro, ru, sk, sv, tr, uk, zh
├── pkgbuild/
│   └── PKGBUILD                         # Script de empacotamento Arch/BigLinux
├── usr/
│   ├── bin/
│   │   ├── biglinux-tts                 # Entry point bash → python main.py
│   │   └── biglinux-tts-speak           # Script bash standalone para Alt+V
│   └── share/
│       ├── applications/
│       │   └── br.com.biglinux.tts.desktop   # Launcher .desktop
│       ├── biglinux/tts-biglinux/       # ← Código Python do aplicativo
│       │   ├── main.py                  # Entry point: CLI args, logging, App.run()
│       │   ├── application.py           # Adw.Application: ciclo de vida, ações
│       │   ├── config.py               # Constantes, enums, dataclasses, I/O JSON
│       │   ├── window.py               # Adw.ApplicationWindow: header bar, menu
│       │   ├── services/
│       │   │   ├── tts_service.py       # Máquina de estados TTS: speak/stop
│       │   │   ├── voice_manager.py     # Descoberta de vozes (3 motores)
│       │   │   ├── text_processor.py    # Processamento: abrev, chars, URLs
│       │   │   ├── clipboard_service.py # Clipboard Wayland (wl-paste) / X11
│       │   │   ├── settings_service.py  # Persistência JSON com debounce
│       │   │   └── tray_service.py      # System tray PySide6 em subprocesso
│       │   ├── ui/
│       │   │   ├── main_view.py         # View principal: hero, controles, seções
│       │   │   ├── components.py        # 9 fábricas de widgets Adwaita
│       │   │   └── welcome_dialog.py    # Diálogo de boas-vindas (primeiro uso)
│       │   ├── utils/
│       │   │   ├── async_utils.py       # Debouncer, run_in_thread
│       │   │   └── i18n.py             # Sistema i18n (parsing .po)
│       │   └── resources/
│       │       ├── __init__.py          # load_css() — carrega style.css
│       │       └── style.css            # CSS customizado (hero, animações)
│       ├── icons/hicolor/scalable/
│       │   ├── apps/biglinux-tts.svg        # Ícone do aplicativo
│       │   └── status/tts-biglinux-symbolic.svg  # Ícone symbolic (tray)
│       ├── khotkeys/
│       │   └── ttsbiglinux.khotkeys     # Atalho KDE Plasma 5
│       └── locale/                      # Traduções compiladas (.mo)
│           ├── pt_BR/LC_MESSAGES/tts-biglinux.mo
│           └── .../                     # 28 idiomas
└── README.md
```

---

## Arquitetura

### Diagrama de Componentes

```
┌─────────────────────────────────────────────────────────────────┐
│                         main.py                                 │
│               Argument parsing, logging setup                   │
│                    TTSApplication.run()                          │
├─────────────────────────────────────────────────────────────────┤
│                    application.py                                │
│         TTSApplication (Adw.Application)                        │
│    startup → activate → shutdown lifecycle                      │
│    Global actions: about, quit, tray setup                      │
├──────────────────┬──────────────────┬───────────────────────────┤
│    UI Layer      │  Service Layer   │  Data Layer               │
├──────────────────┼──────────────────┼───────────────────────────┤
│ window.py        │ tts_service.py   │ config.py                 │
│ ├ HeaderBar      │ ├ speak()        │ ├ AppSettings             │
│ ├ NavigationView │ ├ stop()         │ ├ SpeechConfig            │
│ └ Toast overlay  │ └ state machine  │ ├ TextConfig              │
│                  │                  │ ├ ShortcutConfig           │
│ main_view.py     │ voice_manager.py │ └ WindowConfig             │
│ ├ Hero section   │ └ discover()     │                           │
│ ├ Voice controls │                  │ settings_service.py       │
│ ├ Backend select │ text_processor.py│ ├ load/save JSON          │
│ ├ Text options   │ ├ abbreviations  │ └ debounced auto-save     │
│ └ Advanced       │ ├ special chars  │                           │
│                  │ └ formatting     │                           │
│ components.py    │                  │                           │
│ └ 9 factories    │ clipboard_svc.py │                           │
│                  │ ├ wl-paste       │                           │
│ welcome_dialog.py│ └ xsel           │                           │
│ └ First-run      │                  │                           │
│                  │ tray_service.py  │                           │
│                  │ └ PySide6 subproc│                           │
├──────────────────┼──────────────────┼───────────────────────────┤
│   utils/i18n.py  │   utils/async_utils.py                      │
│   └ _() function │   ├ Debouncer (GLib.timeout_add)             │
│                  │   └ run_in_thread (daemon + GLib.idle_add)   │
└──────────────────┴──────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
      speech-dispatcher   espeak-ng       piper-tts
      (speechd.SSIPClient)  (direct)     (stdin → aplay)
```

### Camadas

- **UI Layer** — Widgets GTK4 + libadwaita seguindo GNOME HIG. Seção hero com status ao vivo, grupos de preferências para configurações de voz, seleção de motor, processamento de texto e opções avançadas. Notificações toast para feedback
- **Service Layer** — Máquina de estados TTS com ciclo speak/stop/cleanup. Descoberta de vozes em background. Pipeline de normalização de texto. Acesso ao clipboard tanto Wayland quanto X11
- **Data Layer** — Dataclasses tipadas para todas as configurações. Persistência JSON em `~/.config/biglinux-tts/settings.json`. Defaults sensatos com capacidade de reset

### Máquina de Estados TTS

```
         speak()              stop() / error / conclusão
  ┌──────────────┐       ┌──────────────────────────────┐
  │              ▼       │                              │
  │         ┌────────┐   │   ┌──────────┐              │
  │         │  IDLE  │───┘   │ SPEAKING │──────────────┘
  │         └────────┘       └──────────┘
  │              │                │
  │         speak()          error()
  │              │                │
  │         ┌────▼────┐     ┌────▼─────┐
  │         │SPEAKING │     │  ERROR   │
  │         └─────────┘     └──────────┘
  │                              │
  └──────────────────────────────┘
                speak()
```

- **IDLE**: nenhuma fala em progresso, pronto para receber comandos
- **SPEAKING**: áudio sendo reproduzido; monitorado por polling a cada 300ms via `GLib.timeout_add`
- **ERROR**: erro ocorreu (motor indisponível, modelo não encontrado); retorna a IDLE na próxima tentativa

### Protocolo IPC do System Tray

O system tray roda em um subprocesso PySide6 separado, comunicando-se com o processo GTK principal via JSON lines:

```
Processo GTK (pai)              Processo Qt (filho)
      │                                │
      │── {"cmd":"set_menu",...} ──────▶│  configura menu de contexto
      │── {"cmd":"set_tooltip",...} ───▶│  define tooltip
      │                                │
      │◀── {"event":"ready"} ─────────│  tray icon visível
      │◀── {"event":"activate"} ──────│  clique esquerdo
      │◀── {"event":"menu","id":1} ───│  item de menu clicado
      │                                │
      │── {"cmd":"quit"} ─────────────▶│  encerrar
      │                                │
```

O processo pai monitora stdout do filho via `GLib.IOChannel.unix_new()` com watch não-bloqueante.

---

## Configuração

### Arquivos de Configuração

| Caminho | Conteúdo |
|---|---|
| `~/.config/biglinux-tts/settings.json` | Todas as configurações do app (JSON) |
| `/tmp/biglinux-tts-{usuario}.pid` | PID do processo de fala (toggle Alt+V) |

### Schema de Configurações (`settings.json`)

```json
{
  "speech": {
    "rate": -25,
    "pitch": -25,
    "volume": 75,
    "voice_id": "piper:/usr/share/piper-voices/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx",
    "backend": "piper",
    "output_module": "rhvoice"
  },
  "text": {
    "expand_abbreviations": true,
    "process_urls": false,
    "process_special_chars": true,
    "strip_formatting": true,
    "max_chars": 0
  },
  "shortcut": {
    "keybinding": "<Alt>v",
    "enabled": true,
    "show_in_launcher": false
  },
  "window": {
    "width": 560,
    "height": 680,
    "maximized": false
  },
  "show_welcome": true
}
```

### Tabela de Configurações

| Configuração | Descrição | Padrão |
|---|---|---|
| `speech.backend` | Motor TTS: `speech-dispatcher`, `espeak-ng`, `piper` | `speech-dispatcher` |
| `speech.output_module` | Módulo speech-dispatcher: `rhvoice`, `espeak-ng`, etc. | `rhvoice` |
| `speech.voice_id` | Identificador da voz (específico do motor) | Auto-detectado |
| `speech.rate` | Velocidade da fala (-100 a 100) | `0` |
| `speech.pitch` | Tom da voz (-100 a 100) | `0` |
| `speech.volume` | Volume da fala (0 a 100) | `80` |
| `text.expand_abbreviations` | Substituir abreviações por palavras completas | `true` |
| `text.process_special_chars` | Ler símbolos (#, @, %) pelo nome | `false` |
| `text.strip_formatting` | Remover HTML/Markdown antes de ler | `true` |
| `text.process_urls` | Ler URLs em voz alta | `false` |
| `text.max_chars` | Limite de caracteres (0 = ilimitado) | `0` |
| `shortcut.keybinding` | Atalho global de teclado | `<Alt>v` |
| `shortcut.show_in_launcher` | Fixar botão de fala na barra de tarefas | `false` |
| `window.width/height` | Dimensões da janela em pixels | `560×680` |
| `show_welcome` | Mostrar diálogo de boas-vindas | `true` |

### Migração de Configurações Legadas

O app detecta automaticamente configurações do formato antigo em `~/.config/tts-biglinux/` (arquivos individuais: `rate`, `pitch`, `volume`, `voice`) e consolida tudo em um único JSON no novo caminho.

---

## Internacionalização

### Sistema i18n

O sistema de tradução usa arquivos gettext `.po` (formato texto, não binário `.mo`) com parsing próprio em Python:

1. **Detecção de locale**: `LANGUAGE` → `LC_ALL` → `LC_MESSAGES` → `LANG`
2. **Busca de arquivo**: tenta variantes `pt-BR` e `pt_BR`, depois fallback para código base `pt`
3. **Caminhos de busca**: `./locale/` (dev) → `/usr/share/tts-biglinux/locale/` (instalado)

**Uso no código:**
```python
from utils.i18n import _

label.set_text(_("Ready to speak"))  # → "Pronto para falar" em pt-BR
```

### Idiomas Disponíveis (29)

| Código | Idioma | Código | Idioma |
|---|---|---|---|
| bg | Búlgaro | ko | Coreano |
| cs | Tcheco | nl | Holandês |
| da | Dinamarquês | no | Norueguês |
| de | Alemão | pl | Polonês |
| el | Grego | pt | Português |
| en | Inglês | pt-BR | Português (Brasil) |
| es | Espanhol | ro | Romeno |
| et | Estoniano | ru | Russo |
| fi | Finlandês | sk | Eslovaco |
| fr | Francês | sv | Sueco |
| he | Hebraico | tr | Turco |
| hr | Croata | uk | Ucraniano |
| hu | Húngaro | zh | Chinês |
| is | Islandês | it | Italiano |
| ja | Japonês |  |  |

### Adicionando uma Nova Tradução

1. Copie o template: `cp locale/tts-biglinux.pot locale/<idioma>.po`
2. Edite o arquivo `.po` com suas traduções
3. Compile: `msgfmt locale/<idioma>.po -o usr/share/locale/<idioma>/LC_MESSAGES/tts-biglinux.mo`

---

## Detalhes Técnicos

### Processamento de Texto

O módulo `text_processor.py` aplica um pipeline de transformações ao texto antes da síntese:

1. **Strip de formatação** (se ativo): remove tags HTML (`<[^>]+>`), Markdown bold/italic/code, headers, listas, links
2. **Remoção/leitura de URLs** (configurável): remove ou mantém `https?://\S+`
3. **Expansão de abreviações** (sensível ao idioma):
   - **Português**: ~65 abreviações — `tb`→"também", `vc`→"você", `blz`→"beleza", `rsrs`→"risos", `pq`→"porque", `msg`→"mensagem", `obg`→"obrigado", `vlw`→"valeu", etc.
   - **Inglês**: ~30 — `btw`→"by the way", `idk`→"I don't know", `tbh`→"to be honest", etc.
   - **Espanhol**: ~10 — `tb`→"también", `pq`→"porque", etc.
4. **Caracteres especiais** (sensível ao idioma):
   - **Português**: `#`→"cerquilha", `@`→"arroba", `%`→"por cento", `&`→"e comercial", `$`→"cifrão"
   - **Inglês**: `#`→"hash", `@`→"at", `%`→"percent", `&`→"ampersand", `$`→"dollar"
5. **Limpeza final**: colapsa espaços/quebras de linha múltiplos

### Acesso ao Clipboard

O módulo `clipboard_service.py` detecta automaticamente o ambiente gráfico:

- **Wayland**: `wl-paste --primary --no-newline` (seleção primária), fallback para clipboard regular
- **X11**: `xsel --primary -o`, fallback para `xsel -o`, depois `xclip`
- **Timeout**: 3 segundos por comando
- **Detecção**: `XDG_SESSION_TYPE == "wayland"` ou presença de `WAYLAND_DISPLAY`

### Persistência com Debounce

O `settings_service.py` implementa salvamento automático com debounce de 500ms:

1. Qualquer alteração de configuração agenda um timer `GLib.timeout_add(500ms)`
2. Se outra alteração ocorrer antes dos 500ms, o timer anterior é cancelado
3. Após 500ms sem alterações, o JSON é gravado em disco
4. Garante consistência sem I/O excessivo durante ajustes rápidos de sliders

### CSS e Animações

O arquivo `resources/style.css` define o tema visual usando variáveis CSS do Adwaita:

- **Hero section**: gradiente de fundo com cor de destaque do tema (`@accent_bg_color`)
- **Animação de fala**: pulsação do ícone durante reprodução (`@keyframes pulse-speaking`)
- **Badges de qualidade**: chips coloridos para vozes neurais (cor de sucesso)
- **Responsividade**: `Adw.Clamp` limita largura a 600px com threshold de 400px

### Widgets de UI (components.py)

9 funções fábricas para criar widgets Adwaita consistentes:

| Fábrica | Widget | Uso |
|---|---|---|
| `create_preferences_group` | `Adw.PreferencesGroup` | Grupos de configurações |
| `create_action_row_with_switch` | `Adw.ActionRow` + `Gtk.Switch` | Toggles on/off |
| `create_action_row_with_scale` | `Adw.ActionRow` + `Gtk.Scale` | Sliders (velocidade, tom, volume) |
| `create_combo_row` | `Adw.ComboRow` | Seleção de opções (motor, voz) |
| `create_spin_row` | `Adw.SpinRow` | Entrada numérica |
| `create_expander_row` | `Adw.ExpanderRow` | Seções expansíveis |
| `create_button_row` | `Gtk.Button` | Botões de ação |
| `create_icon_button` | `Gtk.Button` (ícone) | Botões com ícone |

Todos os widgets incluem `AccessibleProperty.LABEL` para acessibilidade.

### Async e Threading

- **Debouncer**: implementado com `GLib.timeout_add()` — salva configurações após 500ms de inatividade
- **run_in_thread**: executa operações pesadas (clipboard, descoberta de vozes) em daemon threads, entregando resultado ao main thread via `GLib.idle_add()`
- **TTS monitoring**: polling a cada 300ms via `GLib.timeout_add()` para detectar conclusão da fala (speech-dispatcher) ou fim do processo (espeak-ng, Piper)
- **UI thread**: nenhuma operação bloqueante na thread principal GTK

### Integração com KDE

- **kglobalshortcutsrc**: atalho registrado via `kwriteconfig6` no `kglobalshortcutsrc5`
- **Desktop file**: `X-KDE-Shortcuts` no `.desktop` para integração com Plasma
- **kbuildsycoca6**: regenera cache de serviços após alterações de atalhos
- **icontasks**: configuração do launcher para fixação na barra de tarefas

### Script Bash `biglinux-tts-speak`

Script standalone acionado pelo atalho global Alt+V. Funcionamento:

1. Verifica se já está falando (`/tmp/biglinux-tts-speak-{user}.pid`)
   - Se sim → mata o processo e sai (toggle off)
2. Captura texto selecionado via clipboard (wl-paste/xsel)
3. Aplica processamento de texto (abreviações, caracteres especiais)
4. Lê configurações de `~/.config/biglinux-tts/settings.json`
5. Executa o motor TTS configurado (speech-dispatcher/espeak-ng/piper)
6. Registra PID para permitir toggle na próxima chamada

---

## Empacotamento

### PKGBUILD

O pacote é construído para Arch Linux / BigLinux / Manjaro:

```bash
pkgname=tts-biglinux
pkgver=$(date +%y.%m.%d)    # Versionamento por data (ex: 25.06.19)
pkgrel=$(date +%H%M)        # Release por hora (múltiplos builds/dia)
arch=('any')                 # Independente de plataforma (Python puro)
license=('GPL')
```

A função `package()` copia a árvore `usr/` para o destino, mantendo a estrutura de diretórios já pronta no repositório. Cria o symlink de ícone para compatibilidade com nome antigo.

### Padrão de Diretórios BigLinux

O projeto segue o padrão BigLinux para aplicativos Python:

```
usr/share/biglinux/{nome-do-app}/    # Código Python
usr/bin/{nome-do-app}                # Script bash: cd + exec python main.py
```

Este padrão evita a necessidade de `pyproject.toml`, `pip install`, ou `site-packages`, simplificando empacotamento e distribuição.

---

## Licença

Este projeto é licenciado sob a [GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html).

Os motores TTS (speech-dispatcher, espeak-ng, RHVoice, Piper) possuem suas próprias licenças. Consulte a documentação de cada motor para detalhes.

---

## Autores

- **Tales A. Mendonça** — Projeto BigLinux
- **Bruno Gonçalves Araujo** — Projeto BigLinux
- **Rafael Ruscher** — Desenvolvimento v3.0

---

<p align="center">
  <img src="usr/share/icons/hicolor/scalable/apps/biglinux-tts.svg" alt="BigLinux TTS" width="48">
  <br>
  <em>BigLinux TTS v3.0 — Leitura de texto por voz para o desktop Linux</em>
</p>