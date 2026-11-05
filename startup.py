# -*- coding: utf-8 -*-
"""Prumo: registra as rotas /prumo/* no pyRevit Routes (só 127.0.0.1).

O agente Prumo (processo separado, revit-agent/agente) chama estas rotas e conversa com o
Prumo Hub por WebSocket de saída. Aqui só existe uma lista fechada de operações: nada de
executar código vindo de fora.

Diagnóstico: o resultado do carregamento fica em %LOCALAPPDATA%\Prumo\startup.log
(o pyRevit não mostra erros de startup.py em lugar nenhum).
"""
import os
import sys
import traceback


def _log(texto):
    try:
        pasta = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Prumo")
        if not os.path.isdir(pasta):
            os.makedirs(pasta)
        with open(os.path.join(pasta, "startup.log"), "w") as f:
            f.write(texto)
    except Exception:
        pass


try:
    from pyrevit import routes
    from prumo_revit.rotas import registrar

    api = routes.API("prumo")
    registrar(api)
    _log("ok: rotas /prumo/* registradas (python {})".format(sys.version.split()[0]))
except Exception:
    _log("ERRO ao registrar rotas:\n" + traceback.format_exc())
    raise
