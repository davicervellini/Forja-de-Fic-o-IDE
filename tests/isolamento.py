"""
Isola os testes da configuração real do usuário: sem isso, um config.json com provedores na
nuvem faria os testes pedirem chave de API ou chamarem a internet.
"""

from unittest.mock import patch

from pipeline import config


def local_only():
    """Todas as fases no Ollama, sem reserva local, sem premissa automática e com a interface em inglês."""
    return patch.multiple(
        config,
        PROVIDER_DRAFTING="ollama", PROVIDER_REFINING="ollama", PROVIDER_SUMMARIZING="ollama",
        CLOUD_FALLBACK=False, AUTO_NEXT_PREMISE=False, UI_LANGUAGE="en",
    )


def basic_pipeline():
    """
    Pipeline sem as etapas extras de qualidade (versões por cena, juiz, revisão com citação,
    reescrita da cena que falha). Os testes antigos contam chamadas ao modelo e trocam o modelo por
    stubs que só conhecem as fases básicas; os testes dessas etapas as ligam explicitamente.
    """
    return patch.multiple(
        config,
        DRAFT_CANDIDATES=1, REFINE_CANDIDATES=1, QA_SCENE_RETRIES=0,
        QA_JUDGE_ENABLED=False, REVISE_PASS_ENABLED=False,
        REFINE_MIN_RATIO=0.7, REFINE_MAX_RATIO=1.25, PROMPT_BUDGET_TOKENS=100000,
        # Mínimo do capítulo baixo: os stubs escrevem capítulos curtos de propósito.
        CHAPTER_TARGET_WORDS=600,
    )

