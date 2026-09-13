# Roadmap — porte web-view (faseado, sem quebrar o app atual)

Decisão do dono (2026-07-12): converter a UI para **web (HTML/CSS/JS) com backend
Python**, stack **pywebview** (Edge WebView2). O app Tkinter original em
`..\MC3 MUSIC TUT` **não é tocado** — este porte vive só nesta pasta.

A análise fundamentada (6 agentes + 1 cético) recomendou **fazer faseado, nunca
big-bang**, porque o risco #1 é regredir o fluxo diário que funciona. Este é o
plano, na ordem que reduz risco:

## Fase 0 — Rede de segurança (testes de caracterização)
- [~] Testes de paridade em torno do fluxo real (add/remove/preparar/gerar ISO),
      no nível `_impl`/worker (dados de entrada → arquivos/RuntimeError de saída).
      **Feito** para os caminhos de FALHA (conversão, recompilação, validação de
      entrada, backup em falha parcial). **Falta** o caminho feliz de `add_songs`
      ponta a ponta e o round-trip de strings (`decode_strings` →
      `compile_strings_json_to_strtbl`), que ainda não têm nenhum teste.
- [x] Harness de teste do novo backend (`tests/test_core.py`) — **87 testes**.
- [x] **Repositório git inicializado** — antes não havia nenhum: 5,6k linhas sem
      histórico. O `.gitignore` mantém fora os 21 GB de dados do jogo, o ffmpeg/ffprobe
      (189 MB, redistribuíveis) e os transcripts de sessão.

## Fase 1 — Backend sem UI (o passo-chave de de-risking)
Extrair a lógica reutilizável do god-class Tkinter para módulos SEM `import tkinter`,
com **callbacks de progresso injetados** (nunca importar UI).
- [x] `backend/core.py` — conversão áudio→RSM (ffmpeg + rstm_build), **provado**.
- [x] **build** dos DATs: `rebuild_streams_dat` (hash_build) + `rebuild_assets_dat` (dave)
      + `rebuild_all` + `publish_dat_to_game_files` — **feito** (falta o real-run com game files).
- [ ] **read/extract**: `read_dave`/`read_hash` + `strtbl` (dec/enc) — expor como funções.
- [x] inspecionar ISO (`inspect_iso`), backup/restore (`create_backup_session`/`backup_file`/
      `restore_backup`) — **feito**. Falta: gerar ISO final (ImgBurn).
- [x] mover os tools para `tools/`: dave/hash_build/strtbl copiados (rstm já veio).

## Fase 2 — Estado + confirmações seguras
- [~] Objeto de estado: `core.Workspace` (dataclass) já centraliza os caminhos do workspace.
- [~] Confirmação destrutiva: o **restore** usa uma trava por checkbox (não-bloqueante) +
      `_busy` compartilhado no backend. Padrão pronto para reusar em add/remove/gerar ISO.
- [x] `_busy` **atômico** com `threading.Lock` (`_acquire_busy`/`_release_busy`) — TOCTOU
      eliminado, testado (20/20). Todo acesso a `_busy` passa pelo lock.

## Fase 3 — PORTAR TELA A TELA (com decisão de seguir ou não)
Migrar uma aba por vez, rodando em paralelo ao Tkinter até provar paridade:
- [x] **Preparar Projeto (passos 3+4)** — **feito**: extrai a ISO → workspace editável
      (`copy_iso_to_game_files` + `decompile_workspace`: `dave X`, `hash_build X -nl .lst -a mclub -th 45`,
      `strtbl dec`) + `prepare_project_from_iso` (tudo automático) + `workspace_status`. Tela com
      checklist de status e trava de reimportação. Copiado `MC3_PS2_Streams.lst`. É a FUNDAÇÃO que
      popula o workspace (destrava as telas de editar). Falta real-run com uma ISO de verdade.
- [x] Conversor de áudio → RSM (tela real, não-destrutiva) — **feito, v0.2**.
- [x] Inspetor/validação de ISO (read-only, baixo risco) — **feito**: `core.inspect_iso()`
      monta via PowerShell (Mount-DiskImage), lê SYSTEM.CNF/BOOT2, valida jogo + DATs,
      desmonta no `finally`. Falta validar num Midnight Club 3 real (rodar `python main.py`).
- [x] Recompilar DATs + Backup — **feito**: tela com STREAMS/ASSETS/tudo + criar/restaurar
      backup (trava de confirmação). Backend testado (19/19); falta real-run com game files.
- [x] Adicionar música (o núcleo de uso diário) — **single + LOTE feitos**: backend completo
      (`build_add_spec`/`add_songs` recebe lista + normalização/tags/strings/playlists) +
      tela com **seletor de modo** (Uma música / Lote): single (auto-preenchimento por tags,
      preview ao vivo) e lote (multi-picker → `pick_batch_audio`/`add_batch`, ficha por faixa
      editável, pulados reportados). Conversão automática embutida. Falta só real-run com áudios.
- [x] Remover música — **feito**: `core.list_songs` (enumera STREAMS/Music + contagem de
      playlist + tem-strings) + `remove_songs` (backup → remove de playlists/strings → apaga
      `.rsm` com `_make_writable`; 3 ações opcionais). Tela: lista com busca + seleção +
      opções áudio/playlists/strings + trava de confirmação. 46/46 testes.
- [x] Gerar ISO **final** (ImgBurn) — **feito**: `core.generate_final_iso` (preflight ImgBurn/
      game_files/SYSTEM.CNF → `rebuild_all` → comando ImgBurn BUILD) + `find_imgburn`. Tela:
      status + escolher destino (save dialog) + rótulo do volume + gerar. **Ciclo completo!**
- [ ] Configurações + i18n (mover APP_UI_TEXT para JSON pt/en/es).

- [x] **Início (painel + passo a passo)** — **feito**: `core.overview` agrega programas
      (ffmpeg/ImgBurn/PS2 tools/foobar), contagem de músicas/playlists, backup e os 5 passos
      com estado (ok/pending/available/blocked). Tela = 1º card com tiles + lista de programas
      + passo a passo com botões "Ir para" (navega pro card).

**Real-run já aconteceu**: `backups/` tem 4 sessões `add_music_batch` de jul/2026 e
`iso/MC3_mod.iso` (3,3 GB) foi gerada — ou seja, o ciclo completo rodou com áudio e ISO
de verdade, não só com os testes. Os "falta real-run" acima estão desatualizados.

**A Fase 3 está fechada — o app faz o loop inteiro** (Início guia: Preparar → Adicionar/Remover
→ Recompilar → Gerar ISO). Falta só: real-run com áudios de verdade; Configurações + i18n;
e a Fase 4 (empacotar PyInstaller + Inno). 51/51 testes.

## Fase 4 — Empacotar  ✅ FEITA
- [x] PyInstaller (WebView2 já existe no Win 10/11; sem empacotar Chromium) —
      `packaging/mc3.spec`, onedir, saída em `build_out/dist/`.
- [x] Inno Setup — `packaging/mc3.iss`, per-user (sem admin), saída em
      `build_out/installer/MC3_Music_Manager_Setup.exe`.
- [x] `packaging/make_tools_bundle.py` monta o `tools_bundle` que o spec empacota, e o
      spec **falha o build** se faltar item obrigatório. (Sem isso o `ffprobe.exe` ficou
      de fora do pacote e a leitura de tags morreu em silêncio no app instalado.)

Build completo:
```bash
python packaging/make_tools_bundle.py
python -m PyInstaller packaging/mc3.spec --noconfirm --distpath build_out/dist --workpath build_out/work
ISCC.exe packaging\mc3.iss      # Inno Setup 6 — precisa estar instalado
```

## Armadilhas a blindar (da análise cética) — NÃO esquecer
- Ordem dos passos destrutivos: modais HTML precisam de trava anti-reentrância.
- Drag-drop NÃO dá caminho absoluto → usar sempre picker nativo (feito).
- Preview de áudio: `os.startfile` (player do SO), não `<audio>` web (feito).
- SmartScreen zera reputação de `.exe` novo não-assinado (considerar assinatura).
- WebView2 é dependência que se atualiza sozinha (testar após updates).
- Manutenção dupla: enquanto porta, o app Tkinter atual segue sendo mantido.
