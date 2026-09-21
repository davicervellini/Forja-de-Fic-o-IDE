"""
project.py — Gerenciamento de projetos de ficção.

Cada história vive em sua própria pasta dentro de `projetos/`.
Este módulo cuida de criar, listar, carregar e salvar o estado
de um projeto, garantindo persistência total entre sessões.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pipeline.io_utils import read_file, write_file

logger = logging.getLogger(__name__)


@dataclass
class ChapterEntry:
    """Representação de um capítulo já processado no disco."""
    num: int
    premise: str = ""
    draft: str = ""
    final: str = ""
    summary: str = ""
    status: str = "pending"  # pending, drafting, polishing, summarizing, done, error


class StoryProject:
    """
    Representa um projeto de ficção no disco.

    Estrutura:
        projetos/<slug>/
            projeto.json
            registro_akashico.md
            registro_modelo.md
            memoria_dinamica.md
            historia_ate_agora.md
            estado.json
            capitulos/
                capitulo_01/
                    premissa.md
                    rascunho.md
                    capitulo_final.md
                    resumo.md
                ...
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.chapters_dir = self.project_dir / "capitulos"
        self.metadata: dict = {}

        # Estado do pipeline (persistido em estado.json)
        self.accumulated_summaries: list[tuple[int, str]] = []
        self.story_so_far: str = ""
        self.dynamic_memory: str = ""
        self.last_chapter_num: int = 0

    # ── Paths derivados ──────────────────────────────────────

    @property
    def metadata_path(self) -> Path:
        return self.project_dir / "projeto.json"

    @property
    def akashic_path(self) -> Path:
        return self.project_dir / "registro_akashico.md"

    @property
    def akashic_model_path(self) -> Path:
        return self.project_dir / "registro_modelo.md"

    @property
    def dynamic_memory_path(self) -> Path:
        return self.project_dir / "memoria_dinamica.md"

    @property
    def story_so_far_path(self) -> Path:
        return self.project_dir / "historia_ate_agora.md"

    @property
    def state_path(self) -> Path:
        return self.project_dir / "estado.json"

    @property
    def name(self) -> str:
        return self.metadata.get("name", self.project_dir.name)

    def chapter_dir(self, chapter_num: int) -> Path:
        return self.chapters_dir / f"capitulo_{chapter_num:02d}"

    # ── Criação ──────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        projects_root: Path,
        name: str,
        akashic_source: Path | None = None,
    ) -> "StoryProject":
        """Cria um novo projeto com a estrutura de pastas."""
        slug = cls._slugify(name)
        project_dir = Path(projects_root) / slug

        if project_dir.exists():
            raise FileExistsError(f"Projeto já existe: {project_dir}")

        project_dir.mkdir(parents=True)
        (project_dir / "capitulos").mkdir()

        proj = cls(project_dir)

        # Metadados
        proj.metadata = {
            "name": name,
            "slug": slug,
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "last_chapter": 0,
        }
        proj._save_metadata()

        # Copia Registro Akáshico se fornecido
        if akashic_source and akashic_source.exists():
            content = read_file(akashic_source)
            write_file(proj.akashic_path, content)

        # Cria estado inicial
        proj.save_state()

        logger.info(f"Projeto criado: {name} em {project_dir}")
        return proj

    # ── Carregamento ─────────────────────────────────────────

    @classmethod
    def load(cls, project_dir: Path) -> "StoryProject":
        """Carrega um projeto existente do disco, reidratando todo o estado."""
        proj = cls(project_dir)

        # Carrega metadados
        if proj.metadata_path.exists():
            proj.metadata = json.loads(read_file(proj.metadata_path))

        # Carrega estado persistente
        if proj.state_path.exists():
            state = json.loads(read_file(proj.state_path))
            proj.accumulated_summaries = [
                (int(s[0]), s[1]) for s in state.get("accumulated_summaries", [])
            ]
            proj.story_so_far = state.get("story_so_far", "")
            proj.dynamic_memory = state.get("dynamic_memory", "")
            proj.last_chapter_num = state.get("last_chapter_num", 0)
        else:
            # Fallback: carrega dos arquivos markdown
            if proj.dynamic_memory_path.exists():
                proj.dynamic_memory = read_file(proj.dynamic_memory_path)
            if proj.story_so_far_path.exists():
                proj.story_so_far = read_file(proj.story_so_far_path)

        logger.info(
            f"Projeto carregado: {proj.name} "
            f"(último cap: {proj.last_chapter_num}, "
            f"resumos em memória: {len(proj.accumulated_summaries)})"
        )
        return proj

    # ── Salvamento de estado ─────────────────────────────────

    def save_state(self):
        """Persiste o estado completo do pipeline no disco."""
        state = {
            "last_chapter_num": self.last_chapter_num,
            "accumulated_summaries": [
                [num, text] for num, text in self.accumulated_summaries
            ],
            "story_so_far": self.story_so_far,
            "dynamic_memory": self.dynamic_memory,
        }
        write_file(self.state_path, json.dumps(state, ensure_ascii=False, indent=2))

        # Também salva os arquivos markdown individuais (legibilidade)
        write_file(self.dynamic_memory_path, self.dynamic_memory)
        write_file(self.story_so_far_path, self.story_so_far)

        # Atualiza metadados
        self.metadata["last_modified"] = datetime.now().isoformat()
        self.metadata["last_chapter"] = self.last_chapter_num
        self._save_metadata()

    def _save_metadata(self):
        write_file(
            self.metadata_path,
            json.dumps(self.metadata, ensure_ascii=False, indent=2),
        )

    # ── Leitura dos capítulos existentes ─────────────────────

    def scan_chapters(self) -> list[ChapterEntry]:
        """Escaneia a pasta de capítulos e reconstrói a lista completa."""
        entries: list[ChapterEntry] = []

        if not self.chapters_dir.exists():
            return entries

        # Lista as pastas capitulo_XX ordenadas
        chapter_dirs = sorted(
            d for d in self.chapters_dir.iterdir()
            if d.is_dir() and d.name.startswith("capitulo_")
        )

        for ch_dir in chapter_dirs:
            try:
                num = int(ch_dir.name.split("_")[1])
            except (IndexError, ValueError):
                continue

            entry = ChapterEntry(num=num)

            # Lê cada arquivo se existir
            premissa_path = ch_dir / "premissa.md"
            if premissa_path.exists():
                entry.premise = read_file(premissa_path)

            rascunho_path = ch_dir / "rascunho.md"
            if rascunho_path.exists():
                entry.draft = read_file(rascunho_path)

            final_path = ch_dir / "capitulo_final.md"
            if final_path.exists():
                entry.final = read_file(final_path)

            resumo_path = ch_dir / "resumo.md"
            if resumo_path.exists():
                entry.summary = read_file(resumo_path)

            # Determina status
            if entry.final and entry.summary:
                entry.status = "done"
            elif entry.draft:
                entry.status = "drafting"
            elif entry.premise:
                entry.status = "pending"

            entries.append(entry)

        return entries

    def load_akashic_model(self) -> str:
        """Carrega o Registro Akáshico compilado (versão curta para o LLM)."""
        if self.akashic_model_path.exists():
            return read_file(self.akashic_model_path)
        if self.akashic_path.exists():
            return read_file(self.akashic_path)
        return ""

    # ── Listagem de projetos ─────────────────────────────────

    @staticmethod
    def list_projects(projects_root: Path) -> list[dict]:
        """Lista todos os projetos existentes com metadados básicos."""
        projects_root = Path(projects_root)
        if not projects_root.exists():
            return []

        results = []
        for d in sorted(projects_root.iterdir()):
            meta_path = d / "projeto.json"
            if d.is_dir() and meta_path.exists():
                try:
                    meta = json.loads(read_file(meta_path))
                    results.append({
                        "path": d,
                        "name": meta.get("name", d.name),
                        "last_chapter": meta.get("last_chapter", 0),
                        "last_modified": meta.get("last_modified", ""),
                        "created": meta.get("created", ""),
                    })
                except Exception:
                    results.append({
                        "path": d,
                        "name": d.name,
                        "last_chapter": 0,
                        "last_modified": "",
                        "created": "",
                    })

        return results

    @staticmethod
    def delete_project(project_dir: Path):
        """Remove um projeto inteiro do disco."""
        import shutil
        project_dir = Path(project_dir)
        if project_dir.exists():
            shutil.rmtree(project_dir)
            logger.info(f"Projeto removido: {project_dir}")

    # ── Utilitários ──────────────────────────────────────────

    @staticmethod
    def _slugify(name: str) -> str:
        """Converte nome em slug válido para nome de pasta."""
        import re
        slug = name.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s-]+", "_", slug)
        return slug or "projeto"
