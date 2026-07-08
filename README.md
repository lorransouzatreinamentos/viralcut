# VIRALCUT

Extensão de cortes virais com IA para **Adobe Premiere Pro** e **DaVinci Resolve Studio**.

Fluxo: seleciona a sequência → transcreve o áudio → escolhe um objetivo (reaplicável):
1. **Falas virais** — extrai os melhores cortes diretos, cada um numa cor.
2. **Montar falas** — costura trechos de vários momentos numa narrativa nova (frankenbite).
3. **Remover silêncios** — corta as pausas e junta as falas.

A transcrição é feita uma vez; depois você aplica quantos objetivos quiser na mesma timeline.

---

## Instalação no Windows (DaVinci Resolve Studio **e** Adobe Premiere Pro)

> Um instalador único configura os **dois editores** na mesma máquina.
> Único pré-requisito que você precisa ter: **DaVinci Resolve Studio** (a versão gratuita não permite automação) e/ou **Adobe Premiere Pro**, mais uma **chave da OpenAI**.
> O instalador baixa e instala sozinho tudo o mais (Git, Python, ffmpeg).

**Passo único** — no PowerShell:

```powershell
irm https://raw.githubusercontent.com/lorransouzatreinamentos/viralcut/main/install-windows.ps1 | iex
```

Ele vai:
1. Instalar **Git**, **Python 3.11** e **ffmpeg** (via `winget`, já embutido no Windows 10/11).
2. Baixar o VIRALCUT para `C:\Users\<você>\viralcut`.
3. Montar o ambiente Python e instalar as dependências.
4. Pedir a **chave da OpenAI** e salvá-la.
5. **Instalar o painel do Adobe Premiere Pro** (pasta de extensões CEP do usuário + registro `PlayerDebugMode`).
6. Criar um atalho **VIRALCUT** na área de trabalho (para o fluxo DaVinci).

**No DaVinci Resolve**, uma vez só: `Preferences > System > General > External scripting using = **Local**`.

**No Adobe Premiere Pro**, uma vez só: se ele já estava aberto durante a instalação, **feche e abra de novo** (o painel só aparece após reiniciar).

**Usar no DaVinci:**
1. Abra o Resolve (Studio) com um projeto e uma timeline.
2. Clique no atalho **VIRALCUT** na área de trabalho — o app abre no navegador.
3. Selecionar sequência → Analisar → escolher objetivo → Aplicar.

**Usar no Premiere:**
1. Abra o Premiere com um projeto e uma sequência.
2. Menu **Window > Extensions > VIRALCUT**.
3. Selecionar sequência → Analisar → escolher objetivo → Aplicar.

**Atualizar (nos dois):** clique na **logo ✂ VIRALCUT** no topo do app — ele puxa a versão nova do GitHub e recarrega sozinho. No Premiere, feche e reabra o painel depois.

---

## Instalação no Mac (Adobe Premiere Pro)

Painel CEP self-contained. Ver `scripts/install-premiere.sh` e `PLANO_MESTRE.md`.

```bash
bash scripts/install-premiere.sh   # instala o painel + ativa debug mode
# preencher a chave em ~/.viralcut/.env
```
Reiniciar o Premiere → **Window > Extensions > VIRALCUT**.

---

## Estrutura

| Pasta | O quê |
|-------|-------|
| `core/` | Servidor Python (FastAPI) — motor do DaVinci: transcrição, IA, aplica no Resolve |
| `core/objectives.py` | Montar falas (IA) + remover silêncios (algorítmico) |
| `core/adapters/davinci.py` | Fala com a API do Resolve (source path + criar timelines) |
| `premiere-panel/` | Painel CEP do Premiere (Node self-contained) |
| `ui/` | Interface compartilhada (servida pelo Core no DaVinci) |
| `install-windows.ps1` · `viralcut.bat` | Instalador Windows (DaVinci + Premiere) e launcher (DaVinci) |
| `tests/` | 47 testes unitários + e2e |

## Log

Toda análise grava `~/.viralcut/logs/last-run.json` (o que foi enviado à IA, transcrito, retornado e aplicado) — útil para depurar e avaliar a qualidade dos cortes.

## Requisitos por plataforma

- **DaVinci:** Resolve **Studio** + Python 3.11+ + ffmpeg + chave OpenAI.
- **Premiere:** Premiere Pro 2024+ + ffmpeg + chave OpenAI. Node é fornecido pelo CEP.
