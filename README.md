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

## Instalação no Mac (DaVinci Resolve Studio **e** Adobe Premiere Pro)

Num Mac zerado, cole no Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/lorransouzatreinamentos/viralcut/main/install-mac.sh | bash
```

Instala Homebrew, git, ffmpeg, Python 3.11, o `faster-whisper` e o painel do Premiere (se houver). **Não substitui** git/Python/ffmpeg que já existam na máquina — reaproveita o que está lá. Pede a chave da OpenAI (só para a IA escolher os cortes; o áudio nunca sai do computador).

Depois:
- **DaVinci** → `Preferences > System > General > External scripting using = Local`, abra a timeline na página Edit e rode `open ~/viralcut/viralcut.command`
- **Premiere** → reiniciar → **Window > Extensions > VIRALCUT**

Só o painel do Premiere (sem DaVinci): `bash scripts/install-premiere.sh`.

---

## Estrutura

| Pasta | O quê |
|-------|-------|
| `core/` | Servidor Python (FastAPI) — motor do DaVinci: transcrição, IA, aplica no Resolve |
| `core/objectives.py` | Montar falas (IA) + remover silêncios (algorítmico) |
| `core/adapters/davinci.py` | Fala com a API do Resolve (source path + criar timelines) |
| `premiere-panel/` | Painel CEP do Premiere (Node self-contained) |
| `ui/` | Interface compartilhada (servida pelo Core no DaVinci) |
| `core/cache.py` | Cache de transcrição por arquivo-fonte (compartilhado com o painel) |
| `install-windows.ps1` · `viralcut.bat` | Instalador Windows (DaVinci + Premiere) e launcher (DaVinci) |
| `install-mac.sh` · `viralcut.command` | Instalador macOS (DaVinci + Premiere) e launcher (DaVinci) |
| `tests/` | 73 testes unitários + e2e |

## Log

Toda análise grava `~/.viralcut/logs/last-run.json` (o que foi enviado à IA, transcrito, retornado e aplicado) — útil para depurar e avaliar a qualidade dos cortes.

## Transcrição: sempre local

A transcrição roda **sempre no seu computador** (via [faster-whisper](https://github.com/SYSTRAN/faster-whisper)) — grátis, offline, o vídeo nunca sai da máquina. Só o **texto** da transcrição (pequeno) vai pra nuvem depois, no passo de extrair os cortes virais (IA).

Se o `faster-whisper` não estiver instalado, o app **falha com instrução de conserto** — nunca sobe o áudio pra nuvem escondido. Os instaladores (`install-mac.sh`, `install-windows.ps1`) já o instalam. Manualmente:
```bash
~/viralcut/.venv/bin/pip install faster-whisper       # Mac/Linux
%USERPROFILE%\viralcut\.venv\Scripts\pip install faster-whisper  # Windows
```
Primeira transcrição baixa o modelo (`small`, ~466MB, uma vez só, fica em cache do HuggingFace). Depois roda ~7× mais rápido que tempo real em CPU comum, sem GPU.

### Cache

Cada transcrição é guardada em `~/.viralcut/cache/`, chaveada pelo **arquivo de vídeo** (caminho + tamanho + data de modificação). Abrir o mesmo vídeo de novo reaproveita a transcrição na hora — inclusive entre Premiere e DaVinci, que compartilham o mesmo cache.

Mexer na timeline (cortar, reordenar) **não** invalida o cache: o que é transcrito é o arquivo-fonte, e ele não mudou. Trocar o vídeo invalida sozinho. Para forçar do zero, use **↻ Transcrever novamente** no painel.

Escape hatch (usa a API da OpenAI, custa ~$0,006/min e sobe o áudio): `VIRALCUT_TRANSCRIBE=api` no `.env`.

## Requisitos por plataforma

- **DaVinci:** Resolve **Studio** + Python 3.11+ + ffmpeg + chave OpenAI.
- **Premiere:** Premiere Pro 2024+ + ffmpeg + chave OpenAI. Node é fornecido pelo CEP.
- **Transcrição local (opcional, nos dois):** `pip install faster-whisper`.
