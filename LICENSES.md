# Avisos de licença e créditos — MC3 Music Manager (edição Web-view)

Este programa é distribuído junto com software de terceiros. Abaixo estão os
avisos exigidos por cada um. **Nenhum conteúdo do jogo acompanha este programa.**

---

## ⚠️ Conteúdo do jogo (leia primeiro)

**O MC3 Music Manager NÃO inclui, NÃO distribui e NÃO fornece nenhum arquivo do
jogo _Midnight Club 3: DUB Edition Remix_.**

*Midnight Club 3: DUB Edition Remix* é © Rockstar Games / Take-Two Interactive.
Este é um projeto **não-oficial**, sem qualquer vínculo, patrocínio ou aprovação
da Rockstar Games ou da Take-Two.

Para usar o programa você precisa da **sua própria cópia legítima do jogo**. Todo
o conteúdo (ASSETS, STREAMS, playlists, strings, ISO) é gerado **a partir do disco
do próprio usuário**, na máquina dele, e nunca sai dela.

Você é responsável por respeitar as leis de direito autoral do seu país quanto à
cópia de segurança do jogo e às músicas que adicionar.

---

## FFmpeg / FFprobe

- **Site:** https://ffmpeg.org
- **Licença:** LGPL v2.1 ou posterior (algumas builds são GPL v2+, dependendo dos
  componentes compilados).
- **Uso:** converte o áudio de entrada (MP3/FLAC/OGG/M4A/…) para WAV normalizado
  (44100 Hz, estéreo, 16-bit) antes da conversão para RSM; o FFprobe lê as tags.
- **Build redistribuída:** binários Windows de https://www.gyan.dev/ffmpeg/builds/

> Aviso exigido: "This software uses libraries from the FFmpeg project under the
> LGPLv2.1." O código-fonte do FFmpeg está disponível em https://ffmpeg.org/download.html
> e, para a build específica aqui redistribuída, em https://www.gyan.dev/ffmpeg/builds/

O FFmpeg é usado como **executável separado**, invocado por linha de comando —
não há linkagem estática com o código deste programa.

---

## Ferramentas de ROM-hacking do PS2

Ferramentas da comunidade de modding, redistribuídas para que o fluxo funcione
sem download manual. Todos os créditos aos autores originais.

| Ferramenta | Função |
|---|---|
| `dave` | empacota/desempacota `ASSETS.DAT` |
| `hash_build` | empacota/desempacota `STREAMS.DAT` (algoritmo de hash `MClub`) |
| `strtbl` | decodifica/codifica `mcstrings02.strtbl` |
| `rstm_build` | converte WAV → RSM (áudio do PS2) — por Edness, v1.4.2 |

Se você é autor de alguma destas ferramentas e quer que o aviso de licença seja
ajustado, corrigido ou que ela seja removida deste pacote, abra uma issue em
<https://github.com/plmarcos/mc3-music-manager/issues> — será atendido prontamente.

---

## ⚠️ Componentes do SDK do PlayStation 2 (Sony)

Dois arquivos que acompanham o programa **não são** da comunidade de modding: são
componentes do SDK oficial do PlayStation 2. O aviso abaixo foi lido do próprio
binário.

| Arquivo | Aviso embutido | Função |
|---|---|---|
| `ps2str.exe` | Copyright (C) 1999, 2000 Sony Computer Entertainment Inc. | converte WAV → ADS/SS2; é quem o `rstm_build` chama |
| `encvag.dll` | Copyright (C) 1998-1999 Sony Computer Entertainment Inc. | codificador VAG que o `ps2str` carrega — sem ele o `ps2str` nem inicia |

São redistribuídos **sem autorização da Sony**, apenas porque o `rstm_build` depende
deles e não se conhece substituto livre que produza o mesmo resultado. Nenhum vínculo
com a Sony Computer Entertainment é sugerido ou implicado. Se a Sony, ou quem a
represente, pedir a retirada, ela será feita imediatamente — abra uma issue em
<https://github.com/plmarcos/mc3-music-manager/issues>.

---

## pywebview

- **Site:** https://pywebview.flowrl.com
- **Licença:** BSD 3-Clause
- **Uso:** cria a janela nativa e a ponte Python ↔ JavaScript.

## Python

- **Site:** https://www.python.org
- **Licença:** PSF License Agreement
- **Uso:** o interpretador é embarcado pelo PyInstaller no executável.

## PyInstaller (ferramenta de build)

- **Site:** https://pyinstaller.org
- **Licença:** GPL v2 com exceção especial que **permite** distribuir os
  executáveis gerados sob a licença que você quiser.

## Microsoft Edge WebView2

- **Uso:** renderiza a interface. É um componente **do sistema operacional**
  (já presente no Windows 10/11) — não é redistribuído por este pacote.
- Sujeito aos termos da Microsoft: https://developer.microsoft.com/microsoft-edge/webview2/

## Inno Setup (ferramenta de build)

- **Site:** https://jrsoftware.org/isinfo.php
- **Licença:** Inno Setup License (permite criar instaladores comerciais e livres).

---

## Este programa

O MC3 Music Manager é um projeto pessoal, sem fins lucrativos e **não-oficial**,
feito para quem quer trocar a trilha sonora da própria cópia do jogo.

O **código escrito para este projeto** (`main.py`, `backend/`, `frontend/`,
`packaging/`, `tests/` e a documentação) está sob a licença **MIT** — veja o arquivo
`LICENSE`. A MIT vale só para esse código: **não** cobre os binários de terceiros
listados acima nem qualquer conteúdo do jogo.

**Sem garantia.** O programa mexe em arquivos do seu jogo. Ele faz **backup
automático** antes de qualquer operação destrutiva, mas o uso é por sua conta e
risco. Sempre mantenha uma cópia da sua ISO original.
