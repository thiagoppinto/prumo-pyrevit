# -*- coding: utf-8 -*-
"""Coleta para a conferência de padrão (SÓ LEITURA: nenhuma transação, nada é alterado).

As regras ficam no hub (src/core/conferencia.ts); aqui só se reúne o que elas precisam, de forma compacta.
Compatível com IronPython 2.7 e CPython 3 (sem f-strings).
"""
from pyrevit import DB

from .leitura import _col, _id, _mm, _nome, _param, _params_texto

LIMITE = 3000
M2 = 0.09290304
TIPOS_VISTA_IGNORADOS = ("ProjectBrowser", "SystemBrowser", "Internal", "Undefined", "Schedule", "ColumnSchedule", "PanelSchedule")


def _marcados(doc, categorias):
    """Ids dos elementos que têm pelo menos uma etiqueta (tag) em alguma vista."""
    ids = set()
    for cat in categorias:
        try:
            tags = list(_col(doc, cat))
        except Exception:
            continue
        for t in tags:
            try:
                for eid in t.GetTaggedLocalElementIds():
                    ids.add(_id(eid))
                continue
            except Exception:
                pass
            try:
                ids.add(_id(t.TaggedLocalElementId))
            except Exception:
                pass
    return ids


def _esquadrias(doc, categoria, categorias_tag):
    etiquetados = _marcados(doc, categorias_tag + ["OST_MultiCategoryTags"])
    itens = []
    for el in _col(doc, categoria):
        try:
            tipo = doc.GetElement(el.GetTypeId())
            nivel = doc.GetElement(el.LevelId) if _id(el.LevelId) > 0 else None
            itens.append({
                "id": _id(el.Id),
                "type": _nome(tipo) if tipo is not None else "",
                "family": getattr(tipo, "FamilyName", "") if tipo is not None else "",
                "mark": _param(el, DB.BuiltInParameter.ALL_MODEL_MARK) or "",
                "type_mark": (_param(tipo, DB.BuiltInParameter.ALL_MODEL_TYPE_MARK) if tipo is not None else "") or "",
                "level": _nome(nivel) if nivel is not None else "",
                "tagged": _id(el.Id) in etiquetados,
            })
        except Exception:
            continue
        if len(itens) >= LIMITE:
            break
    return itens


def _ambientes(doc):
    itens = []
    for r in _col(doc, "OST_Rooms"):
        try:
            itens.append({
                "id": _id(r.Id),
                "name": _param(r, DB.BuiltInParameter.ROOM_NAME) or "",
                "number": r.Number or "",
                "level": _nome(r.Level) if r.Level is not None else "",
                "area_m2": round((r.Area or 0) * M2, 2),
                "placed": r.Location is not None,
            })
        except Exception:
            continue
        if len(itens) >= LIMITE:
            break
    return itens


def _vistas(doc):
    colocadas = {}
    for vp in _col(doc, cls=DB.Viewport):
        try:
            colocadas[_id(vp.ViewId)] = doc.GetElement(vp.SheetId).SheetNumber
        except Exception:
            pass
    itens = []
    for v in _col(doc, cls=DB.View):
        try:
            if v.IsTemplate or isinstance(v, DB.ViewSheet):
                continue
            t = str(v.ViewType)
            if t in TIPOS_VISTA_IGNORADOS:
                continue
            d = {"id": _id(v.Id), "name": _nome(v), "type": t, "on_sheet": colocadas.get(_id(v.Id))}
            try:
                d["scale"] = "1:{}".format(v.Scale) if v.Scale else None
            except Exception:
                d["scale"] = None
            try:
                if v.GenLevel is not None:
                    d["level"] = _nome(v.GenLevel)
            except Exception:
                pass
            try:
                if _id(v.ViewTemplateId) > 0:
                    d["template"] = _nome(doc.GetElement(v.ViewTemplateId))
            except Exception:
                pass
            try:
                d["crop"] = bool(v.CropBoxActive)
            except Exception:
                pass
            itens.append(d)
        except Exception:
            continue
        if len(itens) >= LIMITE:
            break
    return itens


def _pranchas(doc):
    itens = []
    folhas = [s for s in _col(doc, cls=DB.ViewSheet) if not s.IsPlaceholder]
    folhas.sort(key=lambda s: s.SheetNumber)
    for s in folhas:
        try:
            tbs = list(_col(doc, "OST_TitleBlocks", view_id=s.Id))
            params = _params_texto(tbs[0]) if tbs else {}
            params.update(_params_texto(s))
            try:
                ext = s.Outline  # BoundingBoxUV da folha, em pés
                u0, v0, u1, v1 = ext.Min.U, ext.Min.V, ext.Max.U, ext.Max.V
            except Exception:
                u0 = v0 = u1 = v1 = None
            vps = []
            for vid in s.GetAllViewports():
                vp = doc.GetElement(vid)
                v = doc.GetElement(vp.ViewId)
                fora = False
                if u0 is not None:
                    try:
                        o = vp.GetBoxOutline()
                        tol = 0.01  # ~3 mm
                        fora = (o.MinimumPoint.X < u0 - tol or o.MinimumPoint.Y < v0 - tol
                                or o.MaximumPoint.X > u1 + tol or o.MaximumPoint.Y > v1 + tol)
                    except Exception:
                        fora = False
                sem_recorte = False
                try:
                    sem_recorte = isinstance(v, DB.ViewPlan) and not bool(v.CropBoxActive)
                except Exception:
                    pass
                vps.append({"view": _nome(v), "outside": fora, "no_crop": sem_recorte})
            itens.append({"number": s.SheetNumber, "name": s.Name, "titleblock": bool(tbs), "params": params, "viewports": vps})
        except Exception:
            continue
    return itens


def _revisoes(doc):
    out = []
    for r in _col(doc, cls=DB.Revision):
        try:
            out.append({"seq": r.SequenceNumber, "description": r.Description or "", "date": r.RevisionDate or "", "issued": bool(r.Issued)})
        except Exception:
            continue
    out.sort(key=lambda x: x["seq"])
    return out


def _modelo(doc):
    m = {"warnings": None, "warning_samples": [], "links_unloaded": [], "unused_types": None, "unused_samples": []}
    try:
        contagem = {}
        total = 0
        for w in doc.GetWarnings():
            total += 1
            txt = w.GetDescriptionText()
            contagem[txt] = contagem.get(txt, 0) + 1
        m["warnings"] = total
        m["warning_samples"] = [{"message": k[:160], "count": n} for k, n in sorted(contagem.items(), key=lambda kv: -kv[1])[:5]]
    except Exception:
        pass
    try:
        for t in _col(doc, cls=DB.RevitLinkType, tipos=True):
            try:
                if str(t.GetLinkedFileStatus()) != "Loaded":
                    m["links_unloaded"].append(_nome(t))
            except Exception:
                continue
    except Exception:
        pass
    try:
        usados = set()
        for fi in _col(doc, cls=DB.FamilyInstance):
            usados.add(_id(fi.GetTypeId()))
        n = 0
        amostra = []
        for s in _col(doc, cls=DB.FamilySymbol, tipos=True):
            try:
                if s.Category is None or s.Category.CategoryType != DB.CategoryType.Model:
                    continue
                if _id(s.Id) in usados:
                    continue
                n += 1
                if len(amostra) < 15:
                    amostra.append("{}: {}".format(getattr(s, "FamilyName", ""), _nome(s)))
            except Exception:
                continue
        m["unused_types"] = n
        m["unused_samples"] = amostra
    except Exception:
        pass
    return m


def auditoria(doc):
    niveis = sorted([{"name": _nome(l), "elevation_mm": _mm(l.Elevation)} for l in _col(doc, cls=DB.Level)], key=lambda x: x["elevation_mm"])
    d = {
        "file": doc.Title,
        "levels": niveis,
        "rooms": _ambientes(doc),
        "doors": _esquadrias(doc, "OST_Doors", ["OST_DoorTags"]),
        "windows": _esquadrias(doc, "OST_Windows", ["OST_WindowTags"]),
        "views": _vistas(doc),
        "sheets": _pranchas(doc),
        "revisions": _revisoes(doc),
        "model": _modelo(doc),
        "limit": LIMITE,
    }
    return d
