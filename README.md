# MC3 Music Manager — Web-view edition (protótipo)

Porte da interface do MC3 Music Manager para **HTML/CSS/JS** dentro de uma
janela **pywebview**, mantendo o **Python** como backend (reaproveitando os
tools PS2 `dave.py` / `hash_build.py` / `strtbl.py` / `rstm_build.py`, ffmpeg,
ISO, backup).

> O projeto original em Tkinter **não é modificado** — este porte vive só nesta
> pasta. Migração faseada: primeiro o esqueleto + a arquitetura, depois cada
> funcionalidade é portada uma a uma, provando paridade antes de trocar.

## Rodar

```bash
pip install -r requirements.txt
python main.py
```

## Ferramentas externas (nao versionadas)

O repositorio **nao** guarda `ffmpeg.exe` nem `ffprobe.exe` (189 MB somados): sao
redistribuiveis de terceiros, e a build exata esta creditada em `LICENSES.md`. Numa
copia nova do fonte, reponha os dois em `tools/wav to rsm/`:

- Baixe uma build Windows em <https://www.gyan.dev/ffmpeg/builds/> (a mesma origem
  citada no `LICENSES.md`) e copie `ffmpeg.exe` e `ffprobe.exe` para
  `tools/wav to rsm/`.
- Sem `ffmpeg` o app **nao converte** MP3/FLAC/OGG (so `.wav`/`.ads`/`.ss2`/`.rsm`
  passam direto); sem `ffprobe` ele **nao le as tags**, e o auto-preenchimento de
  titulo/artista cai para adivinhacao pelo nome do arquivo.

As ferramentas PS2 da comunidade (`dave.py`, `hash_build.py`, `strtbl.py`,
`rstm_build.exe`) **estao** versionadas: sao pequenas e dificeis de reobter.
Junto com elas vao `ps2str.exe` e `encvag.dll`, que **nao** sao da comunidade:
sao componentes do SDK do PlayStation 2, com copyright da Sony Computer
Entertainment. O `rstm_build` depende dos dois para converter WAV. Veja
`LICENSES.md`.

Confira o que o pacote vai levar com:

```bash
python packaging/make_tools_bundle.py --check
```

## Estrutura

```
main.py                 # entrada: cria a janela pywebview + expõe a Api
backend/
  api.py                # ponte JS -> Python (pywebview.api.*)
  bridge.py             # ponte Python -> JS (progresso/log/status ao vivo)
  tasks.py              # tarefas em background (equivalente a _start_background_task)
frontend/
  index.html            # a UI
  css/style.css         # tema (a liberdade visual que o Tkinter não dá)
  js/app.js             # controlador: chama a Api e ouve os eventos push
```

## O que este esqueleto já prova

- Janela nativa (Edge WebView2), sem empacotar Chromium.
- **JS → Python**: `get_app_info`, `pick_folder` (diálogo **nativo** do Windows).
- **Python → JS**: tarefa em thread transmitindo **progresso + log ao vivo**
  (a parte mais arriscada da migração — o modelo de fila do Tkinter virando IPC).

## Próximas fases (planejado)

1. Extrair a lógica de domínio do `mc3_music_manager.py` para módulos backend
   reutilizáveis (sem UI).
2. Portar tela a tela: Adicionar música → Remover → Preparar/Gerar ISO.
3. i18n em JSON (pt-BR/en-US/es-ES) reaproveitando as traduções existentes.
4. Empacotar com PyInstaller (o WebView2 já existe no Windows 10/11).
