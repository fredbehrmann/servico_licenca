# -*- coding: utf-8 -*-
"""Limite de requisições por instalação (§ pergunta 5).

Teto generoso por ``instalacao_id`` — o app usa cache e reconsulta esporadicamente,
então algumas consultas por hora bastam para uso legítimo e barram laço/abuso.

É um contador em memória, por processo. Na Railway com mais de uma instância o
teto é por instância; para o estágio de homologação é suficiente. Um limite
global exigiria estado compartilhado (ex.: Redis), que fica para quando o volume
justificar.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict


class LimitadorMemoria:
    def __init__(self, maximo: int = 12, janela_s: int = 3600) -> None:
        self._maximo = max(1, int(maximo))
        self._janela = max(1, int(janela_s))
        self._hist: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def permitido(self, chave: str, agora: float | None = None) -> bool:
        agora = time.monotonic() if agora is None else agora
        with self._lock:
            fila = self._hist.setdefault(chave, deque())
            corte = agora - self._janela
            while fila and fila[0] < corte:
                fila.popleft()
            if len(fila) >= self._maximo:
                return False
            fila.append(agora)
            return True
