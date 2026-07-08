# VIRALCUT

Extensão de cortes virais com IA para **Adobe Premiere Pro** e **DaVinci Resolve Studio**.

Fluxo: seleciona a sequência → transcreve o áudio → escolhe um objetivo (reaplicável):
1. **Falas virais** — extrai os melhores cortes diretos, cada um numa cor.
2. **Montar falas** — costura trechos de vários momentos numa narrativa nova (frankenbite).
3. **Remover silêncios** — corta as pausas e junta as falas.

A transcrição é feita uma vez; depois você aplica quantos objetivos quiser na mesma timeline.

---

## Instalação no Windows (DaVinci Resolve **Studio**)

> Requer DaVinci Resolve **Studio** (a versão gratuita não permite automação).

**Pré-requisitos** (instale uma vez):
- [Python 3.11+](https://python.org) — marque **"Add Python to PATH"** no instalador.
- [Git](https://git-scm.com).
- ffmpeg: no PowerShell, `winget install Gyan.FFmpeg` (depois reabra o PowerShell).

**Instalar o VIRALCUT** (PowerShell):
```powershell
git clone https://github.com/lorransouzatreinamentos/viralcut.git
cd viralcut
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
```
O instalador pede a chave da OpenAI e configura tudo. (O repositório é privado — você precisa estar convidado e autenticado no GitHub para o `git clone`.)

**No DaVinci Resolve**, uma vez: `Preferences > System > General > External scripting using = **Local**`.

**Usar:**
1. Abra o Resolve com um projeto e uma timeline.
2. Dê dois cliques em **`viralcut.bat`** (ou rode no terminal). Ele abre o app no navegador.
3. Selecionar sequência → Analisar → escolher objetivo → Aplicar.

**Atualizar:** clique na **logo ✂ VIRALCUT** no topo do app — ele puxa a versão nova do GitHub sozinho e recarrega.

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
