# -*- coding: utf-8 -*-
"""Rotas /prumo/* (pyRevit Routes, só 127.0.0.1). Lista fechada; nesta versão, só leitura."""
import json
import logging

from pyrevit import routes

from . import VERSAO
from . import leitura
from .seguranca import negar

logger = logging.getLogger(__name__)


def _conserta(o):
    """O servidor de rotas do pyRevit lê o corpo como latin-1: UTF-8 chega como 'TÃRREO'. Desfaz isso."""
    if isinstance(o, dict):
        return dict((_conserta(k), _conserta(v)) for k, v in o.items())
    if isinstance(o, list):
        return [_conserta(x) for x in o]
    try:
        if any(ord(c) > 127 for c in o):
            return o.encode("latin-1").decode("utf-8")
    except Exception:
        pass
    return o


def _corpo(request):
    d = request.data
    if isinstance(d, dict):
        return _conserta(d)
    try:
        return _conserta(json.loads(d)) if d else {}
    except Exception:
        return {}


def _erro(e, status=500):
    logger.error("prumo: %s", e)
    return routes.make_response(data={"error": str(e)}, status=status)


def _doc(request):
    ref = _corpo(request).get("doc")
    d = leitura.resolver(ref)
    if d is None:
        raise LookupError("Modelo não está aberto no Revit: {}".format(ref or "(nenhum ativo)"))
    return d


def registrar(api):
    @api.route("/status/", methods=["GET"])
    def prumo_status(doc, request):
        n = negar(request)
        if n is not None:
            return n
        try:
            s = leitura.status()
            s["extensao"] = VERSAO
            return routes.make_response(data=s)
        except Exception as e:
            return _erro(e)

    @api.route("/documents/", methods=["GET"])
    def prumo_documentos(doc, request):
        n = negar(request)
        if n is not None:
            return n
        try:
            return routes.make_response(data={"documents": leitura.documentos()})
        except Exception as e:
            return _erro(e)

    @api.route("/summary/", methods=["POST"])
    def prumo_resumo(doc, request):
        n = negar(request)
        if n is not None:
            return n
        try:
            return routes.make_response(data=leitura.resumo(_doc(request)))
        except LookupError as e:
            return _erro(e, 404)
        except Exception as e:
            return _erro(e)

    @api.route("/apply/", methods=["POST"])
    def prumo_aplicar(doc, request):
        """Escrita: lista fechada de operações em transação única (ver escrita.py). 403 sem a flag de escrita."""
        n = negar(request)
        if n is not None:
            return n
        try:
            from . import escrita
            c = _corpo(request)
            return routes.make_response(data=escrita.aplicar(
                _doc(request), c.get("commands"), c.get("plan_id") or c.get("plano_id"),
                c.get("mode") or "dry_run", bool(c.get("confirm"))))
        except escrita.Recusa as e:
            return _erro(e, e.status)
        except LookupError as e:
            return _erro(e, 404)
        except ValueError as e:
            return _erro(e, 400)
        except Exception as e:
            return _erro(e)

    @api.route("/standard/", methods=["POST"])
    def prumo_padrao(doc, request):
        n = negar(request)
        if n is not None:
            return n
        try:
            limite = int(_corpo(request).get("limit") or 300)
            return routes.make_response(data=leitura.padrao(_doc(request), max(1, min(limite, 1000))))
        except LookupError as e:
            return _erro(e, 404)
        except Exception as e:
            return _erro(e)
