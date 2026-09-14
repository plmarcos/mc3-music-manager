# CLAUDE.md — MC3 Music Manager (edição Web-view)

> ## 🚨 O PC FOI FORMATADO EM 29/07/2026
>
> O disco **C: foi apagado**; o SSD **F: (onde este projeto vive) foi desconectado e sobreviveu intacto**.
> Sua memória de sessões anteriores **não existe mais** — ela estava no C:.
>
> **ANTES DE QUALQUER COISA, leia o documento mestre de retomada:**
> ### 📄 `F:\Importantes\MyScriptsClaude\RETOMAR-AQUI.md`
> (fica **um nível acima** desta pasta — 9 seções: restaurar o ambiente, mapa dos projetos,
> estado dos dois apps, decisões a não re-litigar, armadilhas e onde paramos)
>
> **E restaure a memória** (100 arquivos, 10 projetos, já testado):
> clique com o botão direito em
> `F:\Importantes\MyScriptsClaude\_MEMORIA CLAUDE (BACKUP)\RESTAURAR-MEMORIA.ps1`
> → *"Executar com o PowerShell"*.
>
> Se o Python/ffmpeg/ImgBurn ainda não foram reinstalados, o **§2 do RETOMAR-AQUI.md** tem o passo a passo.
> *(Quando o ambiente estiver restaurado e você tiver lido tudo, pode apagar este aviso.)*

> **Leia este arquivo primeiro.** Ele existe para você (Claude) NÃO começar do zero.
> Resume o que é o projeto, as decisões já tomadas, o estado atual, como rodar, e
> as armadilhas a evitar. Complementa `README.md` (visão geral) e `ROADMAP.md` (plano).

---

## 🎯 O que é

Porte da interface do **MC3 Music Manager** — um app desktop Windows que troca a
trilha sonora do jogo de PS2 *Midnight Club 3: DUB Edition Remix* — de **Tkinter**
para **web (HTML/CSS/JS)**, mantendo o **Python como backend** (reaproveitando toda
a lógica: ferramentas PS2, ffmpeg, ISO, backup).

Stack: **pywebview** (janela nativa via Edge **WebView2**, sem empacotar Chromium).

## 🚧 REGRA DE OURO (não quebrar)

O app **original em Tkinter** vive em `..\MC3 MUSIC TUT` e está **em uso diário**.
- **NÃO modifique o projeto original.** Este porte é 100% isolado nesta pasta.
- A diretriz do dono: **regressão do fluxo que funciona é o risco #1.**
- Migração é **faseada, tela por tela, NUNCA big-bang.**

## ✅ Estado atual (v0.3.0 — verificado na tela)

Um "walking skeleton" + o **primeiro recurso real** funcionando ponta a ponta:
- Janela pywebview renderiza (tema escuro moderno).
- **JS → Python** (`pywebview.api.*`): `get_app_info`, pickers nativos.
- **Python → JS** (streaming ao vivo de progresso + log de uma thread).
- **Conversor de áudio → RSM (PS2)** REAL: `core.convert_audio_to_rsm` (ffmpeg + rstm_build).
  Provado: gerou um `.rsm` de 100 KB. **v0.3: o card avulso "Converter um arquivo" foi FUNDIDO
  em "Adicionar música" (passo 2)** — a conversão já é automática ao adicionar; o "▶ Ouvir"
  (preview via `os.startfile`) migrou para lá (`preview_add_audio`). Removidos o hero e o item
  "Início" do rail; a 1ª aba agora é "Preparar Projeto". `convert_selected`/`pick_audio_file`
  do api foram removidos (código morto).
- **Validação de ISO (read-only)**: `core.inspect_iso()` monta a ISO via PowerShell
  (`Mount-DiskImage`), lê `SYSTEM.CNF`/BOOT2, valida se é o MC3 (`SLUS_213.55`) e se tem
  ASSETS.DAT/STREAMS.DAT, e desmonta no `finally`. **v0.3: o card avulso "Inspetor de ISO"
  foi FUNDIDO no "Preparar Projeto"** (botão "🔍 Validar ISO" + linha de resultado; api
  `validate_project_iso` emite `pp_validation`). Casava com o passo "2. validar ISO" do fluxo
  guiado do original. **Validado num MC3 real: a extração (Preparar tudo) rodou e populou o workspace.**
- **Recompilar DATs + Backup**: `core.Workspace` (dataclass de caminhos, base=própria pasta);
  `rebuild_streams_dat`/`rebuild_assets_dat`/`rebuild_all` (hash_build/dave reais, stdin `y\n`)
  + `publish_dat_to_game_files`; `create_backup_session`/`backup_file`/`restore_backup`
  (manifest com `existed:false` → restore APAGA o que foi criado). UI: card de recompilar +
  card de backup com **trava de confirmação** (checkbox) no restore. `_run` agora aceita `input_text`.
  Tools dave/hash_build/strtbl copiados para `tools/`. Falta real-run com game files de verdade.
- **Adicionar música (single)**: backend completo em `core.py` — `build_add_spec`/`add_songs`
  (= `_apply_add_specs`) + toda a cadeia de normalização (`normalize_game_text`,
  `coerce_asset_name`), leitura de tags (`source_guess`/`read_audio_tags` via ffprobe),
  tabela de strings (`song_entry`/`insert_entry_near_genre_block`/`decode_strings`/
  `compile_strings_json_to_strtbl`) e `update_playlist`. Constantes `GENRES`/`LANGUAGE_CONNECTORS`/
  `GENRE_DEFAULT_PLAYLISTS` portadas. Tela: áudio + auto-preenchimento por tags + gênero +
  playlists alvo + preview ao vivo + trava de confirmação (destrutivo, com backup automático).
  **v0.3: modo LOTE feito** — seletor "Uma música / Lote"; lote = `pick_batch_audio` (multi-picker
  + guess por faixa) → ficha editável por faixa → `add_batch` monta N specs e chama `add_songs`
  (backup label `add_music_batch`), reportando pulados. `_busy` é atômico (`_acquire_busy`).
  **Falta:** real-run com áudios de verdade.
- **Preparar Projeto (passos 3+4)**: extrai a ISO → workspace editável — o espelho do rebuild
  (mesmas ferramentas em modo `X`): `copy_iso_to_game_files` (mount+copy_tree), `decompile_workspace`
  (`dave X ASSETS.DAT`; `hash_build X STREAMS.DAT -nl MC3_PS2_Streams.lst -a mclub -th 45`; `strtbl dec`),
  `prepare_project_from_iso` (tudo), `workspace_status`. `MC3_PS2_Streams.lst` copiado p/ `tools/`.
  ⚠️ flag de algoritmo é **`mclub` minúsculo na extração** vs `MClub` no rebuild. Tela = 1º card
  (checklist + trava de reimportação). **Esta é a fundação que popula o workspace.**
  **v0.3+: botão "♻ Resetar projeto"** (rodapé do card, zona destrutiva própria) — `core.reset_workspace`
  apaga a extração (Arquivos da ISO, ASSETS/, STREAMS/, ASSETS.DAT/STREAMS.DAT raiz, mcstrings) via
  `_rmtree`/`_make_writable`, **preserva `backups/`** e a ISO já gerada; api `reset_project` (busy-lock,
  canal `pp_*`, zera `_selected_iso` → força escolher nova ISO); trava `pp-reset-confirm`. É o inverso
  de `prepare_project_from_iso` — para recomeçar do zero com outra ISO. `loadPrepareStatus` agora zera
  o rótulo da ISO quando não há ISO (senão o nome antigo grudava após o reset).
- **Remover música**: `core.list_songs` (enumera `STREAMS/Music/<gênero>/*.rsm` + contagem de
  playlist + tem-strings) + `remove_songs` (backup → `update_playlist(...,"remove")` → apaga
  entradas de string e recompila STRTBL → `unlink` do `.rsm` com `_make_writable`; ações áudio/
  playlists/strings independentes). Tela: lista com busca/seleção + opções + trava. Espelha `remove_music`.
- **Gerar ISO final**: `core.generate_final_iso` (preflight ImgBurn/game_files/SYSTEM.CNF →
  `rebuild_all` → `_build_imgburn_command` BUILD) + `find_imgburn` (PATH + Program Files). Tela:
  status + escolher destino (SAVE_DIALOG) + rótulo do volume + gerar. Espelha `_generate_final_iso_impl`.
  **Fase 3 fechada: o app faz o ciclo inteiro** (Preparar → Adicionar/Remover → Recompilar → Gerar ISO).
- **Início (painel + passo a passo)**: `core.overview(ws)` agrega detecção de programas
  (`find_ffmpeg`/`find_imgburn`/`find_foobar` + PS2 tools), contagem de músicas/playlists, último
  backup, e os 5 passos com estado (ok/pending/available/blocked) + target de navegação. Tela = 1º
  card (`card-inicio`, rail ativo) com tiles de status + lista de programas + passo a passo com
  botões "Ir para" (`goToCard`). É o "passo 1" do original.
- **Tema MIDNIGHT NEON (synthwave)**: reskin completo em `frontend/css/style.css` (reescrito
  do zero) — neon magenta/ciano/violeta, grade em perspectiva, glow, gradientes, contadores/tags
  com brilho. **Sem tocar `index.html` nem `app.js`**: TODAS as classes (estáticas + geradas por
  JS: `.stat-tile`/`.value`, `.prog-item`/`.prog-status`, `.step-item`/`.step-state`, `.song-item`/
  `.song-str`, `.pl-chip`, etc.) preservadas. Verificado servindo o front + injetando api falsa:
  boot popula tudo, estilos computados batem (body com radiais neon, text-fill transparente nos
  gradientes, `.btn.primary`/`.progress-fill` magenta→ciano), **zero erro de console**. Nasceu do
  `prototypes/01-midnight-neon.html`, escolhido pelo dono. ⚠️ `backdrop-filter: blur` foi TIRADO
  dos cards empilhados e stat-tiles (perf de uso diário — 7 cards grandes rolando); mantido só na
  topbar/rail (fixos). Não regredir isso.
- **Robustez + Configurações + Instalador + i18n (4 frentes, plano `virtual-crafting-mango`)**:
  - **Robustez:** `_run` ganhou `timeout` via watchdog daemon-thread (mata o proc no deadline →
    `ToolError`) — aplicado só em ffmpeg/ffprobe/mount (`FFMPEG_TIMEOUT`/`FFPROBE_TIMEOUT`/`MOUNT_TIMEOUT`);
    empacotadores (dave/hash_build/ImgBurn) NÃO têm timeout (contam com Cancelar). **Cancelar:**
    `_ACTIVE_PROCS`+`cancel_active()` no core; `Api.cancel_current()` + `_cancel_requested` (privado) +
    `_error_payload` (rotula `cancelled`); botão ✕ Cancelar em cada card de tarefa longa. **Erros
    visíveis:** `showBackendError` (pill `.error` + `#app-banner`) no boot-poll esgotado + catch do
    loadBackendInfo; `showLoadError` deixa o card vermelho quando um `get_*_status` falha.
  - **Configurações (tela nova `card-settings`, o botão morto do rail agora navega):** `core.options_path`/
    `load_options`/`save_options` (`options.json` na base, espelha o `project_state.json` do original);
    `Api.get_options`/`set_option`; `self._options` (privado) semeia última ISO/destino só se existir;
    `loadSettings()` aplica os padrões (rótulo do volume, toggles de remover) nas telas de trabalho.
    `pick_project_iso`/`pick_iso_output`/`reset_project` persistem.
  - **Instalar ferramentas (winget):** `core.install_tool(kind)` (ffmpeg/imgburn/foobar via
    `winget install --id … --silent`, cancelável, re-detecta; sem winget → `reason=no-winget`+url);
    `overview()` enriquece `programs` com `kind`/`installable`/`download_url`; `Api.install_tool`
    (canal `inst_*`) + `open_download_page`; botão **Instalar**/**Abrir página** por programa faltando
    no Início E em Configurações (`renderPrograms`).
  - **Multi-idioma pt/en/es:** `frontend/locales/{pt,en,es}.json` (chaves estáveis; pt=fonte);
    `core.load_translations` + `Api.get_i18n`/`set_language` (persiste em options, `self._i18n` seria
    privado); `app.js` `I18N`/`t(key)`/`applyStaticTranslations()` via `data-i18n`; seletor em
    Configurações troca **ao vivo** (rail + títulos de card + rótulos das Configurações).
    🔒 **O SELETOR ESTÁ OCULTO** — decisão do dono: não expor a troca de idioma enquanto a
    tradução não acabar. As tabelas cobrem **20 chaves** (trilho + rótulos de Configurações);
    todo o resto — status, erros, botões dentro dos cards e **todas** as mensagens do Python —
    é PT-BR fixo no código, então escolher "English" entregava um app ~95% em português.
    Liga/desliga por **`I18N_ENABLED` em `frontend/js/app.js`** (fonte única: ele revela o
    `#settings-language-block` e volta a respeitar o idioma salvo). Enquanto `false`, a tela
    renderiza sempre em pt-BR e a preferência salva em `options.json` fica **intacta**, para
    ninguém que já tivesse escolhido "English" ficar preso numa tela meio traduzida.
    A maquinaria inteira segue no lugar e testada — falta só traduzir a cauda longa.
- **Fase 4 — EMPACOTADO (PyInstaller + Inno) ✅**: `packaging/mc3.spec` (**onedir**, não onefile —
  tools/ tem 115 MB e o onefile re-extrairia tudo pro temp a cada abertura) + `packaging/mc3.iss`
  → `build_out/installer/MC3_Music_Manager_Setup.exe` (**90 MB**; instalado = 241 MB).
  - **Split de caminhos (o coração do port congelado):** `core._resource_root()` = `sys._MEIPASS`
    (`_internal/`, read-only: frontend + tools) vs `core._app_root()` = `Path(sys.executable).parent`
    (**gravável**, onde vive o workspace). `PROJECT_ROOT` = app_root; `TOOLS_*`/`LOCALES_DIR` =
    RESOURCE_ROOT. ⚠️ Se o workspace saísse do `_MEIPASS`, os ~19 GB de dados do jogo iriam pra um
    temp que o Windows apaga. Provado com sonda congelada: `workspace == pasta do exe -> True`.
  - **🔴 Bloqueador resolvido:** dave/hash_build/strtbl só existiam como `.py`, e `_tool_command`
    roda `.py` via `sys.executable` — que **no .exe é o próprio app** (`MC3.exe dave.py …` relançaria
    a GUI em loop). Fix: **compilar os 3 como `.exe`** (o `find_*` já preferia `.exe`) + guarda no
    `_tool_command` que levanta `ToolError` se achar `.py` estando `frozen`.
  - `main.py`: `INDEX` vem do RESOURCE_ROOT; `debug` só com `MC3_DEBUG=1` ou rodando do fonte.
  - **Inno**: per-user (`PrivilegesRequired=lowest`, `{autopf}`→LocalAppData\Programs) — Program
    Files NÃO serve porque o workspace fica ao lado do exe e precisa ser gravável; página de pasta
    ON (usuário escolhe drive grande); `[UninstallDelete]` só apaga `options.json` — **nunca** os
    dados/backups do jogo. Validado: install silencioso exit 0 → app abre → uninstall exit 0.
  - `.gitignore` criado: bloqueia `iso/`, `Arquivos da ISO/`, `ASSETS/`, `STREAMS/`, `*.DAT`,
    `mcstrings02.*`, `backups/`, `options.json`, `build_out/` (conteúdo do jogo NUNCA vaza).
  - ⚠️ **Falta `ffprobe.exe` no bundle** → num PC limpo o auto-preencher tags degrada pro palpite
    por nome de arquivo (não quebra). Embarcar custa +~85 MB; ou o usuário clica "Instalar FFmpeg".
- **Barra de título própria + ícone + gênero em massa + licenças**:
  - **Janela sem moldura:** `main.py` usa `frameless=True, easy_drag=False` (easy_drag ligado faria
    a UI INTEIRA virar alça de arrasto); a `.titlebar` no HTML opta pelo arrasto via a classe
    **`pywebview-drag-region`**. Botões min/max/fechar → `Api.window_minimize/window_toggle_maximize/
    window_close` (usam `self._bridge.window`; `_maximized` é privado — gotcha do js_api).
  - **🔴 Regressão achada e corrigida:** frameless faz o winforms setar `FormBorderStyle.None`
    (`winforms.py:269-271`) → cai o `WS_THICKFRAME` e **a janela não redimensiona mais**.
    Fix: `main.py::_restore_frameless_resize()` readiciona `WS_THICKFRAME|WS_MINIMIZEBOX` via ctypes
    no evento `window.events.shown` (WS_THICKFRAME **sem** WS_CAPTION = chrome próprio + resize, o
    padrão do VS Code). Verificado nos estilos Win32: `CAPTION=False, THICKFRAME=True`.
  - **Ícone:** `packaging/make_icon.py` (Pillow) desenha o selo neon (gradiente magenta→violeta→ciano,
    vinil, linhas de velocidade) em 1024px → `mc3.ico` com 7 tamanhos (16→256). Conferido ampliado:
    legível a 16px. Ligado no `mc3.spec` (`icon=`) e no `.iss` (`SetupIconFile`).
  - **Gênero em massa no lote:** barra `#batch-bulk` (só aparece com faixas) + `btn-batch-bulk-apply`
    seta todos os `.batch-genre` de uma vez — antes era dropdown por dropdown (19 faixas!).
  - **`LICENSES.md`**: FFmpeg (LGPL/GPL + link do fonte), ferramentas PS2 (créditos), pywebview,
    Python, WebView2, Inno. Deixa explícito que **nenhum conteúdo do jogo** é distribuído e que o
    projeto é não-oficial (© Rockstar). Vai instalado (`[Files]`) e é mostrado no assistente
    (`LicenseFile=`).
- **Relatório de erro num clique (mailto):** quando uma tarefa falha, `noteError(tela, erro,
  console-id)` guarda `{tela, erro, log do console}` e revela a barra `#error-report-bar`; botão
  **🐞 Enviar relatório** → `Api.send_error_report` monta o corpo **compacto** (erro no topo +
  ambiente + ferramentas + workspace, sem os caminhos longos p/ caber no limite ~2048 do mailto),
  abre o e-mail via `os.startfile("mailto:…")` (fallback `webbrowser.open`) pré-preenchido para
  `REPORT_EMAIL = 207797225+plmarcos@users.noreply.github.com`; **📋 Copiar** → `get_error_report` devolve o relatório
  COMPLETO (com caminhos) numa `<textarea>` + `execCommand('copy')`. Também há uma seção **Suporte**
  em Configurações. **Sem servidor, sem senha** — o usuário revisa e envia. Verificado: barra some no
  boot (fix do `[hidden]`), aparece no erro, captura o log do console, e chama as 2 APIs certas.
- `tests/test_core.py` = **73/73** verdes.
- **Auditoria do conversor (multi-agente)** corrigiu 3 achados no `core.py`: (1) rstm_build
  agora roda via `_tool_command` (o `.py` sem `.exe` executava sem interpretador → quebrava);
  (2) restaurado o fallback `.wav`-sem-ffmpeg → rstm direto; (3) `_make_writable()` limpa o
  bit somente-leitura antes de sobrescrever `.rsm`/`.play`/`.strtbl`/`.DAT` e no restore.

## 🗂️ Arquitetura

```
main.py                 # entrada pywebview: cria a janela + expõe a classe Api (js_api)
backend/
  core.py               # ⭐ serviço de domínio SEM UI (nada de tkinter/pywebview).
                        #    Progresso via CALLBACKS injetados. Já tem convert_audio_to_rsm().
  api.py                # ponte JS->Python: métodos chamáveis como pywebview.api.<m>()
  bridge.py             # ponte Python->JS: emit(event, payload) via window.evaluate_js
                        #    (substitui a fila queue.Queue + root.after do Tkinter)
  tasks.py              # run_in_background() = equivalente ao _start_background_task
frontend/
  index.html            # a UI
  css/style.css         # tema MIDNIGHT NEON (synthwave) — reskin completo, classes preservadas
  js/app.js             # controlador: chama a Api e ouve os eventos push (window.__mc3.on)
  locales/{pt,en,es}.json # tabelas de traducao (servidas por Python: file:// bloqueia fetch)
tools/                  # dave.py/hash_build.py/strtbl.py + MC3_PS2_Streams.lst
tools/wav to rsm/       # ffmpeg.exe + ffprobe.exe + rstm_build.exe + ps2str/MFAudio/encvag
packaging/              # mc3.spec (PyInstaller) + mc3.iss (Inno) + make_tools_bundle.py
tests/test_core.py
```

**Dois canais Python↔JS** (é o coração da arquitetura):
- **pull**: JS chama `await window.pywebview.api.metodo(args)` → método da classe `Api`.
- **push**: Python chama `bridge.emit("evento", payload)` → dispara `window.__mc3.on("evento", fn)` no JS.

## ▶️ Como rodar e testar

```bash
pip install -r requirements.txt      # pywebview>=6.2
python main.py                       # abre a janela
python -m pytest -q                  # roda os testes (87)
python -m py_compile backend/*.py main.py   # checagem rápida
```
Ambiente confirmado: **Python 3.14.4**, **pywebview 6.2.1**, `pythonw` no PATH =
`C:\Python314\pythonw.exe`, WebView2 (Edge Chromium) já presente no Windows 10/11.

## 🧭 Decisões JÁ TOMADAS (não re-litigar)

1. **Vai ser webview** — o dono escolheu, ciente do custo (uma análise fundamentada
   estimou 3-5 meses / 4-7 part-time e recomendou *contra* uma reescrita completa,
   sugerindo restyle Tkinter; o dono decidiu ir de webview mesmo assim, faseado).
2. **Stack = pywebview** (não Electron/Tauri — esses forçariam Python como subprocesso;
   não Eel/Flask+browser — os pickers precisam de caminho absoluto e um servidor
   localhost incomoda antivírus/firewall num Setup.exe distribuído).
3. **Backend-first + tela por tela.** Extrair a lógica reutilizável para `backend/core.py`
   (sem UI) e portar uma tela de cada vez, provando paridade.

## ⚠️ Armadilhas a BLINDAR (da análise cética — leve a sério)

- **Ordem dos passos destrutivos:** no Tkinter, `messagebox.askyesno` BLOQUEIA o loop e
  serializa passos destrutivos "de graça". Modais HTML são não-bloqueantes → toda
  confirmação destrutiva precisa de **trava (busy-lock)** contra duplo-clique/reentrância.
  (Já há um `_busy` no conversor; **generalizar** para add/remove/gerar-ISO.)
- **Drag-drop NÃO dá caminho absoluto** (segurança do navegador) → as ferramentas CLI
  precisam do caminho real → use **sempre** `window.create_file_dialog` (picker nativo).
- **Preview de áudio:** use `os.startfile` (player do SO, toca qualquer formato), NÃO
  `<audio>` do WebView2 (só codecs web → regressão de formato).
- **SmartScreen** zera reputação de `.exe` novo não-assinado; **WebView2** se auto-atualiza
  por baixo do app (testar após updates do Windows).
- **Manutenção dupla:** enquanto porta, o app Tkinter original segue sendo mantido.

## 🐛 Gotchas técnicos já descobertos

- **"Falha ao decodificar mcstrings02.strtbl" — preparar NÃO validava a ISO.** Relatado
  por um usuário do app instalado: `strtbl.py:198 read_str / AssertionError: String does
  not match its expected size`, a 91% do "Preparar tudo automaticamente".
  O `inspect_iso()` só era chamado pelo botão **opcional** "🔍 Validar ISO" — nada no
  caminho de preparação conferia o BOOT2. Dava para apontar o app para qualquer `.iso`,
  esperar a cópia de ~4 GB e receber um traceback do Python vindo de dentro da ferramenta.

  Assinaturas medidas (reproduzindo corrupções contra o `.strtbl` bom):

  | corrupção | onde quebra |
  |---|---|
  | truncado | `parse_strtbl:220` — *"Not a valid .STRTBL container"* |
  | caractere fora do BMP (emoji) no texto | `determine_hash:134` — *"Failed to determine the hash algorithm"* |
  | **bytes corrompidos no meio** | **`read_str:198` — *"String does not match its expected size"*** ← a do relato |

  Ou seja: **não** era truncamento nem emoji. O container é válido e as labels leem; o
  que não bate são os tamanhos das strings — compatível com **outra versão/região do
  jogo** (layout diferente da tabela) ou extração corrompida.

  **Corrigido**: `core.assert_supported_iso()` roda ANTES de qualquer cópia, em
  `prepare_project_from_iso` e `copy_iso_to_game_files` (com `verify=False` na chamada
  interna, para não montar a imagem duas vezes). Recusa **antes** de apagar a extração
  atual, e a mensagem diz o BOOT2 encontrado e o esperado. O relatório de erro passou a
  incluir a ISO — era o dado que mais faltava num relato remoto.

  ⚠️ **A causa raiz do relato original segue não confirmada** — sem acesso à máquina nem
  à ISO daquele usuário. O que foi fechado é a lacuna que deixava isso acontecer sem
  diagnóstico. Se reaparecer: peça o relatório de erro, que agora traz a ISO e o BOOT2.

- **🔇 MÚSICA MUDA NO JOGO — o `rstm_build` não entrega o formato do MC3.** Comparando
  os **135** `.rsm` de música que vieram do jogo com os gerados pelo app, a divisão foi
  limpa (135 × 11) em **quatro** campos:

  | | jogo (135/135) | rstm_build (11/11) |
  |---|---|---|
  | `0x08` sample rate | **32000** | 44100 |
  | `0x1C` loop start | **32** | 0 |
  | `0x24` | **0xFFFFFFFF** | 0 |
  | 1º frame dos dados | **zerado (init do SPU)** | removido |

  O `0x24` o `rstm_build` **nunca escreve** — não há uma linha sequer que toque nesse
  offset. O frame de init ele remove de propósito (`rstm_build.py:152`, *"RSMs don't
  have these"*): pode valer para o Bully, é falso para a música do MC3. O 44100 vinha
  do **nosso** `ffmpeg -ar`, não da ferramenta.

  **Corrigido em `core.conform_rsm_to_game()`**, chamada por `_publish_rsm` (e também
  no ramo que só copia um `.rsm`). Idempotente. A taxa virou `core.MUSIC_SAMPLE_RATE`.

  ⚠️ **Corrigir dentro do `rstm_build.py` não teria efeito nenhum**: `find_rstm_build()`
  dá prioridade ao `rstm_build.EXE`, que existe em `tools/wav to rsm/` — o `.py` nem roda.
  Qualquer conserto no pipeline de áudio tem de ser **pós-processamento no core**, ou
  então recompilar o `.exe`.

  ⚠️ Faixas adicionadas ANTES desta correção continuam quebradas: a conformidade não
  reamostra, então elas precisam ser **removidas e adicionadas de novo** a partir do
  áudio original.

- **`_run` devolve `(code, out)` — nunca descarte o `out`.** O ramo do ffmpeg fazia
  `code, _ = _run(...)` e logo abaixo usava `_tool_detail(out)`: `UnboundLocalError` em vez
  da mensagem. Pior, **"Cancelar" cai nesse mesmo ramo** (matar o processo → `code != 0`).
  Hoje as 10 chamadas propagam a saida real da ferramenta. Coberto por
  `ConversionFailureReporting`.
- **`coerce_asset_name` faz dois arquivos virarem um.** Ela tira `official`, `video`, `hq`,
  bitrates… entao `Artista - Musica (Official Video).mp3` e `Artista - Musica [HQ].mp3`
  reivindicam o **mesmo** `.rsm`. `existing_spec_targets` so olha o DISCO, entao o lote
  rodava "com sucesso" e a 2ª faixa sobrescrevia a 1ª. Hoje `find_duplicate_spec_targets`
  barra antes de qualquer escrita.
- **`hash_build` grava um `.lst` irmao derivado do NOME de saida** (`build_hash`, fim da
  funcao): `STREAMS.DAT` → `STREAMS.LST`. Por isso a recompilacao constroi num scratch
  **preservando o nome do arquivo** — um `STREAMS.DAT.tmp` produziria `STREAMS.DAT.LST` e
  deixaria o `STREAMS.LST` desatualizado.
- **Recompilar DAT e atomico agora.** O `open(output, "wb")` do dave/hash_build **trunca na
  hora**, entao cancelar no meio deixava um DAT corrompido na raiz. Constroi-se em
  `.mc3_rebuild/` e publica-se com `os.replace()` so no sucesso. Custo: 2 copias do DAT em
  disco durante a operacao.
- **O bundle de ferramentas e montado a mao e ja divergiu.** O `mc3.spec` empacota
  `build_out/tools_bundle`, **nao** `tools/`. O `ffprobe.exe` entrou em `tools/` e nunca
  chegou ao bundle — o app instalado ficou sem leitura de tags, em silencio. Use sempre
  `python packaging/make_tools_bundle.py`; o `.spec` hoje **falha o build** se faltar algo.
- **O que vem da UI vira caminho.** `genre`/`asset_name`/`playlists` do frontend terminam
  num `unlink()`. Passam por `validate_genre` / `validate_asset_name` / `resolve_playlist`.

- **Corrida do `pywebviewready`:** se o evento dispara antes do JS registrar o listener,
  o boot não roda (UI trava em "verificando ferramentas…"). O guard antigo (evento OU
  `window.pywebview.api` no check) NÃO bastava quando o pywebview serve via http (ex.:
  `http://127.0.0.1:PORT`, Python da MS Store). Solução robusta em `app.js` (`bootWhenReady`):
  tenta já + escuta o evento + **faz polling de 100ms (até 10s)** até `window.pywebview.api.get_app_info`
  existir, então boota UMA vez. Verificado com injeção tardia da API. Não regredir isso.
- **Cache do WebView2:** pode servir HTML/JS antigo entre relançamentos; se uma edição
  "não aparecer", é cache — relançar/limpar.
- **⚠️ js_api SÓ pode ter atributos privados (`_`-prefixados) além dos métodos.** No
  `pywebviewready` o pywebview roda `get_functions()` (util.py) que **percorre RECURSIVAMENTE
  todo atributo público não-chamável** do objeto `Api` que tenha `__module__`, para expor
  sub-APIs. Se a `Api` tem `self.bridge`/`self.workspace` públicos, ele entra no grafo da
  janela/Workspace e cai em **recursão infinita** (`RecursionError: ...Empty.Empty.Empty...`),
  travando o boot em "conectando…". Fix: `self._bridge`/`self._workspace` (privados). Provado:
  após o fix expõe só os 20 métodos. **Nunca adicionar atributo público não-método na `Api`.**
- **`create_file_dialog` a partir do handler js_api funciona** (a preocupação de threading
  da análise NÃO se materializou no pywebview 6.2.1).
- **Arquivos extraídos de ISO são somente-leitura:** o jogo/ISO grava `.rsm`/`.play`/`.strtbl`/
  `.DAT` com o bit read-only. Qualquer escrita que os SOBRESCREVA precisa de `_make_writable()`
  antes (chmod +W), senão dá `PermissionError` (e no Windows nem `unlink` funciona). Isso vale
  para as próximas telas destrutivas (Remover, Gerar ISO) — o original faz isso via `_make_path_writable`.
- **Conversão de áudio + `WinError 32` (achado em real-run do lote):** o scratch da conversão
  (`TemporaryDirectory`) NÃO pode ficar dentro de `STREAMS/Music/<gênero>/` (o porte fazia
  `dir=output.parent`) — (1) essa pasta é EMPACOTADA no STREAMS.DAT, então scratch que sobra vira
  lixo no jogo; (2) a limpeza do `with` estoura `[WinError 32] arquivo já está sendo usado` quando
  o Defender/indexador segura o `.wav` recém-escrito, **abortando o lote inteiro** (mesmo com o
  `.rsm` já pronto). Fix (espelha o original): scratch da conversão vai pro `%TEMP%` do SO +
  `ignore_cleanup_errors=True`; as **4** `TemporaryDirectory` (conversão + 3 de strtbl em
  `base_path`) agora têm `ignore_cleanup_errors=True`; e `sweep_stale_scratch(ws)` roda antes do
  `hash_build` (dentro de `rebuild_streams_dat`) apagando `tmp*`/`mc3_rsm_*` obsoletos de
  STREAMS/base → auto-cura o lixo da run que falhou. `_run` já faz `proc.wait()`, então o lock é
  externo (antivírus), não subprocesso pendurado. Não regredir: temp de conversão fora dos packed.
- **🔴 A música NÃO aparece no jogo se não estiver na RACE playlist do gênero (achado em real-run):**
  não basta `.rsm` + strings + as `<cidade>.play` (cruise). O original tem `_playlist_targets_for_genre`
  (~:7815) que **EXPANDE** a seleção: cada `<cidade>.play` escolhida puxa junto a race playlist do
  gênero **na mesma pasta** (`atlanta.play` → + `techno_race_music.play`); playlists não-cidade só
  entram se casarem com o gênero. O porte usava a seleção **literal** → com o preset "Só cidades" as
  músicas caíam só no cruise e ficavam **invisíveis**. Portado em `core.playlist_targets_for_genre` +
  `_playlist_path_matches_genre`, aplicado nos DOIS workers (`_add_worker`/`_add_batch_worker`) — no
  lote é **por-spec**, pois cada faixa tem seu gênero (e faixa sem playlist compatível é pulada, como
  no original). **Prova nos dados reais do jogo:** as 113 músicas de fábrica estão TODAS em
  `<cidade>.play` + race do gênero + `raceeditor.play` = **12 playlists** (4 cidades); as 10 adicionadas
  estavam em 4. ⚠️ `frontend.play` = música do **MENU**: contém faixas HipHop, então a heurística "já
  contém o gênero" o arrastava para adds de HipHop — mas **nenhuma** música de fábrica está lá →
  excluído via `PLAYLIST_CONTENT_MATCH_EXCLUDE` (desvio consciente do original, que poluía o menu).
- **`rstm_build` grava os intermediários NA PASTA DE SAÍDA (achado em real-run de outro usuário):**
  `rstm_build.py:59-61` faz `base_path = os.path.split(output)[0]` e põe `tmp_*.ads`/`tmp_*.wav` ali;
  `:122` só apaga **no sucesso** → se o `ps2str` falha, o lixo **fica** dentro de
  `STREAMS/Music/<gênero>/`, que é empacotada no STREAMS.DAT. Fix: `convert_audio_to_rsm` agora
  manda o rstm_build gravar num **scratch em `%TEMP%`** e só move o `.rsm` pronto pro lugar
  (`_publish_rsm`, espelha o que o original faz no preview, :8648). Também tira a permissão/AV da
  pasta do jogo do caminho da conversão. ⚠️ Não aponte o rstm_build direto pra pasta do jogo.
- **Erro de ferramenta precisa carregar o motivo:** `raise ToolError(f"... (codigo {code})")` jogava
  fora a saída capturada — o usuário via só "rstm_build falhou (codigo 1)" e o motivo real (a
  reclamação do ps2str) ficava enterrado no console. `_tool_detail(out)` extrai a linha de erro e
  entra na mensagem. **Não regredir: sempre passar a saída do `_run` pro ToolError.**
- **O aviso "Found the data section at 0x74" NÃO é o erro:** é `rstm_build.py:90-98` avisando que o
  WAV do ffmpeg tem chunks de metadados (LIST/INFO/ISFT) e que ele vai **reconstruir** o arquivo —
  e ele reconstrói com sucesso. Reproduzido: WAV com data em 0xF4 converte igual (exit 0). Não
  perca tempo "consertando" o cabeçalho do ffmpeg; nem espaços no caminho (testado: OK).
- **`file://` bloqueia `fetch` de JSON local (i18n):** o pywebview carrega `url=str(INDEX)` (file://),
  e o WebView2/Chromium bloqueia `fetch('locales/pt.json')` (CORS "only http/https"). Por isso as
  traduções são servidas pelo **Python** (`Api.get_i18n` → `core.load_translations` lê os 3 JSON do
  disco), não por fetch no JS. Vale para qualquer recurso local novo: sirva pelo Python, não `fetch`.
- **Timeout NUNCA nos empacotadores:** `_run(timeout=…)` só em ffmpeg/ffprobe/mount. dave/hash_build/
  ImgBurn e o `winget install` rodam SEM timeout (podem levar minutos legitimamente) — para abortar,
  use Cancelar (`cancel_active()` mata o proc → `ToolError` → `on_error` solta o busy-lock). O
  cancelar NÃO mexe no `_busy` direto. Cancelar um rebuild deixa o `base/*.DAT` parcial, mas o
  `Arquivos da ISO/*.DAT` só é republicado no sucesso (jogo não corrompe; há backup).
- **`_run` decodifica em UTF-8 com `errors='replace'` (achado em real-run do lote):** `text=True`
  sozinho usa o locale do Windows (cp1252, estrito) e um byte solto na saída de uma ferramenta (o
  banner de build do ffmpeg tem `0x8d`) estoura `UnicodeDecodeError` NO MEIO do stream `for line in
  proc.stdout`, abortando a tarefa. Fix: `encoding='utf-8', errors='replace'` no `Popen` do `_run`
  (o `read_audio_tags` já fazia). Não regredir.
- **`[hidden]` vs CSS de autor (bug do "banner fantasma"):** o atributo `hidden` só vale
  `[hidden]{display:none}` na folha do **navegador**, e QUALQUER regra de autor com `display`
  (ex.: `.danger-zone{display:flex}`) **ganha dela** → o elemento fica visível mesmo com
  `hidden=true`. Foi o que fez o banner "Não foi possível falar com o backend" (`#app-banner`) e a
  trava de reimportação (`#pp-danger`) aparecerem **SEMPRE, desde o load**, sem nenhuma falha real
  de backend (o probe provou o backend 100% são: 35 métodos expostos, todos os `get_*` OK).
  Fix: **`[hidden] { display: none !important; }`** global no topo do `style.css`.
  ⚠️ Testar `hidden` pela PROPRIEDADE (`el.hidden`) **NÃO detecta** isso — foi o que deixou passar
  nos meus testes. Verifique `getComputedStyle(el).display` ou `el.offsetHeight`.
- **Boot-poll não pode ser agressivo demais:** o watchdog que mostra o banner "sem backend" espera
  **~30s** e CONTINUA tentando (o boot bem-sucedido esconde o banner) — antes desistia em 10s e
  grudava um alarme falso num boot frio do WebView2 / máquina ocupada. `app.js` (`__bootPoll`).

## 📋 Próximos passos

Ver **`ROADMAP.md`** (fonte da verdade). Resumo:
- **Fase 1** — portar o resto da lógica p/ `core.py`: `dave`/`hash_build`/`strtbl`,
  montar/inspecionar ISO, backup, recompilar DATs, gerar ISO final.
- **Fase 3** — próxima tela sugerida: **inspetor/validação de ISO** (read-only, baixo
  risco) e depois **Adicionar música** (núcleo de uso diário; exige a trava de confirmação).
- **Fase 4** — empacotar com PyInstaller (+ Inno), reaproveitando o pipeline do original.

## 🔗 Projeto original (referência para portar a lógica)

- Local: `MC3 MUSIC TUT\mc3_music_manager.py`, ao lado desta pasta.
  ⚠️ O caminho `F:\Importantes\...` que este arquivo citava **nao existe mais** — a
  arvore migrou de `F:` para `E:`. Confira onde o original esta antes de consultar.
  (god-class Tkinter ~9600 linhas; a lógica a portar vive nos métodos `_impl`/worker
  que recebem dados puros e levantam `RuntimeError` — ex.: `_convert_to_rsm`,
  `_apply_add_specs`, `_rebuild_*_dat_impl`, `_mount_iso_drive`, backup helpers).
- As 4 ferramentas PS2 (`dave.py`, `hash_build.py`, `strtbl.py`, `wav to rsm/rstm_build.py`)
  são CLIs puras (zero Tkinter) — portáveis como estão. O `tools/wav to rsm/` daqui já é cópia.
- Ao portar, replique o comportamento e **não** invente caminhos: os caminhos reais do
  workspace são `ASSETS/tune/audio/playlist/city/<cidade>/music/*.play` (pasta `audio`,
  **sem** acento — cuidado, isso já causou bug no original).

---
*Atualize este arquivo ao concluir cada fase/tela, para a próxima sessão continuar limpo.*
