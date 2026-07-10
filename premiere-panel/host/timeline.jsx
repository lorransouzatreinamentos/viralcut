/**
 * VIRALCUT — ExtendScript host (Premiere Pro).
 *
 * Materializa cortes. TODA a inteligencia esta no Core Python; este arquivo so
 * executa operacoes de timeline. Assinaturas verificadas contra
 * ppro-scripting.docsforadobe.dev (2024/2025).
 *
 * REGRAS (aprendidas do fracasso do FastVideo — ver PLANO_MESTRE.md secao 1):
 *   - NUNCA usar QE DOM (qe.*): nao suportado, quebra entre versoes.
 *   - NUNCA setColorLabel em trackItem: nao existe. So em projectItem.
 *   - createSubClip recebe ticks como STRING.
 *   - Nao editar a sequencia original: criar uma NOVA sequencia com os cortes.
 *
 * Estrategia de criacao de sequencia: createNewSequenceFromClips(name, [subclips]).
 * Escolhida porque (a) nao abre dialogo (ao contrario de createNewSequence com
 * ID vazio), (b) insere os clips SEQUENCIALMENTE e ENCAIXADOS automaticamente,
 * (c) deriva as settings da sequencia do primeiro clip. Isso evita insertClip,
 * cuja unidade de 'time' (ticks vs segundos) e ambigua entre versoes.
 */

// json2.js deve ser carregado antes deste arquivo (ES3 nao tem JSON nativo).

var VIRALCUT = (function () {

    var TICKS_PER_SEC = 254016000000;

    function findProjectItemByNodeId(nodeId, root) {
        root = root || app.project.rootItem;
        for (var i = 0; i < root.children.numItems; i++) {
            var child = root.children[i];
            if (child.nodeId === nodeId) return child;
            if (child.type === 2) { // 2 = BIN (ProjectItemType pode nao existir no ExtendScript)
                var found = findProjectItemByNodeId(nodeId, child);
                if (found) return found;
            }
        }
        return null;
    }

    /** Lê a sequência ativa e devolve descritores dos trackItems (em SEGUNDOS)
     *  para o Core montar o plano. Ver PLANO_MESTRE.md secao 8 (PremiereContext). */
    function getActiveSequenceInfo() {
      try {
        var seq = app.project.activeSequence;
        if (!seq) return JSON.stringify({ error: "Nenhuma sequencia ativa." });

        var fps = TICKS_PER_SEC / parseFloat(seq.timebase); // timebase = ticks por frame
        var items = [];
        for (var t = 0; t < seq.videoTracks.numTracks; t++) {
            var track = seq.videoTracks[t];
            for (var c = 0; c < track.clips.numItems; c++) {
                var clip = track.clips[c];
                if (!clip.projectItem) continue;
                items.push({
                    start: parseFloat(clip.start.ticks) / TICKS_PER_SEC,
                    end: parseFloat(clip.end.ticks) / TICKS_PER_SEC,
                    in_point: parseFloat(clip.inPoint.ticks) / TICKS_PER_SEC,
                    project_item_id: clip.projectItem.nodeId,
                    name: clip.name
                });
            }
        }
        return JSON.stringify({
            name: seq.name,
            fps: fps,
            duration_sec: parseFloat(seq.end) / TICKS_PER_SEC,
            seq_items: items
        });
      } catch (e) {
        return JSON.stringify({ error: "getActiveSequenceInfo: " + (e && e.message ? e.message : String(e)) });
      }
    }

    /** TODOS os clips de video da sequencia aberta, em SEGUNDOS (espelho de
     *  davinci.list_timeline_clips). Corrige o bug de "so analisou o 1o video":
     *  o painel transcreve cada arquivo-fonte e costura em tempo de timeline.
     *
     *  Por clip: source_key (caminho do arquivo), ref (nodeId p/ createSubClip),
     *  src_in (in-point na origem), tl_start/tl_end (posicao na timeline). */
    function getTimelineClips() {
      try {
        var seq = app.project.activeSequence;
        if (!seq) return JSON.stringify({ error: "Nenhuma sequencia ativa. Abra a timeline no Premiere." });

        var fps = TICKS_PER_SEC / parseFloat(seq.timebase);
        var clips = [];
        for (var t = 0; t < seq.videoTracks.numTracks; t++) {
            var track = seq.videoTracks[t];
            for (var c = 0; c < track.clips.numItems; c++) {
                var clip = track.clips[c];
                if (!clip.projectItem) continue;
                var path = "";
                try { path = clip.projectItem.getMediaPath(); } catch (eP) {}
                if (!path) continue; // clip sem midia de origem (titulo, cor solida): ignora
                clips.push({
                    source_key: path,
                    ref: clip.projectItem.nodeId,
                    src_in: parseFloat(clip.inPoint.ticks) / TICKS_PER_SEC,
                    tl_start: parseFloat(clip.start.ticks) / TICKS_PER_SEC,
                    tl_end: parseFloat(clip.end.ticks) / TICKS_PER_SEC,
                    name: clip.name
                });
            }
        }
        if (!clips.length) return JSON.stringify({ error: "A timeline aberta nao tem nenhum clip de video com midia." });

        return JSON.stringify({
            name: seq.name,
            fps: fps,
            duration_sec: parseFloat(seq.end) / TICKS_PER_SEC,
            clips: clips
        });
      } catch (e) {
        return JSON.stringify({ error: "getTimelineClips: " + (e && e.message ? e.message : String(e)) });
      }
    }

    /** Aplica o PremiereCutPlan calculado pelo Core.
     *  planJson = { new_sequence_name, cuts: [{project_item_id, in_ticks, out_ticks, label_index, titulo}] }
     *  Cria subclips coloridos e monta uma NOVA sequencia com eles. */
    function applyCutPlan(planJson) {
      try {
        var plan;
        try { plan = JSON.parse(planJson); }
        catch (eParse) { return JSON.stringify({ error: "plano invalido: " + eParse.message }); }

        var cuts = plan.cuts || [];
        if (!cuts.length) return JSON.stringify({ error: "plano sem cortes." });

        // Pasta (bin) por timeline: agrupa a sequencia + os subclipes, mantendo o
        // painel de projeto limpo. Se createBin falhar, cai para a raiz.
        var bin = app.project.rootItem;
        try {
            var made = app.project.rootItem.createBin(plan.new_sequence_name);
            if (made && made !== 0) bin = made;
        } catch (eBin) {}

        var subclips = [];
        var warnings = [];

        for (var i = 0; i < cuts.length; i++) {
            var cut = cuts[i];
            var source = findProjectItemByNodeId(cut.project_item_id);
            if (!source) {
                warnings.push("corte '" + cut.titulo + "': item de origem nao encontrado — pulado");
                continue;
            }
            var name = "VC " + (i + 1) + " — " + cut.titulo;
            // createSubClip(name, startTicksStr, endTicksStr, hasHardBoundaries, takeVideo, takeAudio)
            var sub = source.createSubClip(name, cut.in_ticks, cut.out_ticks, 0, 1, 1);
            if (!sub || sub === 0) {
                warnings.push("corte '" + cut.titulo + "': createSubClip falhou — pulado");
                continue;
            }
            try { sub.setColorLabel(cut.label_index); } catch (eCol) {}
            try { if (bin !== app.project.rootItem) sub.moveBin(bin); } catch (eMove) {}
            subclips.push(sub);
        }

        if (!subclips.length) {
            return JSON.stringify({ error: "nenhum subclip criado.", warnings: warnings });
        }

        // createNewSequenceFromClips insere os subclips sequencialmente e encaixados,
        // na nova sequencia (original intacta), derivando settings do 1o clip.
        // 3o arg = bin de destino -> a sequencia nasce dentro da pasta.
        var newSeq = app.project.createNewSequenceFromClips(plan.new_sequence_name, subclips, bin);
        if (!newSeq || newSeq === 0) {
            return JSON.stringify({ error: "createNewSequenceFromClips falhou.", warnings: warnings });
        }

        return JSON.stringify({
            ok: true,
            new_sequence_name: plan.new_sequence_name,
            created: subclips.length,
            binned: (bin !== app.project.rootItem),
            warnings: warnings
        });
      } catch (e) {
        return JSON.stringify({ error: "applyCutPlan: " + (e && e.message ? e.message : String(e)) });
      }
    }

    // Auto-teste de carregamento: se a UI conseguir chamar isto, o host carregou.
    function version() {
        return JSON.stringify({ ok: true, build: (typeof VIRALCUT_BUILD !== "undefined" ? VIRALCUT_BUILD : "?") });
    }

    return {
        version: version,
        getActiveSequenceInfo: getActiveSequenceInfo,
        getTimelineClips: getTimelineClips,
        applyCutPlan: applyCutPlan
    };
})();
