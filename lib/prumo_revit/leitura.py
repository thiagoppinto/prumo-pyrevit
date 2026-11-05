# -*- coding: utf-8 -*-
"""Leituras do modelo (sem transação; nada é alterado).

Unidades: comprimentos em mm (o Revit trabalha em pés internamente).
Compatível com IronPython 2.7 e CPython 3 (sem f-strings).
"""
import re

from pyrevit import DB, revit

PES_MM = 304.8

CATEGORIAS = [
    ("paredes", "OST_Walls"), ("pisos", "OST_Floors"), ("forros", "OST_Ceilings"), ("coberturas", "OST_Roofs"),
    ("portas", "OST_Doors"), ("janelas", "OST_Windows"), ("escadas", "OST_Stairs"), ("guarda_corpos", "OST_Railings"),
    ("pilares_arq", "OST_Columns"), ("pilares_est", "OST_StructuralColumns"), ("vigas", "OST_StructuralFraming"),
    ("fundacoes", "OST_StructuralFoundation"), ("ambientes", "OST_Rooms"), ("mobiliario", "OST_Furniture"),
    ("luminarias", "OST_LightingFixtures"), ("loucas", "OST_PlumbingFixtures"), ("cotas", "OST_Dimensions"),
    ("textos", "OST_TextNotes"), ("pranchas", "OST_Sheets"), ("vinculos_rvt", "OST_RvtLinks"),
]


def _id(eid):
    """ElementId → int (Revit 2024+ usa .Value; versões antigas, .IntegerValue)."""
    try:
        return int(eid.Value)
    except Exception:
        return int(eid.IntegerValue)


def _nome(el):
    try:
        return el.Name
    except Exception:
        try:
            return DB.Element.Name.GetValue(el)
        except Exception:
            return ""


def _mm(pes):
    return round(pes * PES_MM, 1)


def _param(el, bip):
    try:
        p = el.get_Parameter(bip)
        if p is None or not p.HasValue:
            return None
        if p.StorageType == DB.StorageType.Double:
            return p.AsDouble()
        if p.StorageType == DB.StorageType.Integer:
            return p.AsInteger()
        return p.AsString() or p.AsValueString()
    except Exception:
        return None


def _col(doc, cat_name=None, cls=None, view_id=None, tipos=False):
    c = DB.FilteredElementCollector(doc, view_id) if view_id is not None else DB.FilteredElementCollector(doc)
    if cls is not None:
        c = c.OfClass(cls)
    if cat_name is not None:
        c = c.OfCategory(getattr(DB.BuiltInCategory, cat_name))
    return c.WhereElementIsElementType() if tipos else c.WhereElementIsNotElementType()


# ---------------------------------------------------------------- documentos
def documentos():
    """Projetos abertos no Revit (sem famílias nem vínculos)."""
    uiapp = revit.HOST_APP.uiapp
    ativo = uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    out = []
    for d in uiapp.Application.Documents:
        try:
            if d.IsFamilyDocument or d.IsLinked:
                continue
        except Exception:
            pass
        ref = d.PathName or d.Title
        out.append({
            "ref": ref,
            "name": d.Title,
            "active": bool(ativo is not None and d.Equals(ativo)),
            "workshared": bool(d.IsWorkshared),
        })
    return out


def resolver(ref):
    """Documento aberto pelo caminho ou título; sem ref, o ativo."""
    uiapp = revit.HOST_APP.uiapp
    if not ref:
        return uiapp.ActiveUIDocument.Document if uiapp.ActiveUIDocument else None
    for d in uiapp.Application.Documents:
        if (d.PathName and d.PathName.lower() == ref.lower()) or d.Title == ref:
            return d
    return None


def status():
    app = revit.HOST_APP.app
    docs = documentos()
    return {
        "revit": str(app.VersionNumber),
        "build": str(app.VersionBuild),
        "usuario": str(app.Username),
        "documentos": len(docs),
        "ativo": next((x["name"] for x in docs if x["active"]), None),
    }


# ---------------------------------------------------------------- resumo
def resumo(doc):
    counts = {}
    for chave, cat in CATEGORIAS:
        try:
            n = _col(doc, cat).GetElementCount()
        except Exception:
            n = 0
        if n:
            counts[chave] = n

    niveis = sorted(
        [{"name": _nome(l), "elevation_mm": _mm(l.Elevation)} for l in _col(doc, cls=DB.Level)],
        key=lambda x: x["elevation_mm"],
    )
    vistas = {}
    for v in _col(doc, cls=DB.View):
        if v.IsTemplate:
            continue
        t = str(v.ViewType)
        vistas[t] = vistas.get(t, 0) + 1

    revisoes = []
    for r in _col(doc, cls=DB.Revision):
        revisoes.append({
            "seq": r.SequenceNumber,
            "code": str(_param(r, DB.BuiltInParameter.PROJECT_REVISION_REVISION_NUM) or r.SequenceNumber),
            "description": r.Description or "",
            "date": r.RevisionDate or "",
            "issued": bool(r.Issued),
        })
    revisoes.sort(key=lambda x: x["seq"])

    try:
        avisos = doc.GetWarnings()
        n_avisos = len(list(avisos))
    except Exception:
        n_avisos = None

    pi = doc.ProjectInformation
    return {
        "name": (pi.Name if pi and pi.Name else doc.Title),
        "number": pi.Number if pi else None,
        "client": pi.ClientName if pi else None,
        "file": doc.Title,
        "workshared": bool(doc.IsWorkshared),
        "counts": counts,
        "levels": niveis,
        "views": vistas,
        "sheets": counts.get("pranchas", 0),
        "revisions": revisoes,
        "issues": [{"level": "aviso", "message": "{} avisos no modelo".format(n_avisos)}] if n_avisos else [],
    }


# ---------------------------------------------------------------- padrão de documentação
def _carimbo(doc, folha):
    tbs = list(_col(doc, "OST_TitleBlocks", view_id=folha.Id))
    if not tbs:
        return None
    tb = tbs[0]
    tipo = doc.GetElement(tb.GetTypeId())
    w = _param(tb, DB.BuiltInParameter.SHEET_WIDTH) or (tipo and _param(tipo, DB.BuiltInParameter.SHEET_WIDTH))
    h = _param(tb, DB.BuiltInParameter.SHEET_HEIGHT) or (tipo and _param(tipo, DB.BuiltInParameter.SHEET_HEIGHT))
    familia = tipo.FamilyName if tipo is not None and hasattr(tipo, "FamilyName") else ""
    return {
        "family": familia,
        "type": _nome(tipo) if tipo is not None else "",
        "width_mm": _mm(w) if isinstance(w, float) else None,
        "height_mm": _mm(h) if isinstance(h, float) else None,
    }


def _formato(w, h):
    """A0..A4 pelo tamanho (tolerância de 15 mm), senão 'personalizado'."""
    if not w or not h:
        return None
    a, b = sorted([w, h])
    for nome, (x, y) in [("A4", (210, 297)), ("A3", (297, 420)), ("A2", (420, 594)), ("A1", (594, 841)), ("A0", (841, 1189))]:
        if abs(a - x) <= 15 and abs(b - y) <= 15:
            return nome
    return "personalizado"


def _vista(doc, v):
    d = {"name": _nome(v), "type": str(v.ViewType)}
    try:
        d["scale"] = "1:{}".format(v.Scale) if v.Scale else None
    except Exception:
        pass
    try:
        if v.GenLevel is not None:
            d["level"] = _nome(v.GenLevel)
    except Exception:
        pass
    try:
        if v.ViewTemplateId is not None and _id(v.ViewTemplateId) > 0:
            d["template"] = _nome(doc.GetElement(v.ViewTemplateId))
    except Exception:
        pass
    try:
        d["detail_level"] = str(v.DetailLevel)
    except Exception:
        pass
    # profundidade de corte / faixa da vista (só plantas): alturas em mm relativas ao nível associado
    try:
        if isinstance(v, DB.ViewPlan):
            vr = v.GetViewRange()
            d["view_range"] = {
                "cut_mm": _mm(vr.GetOffset(DB.PlanViewPlane.CutPlane)),
                "top_mm": _mm(vr.GetOffset(DB.PlanViewPlane.TopClipPlane)),
                "bottom_mm": _mm(vr.GetOffset(DB.PlanViewPlane.BottomClipPlane)),
                "depth_mm": _mm(vr.GetOffset(DB.PlanViewPlane.ViewDepthPlane)),
            }
    except Exception:
        pass
    try:
        d["filters"] = len(list(v.GetFilters()))
    except Exception:
        pass
    try:
        d["crop"] = bool(v.CropBoxActive)
    except Exception:
        pass
    try:
        disc = _param(v, DB.BuiltInParameter.VIEW_DISCIPLINE)
        if disc is not None:
            d["discipline"] = disc
    except Exception:
        pass
    return d


def _params_texto(el, limite=40):
    """Parâmetros de texto preenchidos de um elemento (nome → valor): o que alimenta o carimbo."""
    out = {}
    try:
        for p in el.Parameters:
            if len(out) >= limite:
                break
            try:
                if p.StorageType != DB.StorageType.String or not p.HasValue:
                    continue
                val = p.AsString()
                if val:
                    out[p.Definition.Name] = val[:120]
            except Exception:
                continue
    except Exception:
        pass
    return out


def padrao(doc, limite=300):
    """Como o escritório documenta: carimbos, formatos, pranchas e o que vai em cada uma."""
    folhas = [s for s in _col(doc, cls=DB.ViewSheet) if not s.IsPlaceholder]
    folhas.sort(key=lambda s: s.SheetNumber)
    pranchas = []
    carimbos = {}
    formatos = {}
    prefixos = {}
    escalas = {}
    tipos_por_prancha = {}

    for s in folhas[:limite]:
        tb = _carimbo(doc, s)
        fmt = _formato(tb and tb["width_mm"], tb and tb["height_mm"]) if tb else None
        if tb:
            k = "{} : {}".format(tb["family"], tb["type"])
            carimbos[k] = carimbos.get(k, 0) + 1
        if fmt:
            formatos[fmt] = formatos.get(fmt, 0) + 1
        m = re.match(r"^([A-Za-z]+)", s.SheetNumber or "")
        pref = m.group(1).upper() if m else "(numérico)"
        prefixos[pref] = prefixos.get(pref, 0) + 1

        try:
            out = s.Outline
            sx0, sy0, sx1, sy1 = out.Min.U, out.Min.V, out.Max.U, out.Max.V
        except Exception:
            sx0 = sy0 = 0.0
            sx1 = sy1 = 1.0
        larg = (sx1 - sx0) or 1.0
        alt = (sy1 - sy0) or 1.0

        vistas = []
        viewports = {}
        for vp in _col(doc, cls=DB.Viewport, view_id=s.Id):
            viewports[_id(vp.ViewId)] = vp
        for vid in s.GetAllPlacedViews():
            v = doc.GetElement(vid)
            if v is None:
                continue
            item = _vista(doc, v)
            vp = viewports.get(_id(vid))
            if vp is not None:
                # center: fração da folha, X da esquerda para a direita, Y de BAIXO para CIMA (como o Revit)
                c = vp.GetBoxCenter()
                item["center"] = [round((c.X - sx0) / larg, 3), round((c.Y - sy0) / alt, 3)]
                try:
                    o = vp.GetBoxOutline()
                    item["box_mm"] = [_mm(o.MaximumPoint.X - o.MinimumPoint.X), _mm(o.MaximumPoint.Y - o.MinimumPoint.Y)]
                except Exception:
                    pass
                try:
                    item["viewport_type"] = _nome(doc.GetElement(vp.GetTypeId()))
                except Exception:
                    pass
            if item.get("scale"):
                escalas[item["scale"]] = escalas.get(item["scale"], 0) + 1
            vistas.append(item)

        tabelas = []
        tabelas_pos = []
        for si in _col(doc, cls=DB.ScheduleSheetInstance, view_id=s.Id):
            try:
                if si.IsTitleblockRevisionSchedule:
                    continue
            except Exception:
                pass
            sch = doc.GetElement(si.ScheduleId)
            if sch is not None:
                tabelas.append(_nome(sch))
                try:
                    p = si.Point
                    tabelas_pos.append({"name": _nome(sch), "origin": [round((p.X - sx0) / larg, 3), round((p.Y - sy0) / alt, 3)]})
                except Exception:
                    tabelas_pos.append({"name": _nome(sch)})

        tipos = sorted(set(v["type"] for v in vistas))
        tipos_por_prancha[" + ".join(tipos) or "(vazia)"] = tipos_por_prancha.get(" + ".join(tipos) or "(vazia)", 0) + 1

        revs = []
        try:
            for rid in s.GetAllRevisionIds():
                r = doc.GetElement(rid)
                if r is not None:
                    revs.append(r.Description or str(r.SequenceNumber))
        except Exception:
            pass

        pranchas.append({
            "number": s.SheetNumber,
            "name": s.Name,
            "format": fmt,
            "titleblock": tb,
            "views": vistas,
            "schedules": tabelas,
            "schedule_items": tabelas_pos,
            "titleblock_params": _params_texto(next(iter(_col(doc, "OST_TitleBlocks", view_id=s.Id)), None)) if tb else {},
            "sheet_params": _params_texto(s),
            "revisions": revs,
            "current_revision": _param(s, DB.BuiltInParameter.SHEET_CURRENT_REVISION),
        })

    modelos_vista = sorted(set(_nome(v) for v in _col(doc, cls=DB.View) if v.IsTemplate))
    return {
        "file": doc.Title,
        "sheets_total": len(folhas),
        "sheets_listed": len(pranchas),
        "titleblocks": carimbos,
        "formats": formatos,
        "number_prefixes": prefixos,
        "scales": escalas,
        "sheet_compositions": tipos_por_prancha,
        "view_templates": modelos_vista,
        "sheets": pranchas,
    }
