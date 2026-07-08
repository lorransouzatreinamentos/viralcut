# VIRALCUT

Extensão de cortes virais com IA para **Adobe Premiere Pro** e **DaVinci Resolve Studio**.

Fluxo: seleciona a sequência → transcreve o áudio → escolhe um objetivo (reaplicável):
1. **Falas virais** — extrai os melhores cortes diretos, cada um numa cor.
2. **Montar falas** — costura trechos de vários momentos numa narrativa nova (frankenbite).
3. **Remover silêncios** — corta as pausas e junta as falas.

A transcrição é feita uma vez; depois você aplica quantos objetivos quiser na mesma timeline.

---

## Instalação no Windows (DaVinci Resolve **Studio**)

> Único pré-requisito que você precisa ter: **DaVinci Resolve Studio** (a versão gratuita não permite automação) e uma **chave da OpenAI**.
> O instalador baixa e instala sozinho tudo o mais (Git, Python, ffmpeg).

**Passo único** — salve o `install-windows.ps1` numa pasta e, no PowerShell, rode:

```powershell
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
```

Ele vai:
1. Instalar **Git**, **Python 3.11** e **ffmpeg** (via `winget`, já embutido no Windows 10/11).
2. Baixar o VIRALCUT para `C:\Users\<você>\viralcut`.
3. Montar o ambiente Python e instalar as dependências.
4. Pedir a **chave da OpenAI** e salvá-la.
5. Criar um atalho **VIRALCUT** na área de trabalho.

> Se o repositório for privado, você precisa estar convidado como colaborador — o Git pede login no navegador na hora de baixar.

**No DaVinci Resolve**, uma vez só: `Preferences > System > General > External scripting using = **Local**`.

**Usar:**
1. Abra o Resolve (Studio) com um projeto e uma timeline.
2. Clique no atalho **VIRALCUT** na área de trabalho — o app abre no navegador.
3. Selecionar sequência → Analisar → escolher objetivo → Aplicar.

**Atualizar:** clique na **logo ✂ VIRALCUT** no topo do app — ele puxa a versão nova do GitHub e recarrega sozinho.

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
| `install-windows.ps1` · `viralcut.bat` | Instalador e launcher Windows (DaVinci) |
| `tests/` | 47 testes unitários + e2e |

## Log

Toda análise grava `~/.viralcut/logs/last-run.json` (o que foi enviado à IA, transcrito, retornado e aplicado) — útil para depurar e avaliar a qualidade dos cortes.

## Requisitos por plataforma

- **DaVinci:** Resolve **Studio** + Python 3.11+ + ffmpeg + chave OpenAI.
- **Premiere:** Premiere Pro 2024+ + ffmpeg + chave OpenAI. Node é fornecido pelo CEP.
