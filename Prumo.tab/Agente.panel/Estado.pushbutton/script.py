# -*- coding: utf-8 -*-
"""Botão informativo do Prumo (só leitura): mostra a versão da extensão e se o agente já foi configurado.

Existe também para que o pyRevit trate a pasta como extensão com interface e execute o startup.py (rotas /prumo/*).
"""
import os

from pyrevit import forms

from prumo_revit import VERSAO

token = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Prumo", "local_token")
forms.alert(
    "Extensão Prumo {}\nRotas locais: http://127.0.0.1:48884/prumo/ (só leitura)\nAgente configurado neste computador: {}".format(
        VERSAO, "sim" if os.path.exists(token) else "não"),
    title="Prumo")
