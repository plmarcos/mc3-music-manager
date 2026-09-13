# Protótipos de UI/UX — MC3 Music Manager (Web-view)

Três direções de design **bonitas e funcionais** para o app. Cada uma foi aplicada a
uma tela diferente para você comparar como cada estética se comporta num contexto real
(painel, formulário, tabela de dados).

> **O que são:** mockups independentes em HTML/CSS/JS puro — **sem backend**, sem
> pywebview. Servem só para escolher a direção visual. A lógica de verdade continua em
> `../frontend/`. Nada aqui toca o app real nem o projeto Tkinter original.

## Como abrir

Abra **`index.html`** no navegador (galeria com preview ao vivo dos três) ou abra cada
arquivo diretamente. São interativos — navegação, seleção, filtros, preview e barras de
progresso funcionam de mentirinha para dar a sensação real.

---

## As três direções

### 01 · Midnight Neon — *tela: Início* 🌃
`01-midnight-neon.html`

Synthwave que abraça a estética noturna do *Midnight Club*: **neon, brilho (glow),
grade em perspectiva e contadores animados**. É a mais ousada/"gamer".

- **Cara:** rosa/roxo/ciano sobre preto, títulos com gradiente, cards que brilham.
- **Pontos fortes:** personalidade forte, combina com o tema do jogo, "uau" imediato.
- **Cuidado:** brilho demais cansa em uso diário longo; precisa de disciplina para não
  virar poluição visual nas telas densas (tabelas).
- **Melhor para:** telas-vitrine (Início, Gerar ISO) onde impressiona sem atrapalhar.

### 02 · Studio Glass — *tela: Adicionar música* 🧊
`02-studio-glass.html`

**Glassmorphism** moderno: vidro fosco (`backdrop-filter: blur`), gradientes suaves,
bastante respiro e foco no conteúdo. Profissional e elegante.

- **Cara:** painéis translúcidos, roxo/azul suave, sombras leves, cantos arredondados.
- **Pontos fortes:** parece app premium atual; ótimo para formulários (preview ao vivo,
  chips de playlist); equilíbrio entre bonito e usável.
- **Cuidado:** `blur` pesa em máquinas fracas; contraste do texto sobre vidro exige
  atenção para acessibilidade.
- **Melhor para:** telas de trabalho com formulário (Adicionar, Preparar, Recompilar).

### 03 · Rack Minimal — *tela: Remover / biblioteca* ▦
`03-rack-minimal.html`

Estilo **ferramenta-pro** (Linear / Vercel / Raycast): denso, alto contraste, cromo
mínimo, tipografia afiada e **um único acento** (verde-menta). Feito para dados.

- **Cara:** quase-preto, uma cor de destaque, monoespaçada nas colunas técnicas, tabela
  densa com cabeçalho fixo, filtro + busca + seleção múltipla.
- **Pontos fortes:** lê muita informação sem cansar; rápido; sério; escala bem para
  listas grandes de música.
- **Cuidado:** pode parecer "sem graça" para quem espera algo temático do jogo.
- **Melhor para:** telas de dados (Remover, biblioteca, logs).

---

## Recomendação

Não precisa escolher só uma para tudo. A combinação mais forte costuma ser:

> **Base = Studio Glass** (trabalho diário, formulários) **+ tabelas no espírito Rack
> Minimal** (densidade nos dados) **+ toques de Midnight Neon** nas telas-vitrine
> (Início / Gerar ISO), para dar a identidade do *Midnight Club* sem cansar.

Me diga qual rumo você prefere (ou a combinação) e eu aplico o tema no app de verdade,
tela por tela, sem quebrar o que já funciona.

---

## Arquivos

| Arquivo | Direção | Tela demonstrada |
|---|---|---|
| `index.html` | Galeria (hub com previews ao vivo) | — |
| `01-midnight-neon.html` | Midnight Neon (synthwave/neon) | Início |
| `02-studio-glass.html` | Studio Glass (glassmorphism) | Adicionar música |
| `03-rack-minimal.html` | Rack Minimal (pro/denso) | Remover |
