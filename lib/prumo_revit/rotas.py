# -*- coding: utf-8 -*-
"""Rotas /prumo/* (pyRevit Routes, só 127.0.0.1). Lista fechada; nesta versão, só leitura."""
import json
import logging

from pyrevit import routes

from . import VERSAO
from . import leitura
from .seguranca import negar

logger = logging.getLogger(__name__)


def _corpo(request):
    d = request.data
    if isinstance(d, dict):
        return d
    try:
        return json.loads(d) if d else {}
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
