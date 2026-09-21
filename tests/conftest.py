"""Configuracao compartilhada do pytest.

O idioma das mensagens do backend e estado GLOBAL do modulo core (core.tr usa
core._language). Qualquer teste que crie a Api real le o options.json do projeto e
troca esse idioma — e ai os testes seguintes passavam a receber mensagens em ingles
(ou no que o desenvolvedor tivesse escolhido no app), falhando ao procurar texto
em portugues. O resultado da suite dependia da preferencia de quem roda.

Este fixture devolve o idioma de origem ANTES de cada teste, para nenhum teste
herdar o estado de outro.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend import core  # noqa: E402


@pytest.fixture(autouse=True)
def _idioma_de_origem():
    core.set_language(core.SOURCE_LANGUAGE)
    yield
    core.set_language(core.SOURCE_LANGUAGE)
