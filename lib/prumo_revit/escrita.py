# -*- coding: utf-8 -*-
"""Escritas no modelo: lista FECHADA de operações, em uma transação (tudo ou nada).

Operações: create_views (planta | duplicar), create_sheets, place_views (vistas e tabelas), remove_tagged.
Cada elemento criado leva a marca  TPO|<plano_id>|<chave>  (idempotência: reaplicar o mesmo plano não duplica).

Travas (nesta ordem):
  1. o arquivo %LOCALAPPDATA%\\Prumo\\escrita_habilitada precisa existir; cada linha é um trecho do
     caminho do .rvt onde a escrita é permitida (ex.: "COPIA TESTE"). Sem linha que combine, tudo é recusado (403);
  2. modo dry_run executa tudo e desfaz no fim;
  3. remoção (remove_tagged) só com confirm=true (senão 428) e só toca elementos com a marca TPO|<plano>;
  4. nunca salva nem sincroniza; entrada validada contra o esquema abaixo (campo desconhecido → 400).
Compatível com IronPython 2.7 e CPython 3 (sem f-strings).
"""
import os

from pyrevit import DB

from .leitura import _col, _id, _nome, _param, PES_MM

MAX_ITENS = 300

CAMPOS = {
    "create_views": {"op", "items"},
    "create_sheets": {"op", "items"},
    "place_views": {"op", "items", "tabelas"},
    "remove_tagged": {"op", "plano_id"},
}
CAMPOS_VISTA = {"key", "tipo", "nivel", "nome", "escala", "modelo_vista", "origem", "tipo_vista"}
CAMPOS_FOLHA = {"key", "numero", "nome", "carimbo", "parametros"}
CAMPOS_COLOCAR = {"folha", "vista", "x", "y"}
CAMPOS_TABELA = {"folha", "nome", "x", "y"}


class Recusa(Exception):
    def __init__(self, msg, status):
        Exception.__init__(self, msg)
        self.status = status


def _flag_path():
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Prumo", "escrita_habilitada")


def _permitido(doc):
    """403 se a escrita não foi habilitada para este arquivo."""
    try:
        with open(_flag_path(), "r") as f:
            linhas = [l.strip().lower() for l in f.read().splitlines() if l.strip() and not l.strip().startswith("#")]
    except Exception:
        raise Recusa("Escrita desabilitada neste computador (falta %LOCALAPPDATA%\\Prumo\\escrita_habilitada).", 403)
    caminho = (doc.PathName or doc.Title or "").lower()
    if not any(l in caminho for l in linhas):
        raise Recusa("Este modelo não está na lista de escrita permitida (escrita_habilitada).", 403)
    if doc.IsLinked or doc.IsFamilyDocument:
        raise Recusa("Só é possível escrever em um projeto aberto (não vínculo nem família).", 403)


def _checar(cmd, permitidos, onde):
    extras = set(cmd.keys()) - permitidos
    if extras:
        raise ValueError("{}: campos desconhecidos: {}".format(onde, ", ".join(sorted(extras))))


def validar(comandos):
    if not isinstance(comandos, list) or not comandos:
        raise ValueError("commands deve ser uma lista não vazia.")
    total = 0
    for c in comandos:
        if not isinstance(c, dict) or c.get("op") not in CAMPOS:
            raise ValueError("Operação não permitida: {}".format(c.get("op") if isinstance(c, dict) else c))
        _checar(c, CAMPOS[c["op"]], c["op"])
        if c["op"] == "remove_tagged":
            if not c.get("plano_id"):
                raise ValueError("remove_tagged exige plano_id.")
            continue
        itens = c.get("items") or []
        total += len(itens) + len(c.get("tabelas") or [])
        for it in itens:
            if not isinstance(it, dict):
                raise ValueError("{}: item inválido.".format(c["op"]))
            if c["op"] == "create_views":
                _checar(it, CAMPOS_VISTA, "create_views.item")
                if it.get("tipo") not in ("planta", "duplicar"):
                    raise ValueError("create_views.tipo deve ser planta ou duplicar.")
            elif c["op"] == "create_sheets":
                _checar(it, CAMPOS_FOLHA, "create_sheets.item")
            else:
                _checar(it, CAMPOS_COLOCAR, "place_views.item")
        for tb in c.get("tabelas") or []:
            _checar(tb, CAMPOS_TABELA, "place_views.tabela")
    if total > MAX_ITENS:
        raise ValueError("Lote grande demais ({} itens; máximo {}).".format(total, MAX_ITENS))


# ---------------------------------------------------------------- marca de idempotência
_CANDIDATOS_MARCA = ("Comments", "Comentários", "Comentarios")


def _param_marca(el):
    for nome in _CANDIDATOS_MARCA:
        try:
            p = el.LookupParameter(nome)
            if p is not None and not p.IsReadOnly and p.StorageType == DB.StorageType.String:
                return p
        except Exception:
            pass
    for bip in ("ALL_MODEL_INSTANCE_COMMENTS", "VIEW_DESCRIPTION", "SHEET_DESCRIPTION"):
        try:
            p = el.get_Parameter(getattr(DB.BuiltInParameter, bip))
            if p is not None and not p.IsReadOnly and p.StorageType == DB.StorageType.String:
                return p
        except Exception:
            pass
    return None


def _ler_marca(el):
    p = _param_marca(el)
    try:
        return (p.AsString() or "") if p is not None else ""
    except Exception:
        return ""


def _marcar(el, marca):
    p = _param_marca(el)
    if p is None:
        raise ValueError("Elemento sem parâmetro de texto para a marca ({}).".format(_nome(el)))
    p.Set(marca)


def suporte_marca(doc):
    """Diagnóstico (leitura): quais parâmetros aceitam a marca em vistas e folhas deste modelo."""
    out = {}
    try:
        v = next(x for x in _col(doc, cls=DB.View) if not x.IsTemplate and x.ViewType == DB.ViewType.FloorPlan)
        out["vista"] = _param_marca(v) is not None
    except Exception:
        out["vista"] = None
    try:
        s = next(iter(_col(doc, cls=DB.ViewSheet)))
        out["folha"] = _param_marca(s) is not None
    except Exception:
        out["folha"] = None
    return out


# ---------------------------------------------------------------- buscas
def _por_nome(doc, cls, nome):
    for e in _col(doc, cls=cls):
        if _nome(e) == nome:
            return e
    return None


def _vista_por_nome(doc, nome):
    for v in _col(doc, cls=DB.View):
        if not v.IsTemplate and _nome(v) == nome and not isinstance(v, DB.ViewSheet):
            return v
    return None


def _modelo_vista(doc, nome):
    for v in _col(doc, cls=DB.View):
        if v.IsTemplate and _nome(v) == nome:
            return v
    return None


def _nivel(doc, nome):
    for l in _col(doc, cls=DB.Level):
        if _nome(l) == nome:
            return l
    return None


def _tipo_planta(doc, nome_tipo=None):
    candidatos = [t for t in _col(doc, cls=DB.ViewFamilyType, tipos=True) if t.ViewFamily == DB.ViewFamily.FloorPlan]
    if nome_tipo:
        for t in candidatos:
            if _nome(t) == nome_tipo:
                return t
        raise ValueError("Tipo de vista de planta não encontrado: {}".format(nome_tipo))
    if not candidatos:
        raise ValueError("O modelo não tem tipo de vista de planta.")
    return candidatos[0]


def _carimbo(doc, spec):
    familia = (spec or {}).get("familia")
    tipo = (spec or {}).get("tipo")
    for s in _col(doc, "OST_TitleBlocks", tipos=True):
        if getattr(s, "FamilyName", None) == familia and (not tipo or _nome(s) == tipo):
            return s
    raise ValueError("Carimbo não encontrado: {} {}".format(familia, tipo or ""))


def _escala(txt):
    try:
        return int(str(txt).split(":")[1])
    except Exception:
        raise ValueError("Escala inválida: {}".format(txt))


def _nome_livre(doc, base):
    nome, n = base, 1
    while _vista_por_nome(doc, nome) is not None:
        n += 1
        nome = "{} (TPO {})".format(base, n)
    return nome


# ---------------------------------------------------------------- operações
class Contexto(object):
    def __init__(self, doc, plano_id):
        self.doc = doc
        self.plano = plano_id
        self.mapa = {}       # chave -> elemento (vista/folha criada ou reaproveitada)
        self.relatorio = []
        self.avisos = []

    def marca(self, chave):
        return "TPO|{}|{}".format(self.plano, chave)

    def rel(self, op, chave, estado, detalhe=None, el=None):
        r = {"op": op, "key": chave, "estado": estado}
        if detalhe:
            r["detalhe"] = detalhe
        if el is not None:
            r["id"] = _id(el.Id)
        self.relatorio.append(r)


def _achar_marcado(ctx, cls, chave):
    marca = ctx.marca(chave)
    for e in _col(ctx.doc, cls=cls):
        if _ler_marca(e) == marca:
            return e
    return None


def op_create_views(ctx, cmd):
    doc = ctx.doc
    for it in cmd.get("items") or []:
        chave = it.get("key") or it.get("nome")
        if not chave:
            raise ValueError("create_views: item sem key/nome.")
        existente = _achar_marcado(ctx, DB.View, chave)
        if existente is not None:
            ctx.mapa[chave] = existente
            ctx.rel("create_views", chave, "existente", el=existente)
            continue
        if it["tipo"] == "planta":
            nivel = _nivel(doc, it.get("nivel"))
            if nivel is None:
                raise ValueError("Nível não encontrado: {}".format(it.get("nivel")))
            v = DB.ViewPlan.Create(doc, _tipo_planta(doc, it.get("tipo_vista")).Id, nivel.Id)
        else:
            origem = _vista_por_nome(doc, it.get("origem"))
            if origem is None:
                raise ValueError("Vista de origem não encontrada: {}".format(it.get("origem")))
            if not origem.CanViewBeDuplicated(DB.ViewDuplicateOption.WithDetailing):
                raise ValueError("A vista não pode ser duplicada: {}".format(it.get("origem")))
            v = doc.GetElement(origem.Duplicate(DB.ViewDuplicateOption.WithDetailing))
        nome = _nome_livre(doc, it.get("nome") or chave)
        v.Name = nome
        if it.get("escala"):
            v.Scale = _escala(it["escala"])
        if it.get("modelo_vista"):
            mv = _modelo_vista(doc, it["modelo_vista"])
            if mv is None:
                raise ValueError("Modelo de vista não encontrado: {}".format(it["modelo_vista"]))
            v.ViewTemplateId = mv.Id
            if it.get("escala"):
                # o modelo pode controlar a escala; a escala do plano prevalece só se o modelo não a controla
                pass
        _marcar(v, ctx.marca(chave))
        ctx.mapa[chave] = v
        ctx.rel("create_views", chave, "criado" if nome == (it.get("nome") or chave) else "criado (renomeado)", nome, v)


def op_create_sheets(ctx, cmd):
    doc = ctx.doc
    existentes = dict((s.SheetNumber, s) for s in _col(doc, cls=DB.ViewSheet))
    for it in cmd.get("items") or []:
        chave = it.get("key") or it.get("numero")
        marcada = _achar_marcado(ctx, DB.ViewSheet, chave)
        if marcada is not None:
            ctx.mapa[chave] = marcada
            ctx.rel("create_sheets", chave, "existente", marcada.SheetNumber, marcada)
            continue
        numero = it.get("numero")
        if numero in existentes:
            raise ValueError("Já existe prancha com o número {} (sem a marca do plano).".format(numero))
        simbolo = _carimbo(doc, it.get("carimbo"))
        if not simbolo.IsActive:
            simbolo.Activate()
        s = DB.ViewSheet.Create(doc, simbolo.Id)
        s.SheetNumber = numero
        s.Name = it.get("nome") or numero
        _marcar(s, ctx.marca(chave))
        tbs = list(_col(doc, "OST_TitleBlocks", view_id=s.Id))
        for nome_p, valor in (it.get("parametros") or {}).items():
            gravado = False
            for alvo in [s] + tbs[:1]:
                try:
                    p = alvo.LookupParameter(nome_p)
                    if p is not None and not p.IsReadOnly and p.StorageType == DB.StorageType.String:
                        p.Set(valor)
                        gravado = True
                        break
                except Exception:
                    continue
            if not gravado:
                ctx.avisos.append("Parâmetro '{}' não existe (ou é somente leitura) na prancha {}.".format(nome_p, numero))
        existentes[numero] = s
        ctx.mapa[chave] = s
        ctx.rel("create_sheets", chave, "criado", numero, s)


def _folha(ctx, ref):
    s = ctx.mapa.get(ref)
    if s is None:
        for x in _col(ctx.doc, cls=DB.ViewSheet):
            if x.SheetNumber == ref:
                return x
        raise ValueError("Prancha não encontrada: {}".format(ref))
    return s


def _ponto(folha, x, y):
    out = folha.Outline
    u0, v0, u1, v1 = out.Min.U, out.Min.V, out.Max.U, out.Max.V
    return DB.XYZ(u0 + float(x) * (u1 - u0), v0 + float(y) * (v1 - v0), 0)


def op_place_views(ctx, cmd):
    doc = ctx.doc
    for it in cmd.get("items") or []:
        folha = _folha(ctx, it["folha"])
        ref = it["vista"]
        vista = ctx.mapa.get(ref.get("key")) if isinstance(ref, dict) and ref.get("key") else None
        if vista is None:
            nome = ref.get("nome") if isinstance(ref, dict) else ref
            vista = _vista_por_nome(doc, nome)
        if vista is None:
            raise ValueError("Vista não encontrada: {}".format(ref))
        pt = _ponto(folha, it["x"], it["y"])
        existente = None
        for vp in _col(doc, cls=DB.Viewport, view_id=folha.Id):
            if _id(vp.ViewId) == _id(vista.Id):
                existente = vp
        if existente is not None:
            existente.SetBoxCenter(pt)
            ctx.rel("place_views", _nome(vista), "atualizado", folha.SheetNumber)
            continue
        if not DB.Viewport.CanAddViewToSheet(doc, folha.Id, vista.Id):
            raise ValueError("A vista '{}' já está em outra prancha (use create_views tipo duplicar).".format(_nome(vista)))
        vp = DB.Viewport.Create(doc, folha.Id, vista.Id, pt)
        ctx.rel("place_views", _nome(vista), "colocado", folha.SheetNumber, vp)
    for tb in cmd.get("tabelas") or []:
        folha = _folha(ctx, tb["folha"])
        sch = _por_nome(doc, DB.ViewSchedule, tb["nome"])
        if sch is None:
            raise ValueError("Tabela não encontrada: {}".format(tb["nome"]))
        pt = _ponto(folha, tb["x"], tb["y"])
        achou = None
        for si in _col(doc, cls=DB.ScheduleSheetInstance, view_id=folha.Id):
            if _id(si.ScheduleId) == _id(sch.Id):
                achou = si
        if achou is not None:
            achou.Point = pt
            ctx.rel("place_views", tb["nome"], "atualizado", folha.SheetNumber)
        else:
            DB.ScheduleSheetInstance.Create(doc, folha.Id, sch.Id, pt)
            ctx.rel("place_views", tb["nome"], "colocado", folha.SheetNumber)


def op_remove_tagged(ctx, cmd, confirm):
    if not confirm:
        raise Recusa("remove_tagged remove elementos criados pelo plano: confirme com o usuário e reenvie com confirm=true.", 428)
    prefixo = "TPO|{}|".format(cmd["plano_id"])
    alvos_f = [s for s in _col(ctx.doc, cls=DB.ViewSheet) if _ler_marca(s).startswith(prefixo)]
    alvos_v = [v for v in _col(ctx.doc, cls=DB.View) if not v.IsTemplate and not isinstance(v, DB.ViewSheet) and _ler_marca(v).startswith(prefixo)]
    n = 0
    for e in alvos_f + alvos_v:
        try:
            ctx.doc.Delete(e.Id)
            n += 1
        except Exception as ex:
            ctx.avisos.append("Não removido: {} ({})".format(_nome(e), ex))
    ctx.rel("remove_tagged", cmd["plano_id"], "removido", "{} elemento(s)".format(n))


# ---------------------------------------------------------------- entrada
def aplicar(doc, comandos, plano_id, modo, confirm):
    """Valida e executa o plano numa transação. modo: 'dry_run' (desfaz no fim) ou 'aplicar'."""
    if modo not in ("dry_run", "aplicar"):
        raise ValueError("mode deve ser dry_run ou aplicar.")
    _permitido(doc)
    validar(comandos)
    plano_id = str(plano_id or "").strip()
    if not plano_id or "|" in plano_id:
        raise ValueError("plan_id obrigatório (sem '|').")
    ctx = Contexto(doc, plano_id)
    t = DB.Transaction(doc, "Prumo: plano " + plano_id[:40])
    t.Start()
    try:
        for c in comandos:
            op = c["op"]
            if op == "create_views":
                op_create_views(ctx, c)
            elif op == "create_sheets":
                op_create_sheets(ctx, c)
            elif op == "place_views":
                op_place_views(ctx, c)
            elif op == "remove_tagged":
                op_remove_tagged(ctx, c, confirm)
        if modo == "dry_run":
            t.RollBack()
        else:
            t.Commit()
    except Exception:
        try:
            if t.HasStarted():
                t.RollBack()
        except Exception:
            pass
        raise
    return {"mode": modo, "plan_id": plano_id, "ok": True, "report": ctx.relatorio, "warnings": ctx.avisos}
